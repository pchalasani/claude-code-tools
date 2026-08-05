"""Follow one question queue and wake its owning agent session."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from visual_brief.server.queue import MAX_QUESTION_LENGTH, MAX_QUEUE_RECORD_BYTES
from visual_brief.writes.runfiles import (
    CliError,
    run_file,
    run_output_file,
    write_text_atomic,
)

CODEX_ENDPOINT_ENV = "CCTOOLS_CODEX_CALLBACK_ENDPOINT"
CODEX_THREAD_ENV = "CODEX_THREAD_ID"
POLL_INTERVAL_SECONDS = 0.1
CODEX_CURSOR_FILE = ".codex-question-cursor.json"
MAX_CODEX_DELIVERY_ATTEMPTS = 5
QUEUE_BOUNDARY_BYTES = 512


class _Digest(Protocol):
    """Small typed surface shared by hashlib digest objects."""

    def update(self, data: bytes) -> None:
        """Add bytes to the digest."""

    def hexdigest(self) -> str:
        """Return the current hexadecimal digest without consuming it."""


@dataclass(frozen=True)
class _QueueBoundary:
    """Bounded evidence that bytes before the read position are unchanged."""

    head: bytes
    offset: int
    tail: bytes


@dataclass(frozen=True)
class _QueueCursor:
    """Durable position immediately after one complete queue record."""

    device: int
    inode: int
    offset: int
    prefix_sha256: str | None = None


@dataclass(frozen=True)
class _PendingDelivery:
    """Question whose App Server acceptance may have preceded a crash."""

    attempts: int
    cursor: _QueueCursor
    endpoint: str | None
    message_id: str
    text: str
    thread_id: str | None


@dataclass(frozen=True)
class _CodexQueueState:
    """Acknowledged position plus any conservatively ambiguous delivery."""

    cursor: _QueueCursor
    pending: _PendingDelivery | None = None


class _CodexHelperError(CliError):
    """A helper failure with an optional confirmed submission count."""

    def __init__(self, message: str, attempts: int | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts


def follow_questions(
    queue_path: Path,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> Iterator[dict[str, Any]]:
    """Yield complete valid questions appended after the follower starts.

    Args:
        queue_path: Queue file to follow.
        poll_interval: Delay between checks while the file is unchanged.

    Returns:
        An iterator of valid question records in append order.

    Raises:
        CliError: If the queue cannot be opened.
    """
    try:
        queue = queue_path.open("rb")
        queue.seek(0, os.SEEK_END)
    except OSError as error:
        raise CliError(f"cannot watch {queue_path}: {error}") from error
    positioned = _follow_open_queue(
        queue_path,
        queue,
        poll_interval,
        boundary=_queue_boundary(queue),
    )
    return _records_only(positioned)


def _records_only(
    positioned: Iterator[tuple[dict[str, Any], _QueueCursor]],
) -> Iterator[dict[str, Any]]:
    """Strip internal queue positions from public follower records."""
    for record, _ in positioned:
        yield record


def _follow_open_queue(
    queue_path: Path,
    queue: BinaryIO,
    poll_interval: float,
    prefix_digest: _Digest | None = None,
    boundary: _QueueBoundary | None = None,
) -> Iterator[tuple[dict[str, Any], _QueueCursor]]:
    """Yield accepted questions and their post-record queue positions."""
    try:
        pending = b""
        discarding = False
        boundary = boundary or _queue_boundary(queue)
        while True:
            if not _queue_boundary_matches(queue, boundary):
                queue.seek(0)
                pending = b""
                discarding = False
                boundary = _queue_boundary(queue)
                if prefix_digest is not None:
                    prefix_digest = hashlib.sha256()
            chunk = queue.readline(MAX_QUEUE_RECORD_BYTES + 1)
            if chunk:
                boundary = _queue_boundary(queue)
                if prefix_digest is not None:
                    prefix_digest.update(chunk)
                if discarding:
                    if chunk.endswith(b"\n"):
                        discarding = False
                    continue
                if len(pending) + len(chunk) > MAX_QUEUE_RECORD_BYTES:
                    pending = b""
                    discarding = not chunk.endswith(b"\n")
                    continue
                pending += chunk
                if pending.endswith(b"\n"):
                    record = _accepted_question(pending)
                    pending = b""
                    if record is not None:
                        digest = (
                            prefix_digest.hexdigest()
                            if prefix_digest is not None
                            else None
                        )
                        yield record, _queue_cursor(queue, digest)
                continue
            replacement = _replacement(queue_path, queue.fileno())
            if replacement is not None:
                queue.close()
                queue = replacement
                pending = b""
                discarding = False
                boundary = _queue_boundary(queue)
                if prefix_digest is not None:
                    prefix_digest = hashlib.sha256()
                continue
            if _was_truncated(queue_path, queue):
                queue.seek(0)
                pending = b""
                discarding = False
                boundary = _queue_boundary(queue)
                if prefix_digest is not None:
                    prefix_digest = hashlib.sha256()
                continue
            time.sleep(poll_interval)
    finally:
        queue.close()


def watch_command(
    run_id: str,
    run_dir: Path,
    agent: str,
    thread_id: str | None,
    endpoint: str | None,
) -> int:
    """Follow a run queue and deliver each accepted record.

    Args:
        run_id: Selected Visual Brief run id.
        run_dir: Selected run directory.
        agent: Agent adapter name.
        thread_id: Optional explicit Codex thread id.
        endpoint: Optional explicit Codex app-server endpoint.

    Returns:
        The process exit status.
    """
    queue_path = run_file(run_dir, "questions.jsonl")
    if agent == "claude":
        for record in follow_questions(queue_path):
            print(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
        return 0

    selected_thread, selected_endpoint = resolve_codex_target(
        thread_id,
        endpoint,
    )
    records, cursor_path, saved = _follow_codex_questions(run_dir, queue_path)
    instance_id = _run_instance_id(run_dir)
    helper = _codex_helper_path()
    pending = None
    if saved.pending is not None:
        pending = _bind_pending_target(
            cursor_path,
            saved,
            selected_thread,
            selected_endpoint,
        )
    _invoke_codex_helper(
        helper,
        "check",
        run_id,
        instance_id,
        selected_thread,
        selected_endpoint,
    )
    acknowledged = saved.cursor
    if pending is not None:
        reserved_attempts = min(
            pending.attempts + 1,
            MAX_CODEX_DELIVERY_ATTEMPTS,
        )
        attempting = _PendingDelivery(
            attempts=reserved_attempts,
            cursor=pending.cursor,
            endpoint=pending.endpoint,
            message_id=pending.message_id,
            text=pending.text,
            thread_id=pending.thread_id,
        )
        _write_codex_state(
            cursor_path,
            _CodexQueueState(acknowledged, attempting),
        )
        try:
            _invoke_codex_helper(
                helper,
                "deliver",
                run_id,
                instance_id,
                selected_thread,
                selected_endpoint,
                message_id=pending.message_id,
                text=pending.text,
                initial_attempts=pending.attempts,
                maximum_attempts=reserved_attempts,
            )
        except _CodexHelperError as error:
            _restore_unused_reservation(
                cursor_path,
                acknowledged,
                pending,
                error,
            )
            raise
        acknowledged = pending.cursor
        _write_codex_state(cursor_path, _CodexQueueState(acknowledged))
    for record, cursor in records:
        message_id = record.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            print(
                "warning: skipped legacy question without message_id",
                file=sys.stderr,
                flush=True,
            )
            acknowledged = cursor
            _write_codex_state(cursor_path, _CodexQueueState(acknowledged))
            continue
        text = record["text"]
        pending = _PendingDelivery(
            attempts=0,
            cursor=cursor,
            endpoint=selected_endpoint,
            message_id=message_id,
            text=text,
            thread_id=selected_thread,
        )
        attempting = _PendingDelivery(
            attempts=1,
            cursor=cursor,
            endpoint=selected_endpoint,
            message_id=message_id,
            text=text,
            thread_id=selected_thread,
        )
        _write_codex_state(
            cursor_path,
            _CodexQueueState(acknowledged, attempting),
        )
        try:
            _invoke_codex_helper(
                helper,
                "deliver",
                run_id,
                instance_id,
                selected_thread,
                selected_endpoint,
                message_id=message_id,
                text=text,
                maximum_attempts=1,
            )
        except _CodexHelperError as error:
            _restore_unused_reservation(
                cursor_path,
                acknowledged,
                pending,
                error,
            )
            raise
        acknowledged = cursor
        _write_codex_state(cursor_path, _CodexQueueState(acknowledged))
    return 0


def _follow_codex_questions(
    run_dir: Path,
    queue_path: Path,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> tuple[
    Iterator[tuple[dict[str, Any], _QueueCursor]],
    Path,
    _CodexQueueState,
]:
    """Open a Codex queue at its durable position before target checks."""
    cursor_path = run_output_file(run_dir, CODEX_CURSOR_FILE)
    saved = _read_codex_state(cursor_path)
    try:
        queue = queue_path.open("rb")
    except OSError as error:
        raise CliError(f"cannot watch {queue_path}: {error}") from error
    try:
        opened = os.fstat(queue.fileno())
        if saved is None:
            queue.seek(0, os.SEEK_END)
            prefix_digest = _prefix_digest(queue.fileno(), queue.tell())
            starting = _queue_cursor(queue, prefix_digest.hexdigest())
            saved = _CodexQueueState(starting)
            _write_codex_state(cursor_path, saved)
        else:
            resume = saved.pending.cursor if saved.pending else saved.cursor
            if _cursor_matches_open_queue(resume, opened, queue.fileno()):
                queue.seek(resume.offset)
            else:
                queue.seek(0)
            prefix_digest = _prefix_digest(queue.fileno(), queue.tell())
    except OSError as error:
        queue.close()
        raise CliError(f"cannot watch {queue_path}: {error}") from error
    except BaseException:
        queue.close()
        raise
    records = _follow_open_queue(
        queue_path,
        queue,
        poll_interval,
        prefix_digest,
        _queue_boundary(queue),
    )
    return records, cursor_path, saved


def _read_codex_state(cursor_path: Path) -> _CodexQueueState | None:
    """Read and strictly validate durable Codex delivery state."""
    try:
        value = json.loads(cursor_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CliError(f"cannot read Codex queue cursor: {error}") from error
    except json.JSONDecodeError as error:
        raise CliError(f"malformed Codex queue cursor: {error}") from error
    if not isinstance(value, dict):
        raise CliError("malformed Codex queue cursor: expected an object")
    cursor = _parse_cursor(value)
    pending_value = value.get("pending")
    if pending_value is None:
        return _CodexQueueState(cursor)
    if not isinstance(pending_value, dict):
        raise CliError("malformed Codex queue cursor: pending must be an object")
    message_id = pending_value.get("message_id")
    text = pending_value.get("text")
    ambiguous = pending_value.get("ambiguous")
    attempts = pending_value.get("attempts", 1)
    endpoint = pending_value.get("endpoint")
    thread_id = pending_value.get("thread_id")
    cursor_value = pending_value.get("cursor")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(text, str)
        or ambiguous is not True
        or type(attempts) is not int
        or not 0 <= attempts <= MAX_CODEX_DELIVERY_ATTEMPTS
        or (endpoint is not None and not isinstance(endpoint, str))
        or (thread_id is not None and not isinstance(thread_id, str))
        or not isinstance(cursor_value, dict)
    ):
        raise CliError("malformed Codex queue cursor: invalid pending delivery")
    pending = _PendingDelivery(
        attempts=attempts,
        cursor=_parse_cursor(cursor_value),
        endpoint=endpoint,
        message_id=message_id,
        text=text,
        thread_id=thread_id,
    )
    return _CodexQueueState(cursor, pending)


def _parse_cursor(value: dict[str, Any]) -> _QueueCursor:
    """Parse one queue cursor object from durable state."""
    fields = (value.get("device"), value.get("inode"), value.get("offset"))
    if any(type(field) is not int or field < 0 for field in fields):
        raise CliError(
            "malformed Codex queue cursor: device, inode, and offset "
            "must be non-negative integers"
        )
    prefix_sha256 = value.get("prefix_sha256")
    if prefix_sha256 is not None and (
        not isinstance(prefix_sha256, str)
        or len(prefix_sha256) != 64
        or any(character not in "0123456789abcdef" for character in prefix_sha256)
    ):
        raise CliError(
            "malformed Codex queue cursor: prefix_sha256 must be a SHA-256 digest"
        )
    device, inode, offset = fields
    return _QueueCursor(
        device=device,
        inode=inode,
        offset=offset,
        prefix_sha256=prefix_sha256,
    )


def _write_codex_state(cursor_path: Path, state: _CodexQueueState) -> None:
    """Atomically persist acknowledged and in-flight Codex queue state."""
    payload = _cursor_payload(state.cursor)
    if state.pending is not None:
        payload["pending"] = {
            "ambiguous": True,
            "attempts": state.pending.attempts,
            "cursor": _cursor_payload(state.pending.cursor),
            "endpoint": state.pending.endpoint,
            "message_id": state.pending.message_id,
            "text": state.pending.text,
            "thread_id": state.pending.thread_id,
        }
    try:
        write_text_atomic(
            cursor_path,
            json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        )
    except OSError as error:
        raise CliError(f"cannot write Codex queue cursor: {error}") from error


def _bind_pending_target(
    cursor_path: Path,
    state: _CodexQueueState,
    thread_id: str,
    endpoint: str,
) -> _PendingDelivery:
    """Bind legacy pending state and reject a target change mid-delivery."""
    pending = state.pending
    if pending is None:
        raise AssertionError("pending target binding requires a pending delivery")
    if pending.thread_id is not None and pending.thread_id != thread_id:
        raise CliError(
            "a Visual Brief question is still pending for Codex thread "
            f"{pending.thread_id}; resume that thread before changing targets"
        )
    if pending.endpoint is not None and pending.endpoint != endpoint:
        raise CliError(
            "a Visual Brief question is still pending on a different Codex "
            "app-server endpoint; resume through the original codex-dynamic "
            "session"
        )
    if pending.thread_id is not None and pending.endpoint is not None:
        return pending
    bound = _PendingDelivery(
        attempts=pending.attempts,
        cursor=pending.cursor,
        endpoint=endpoint,
        message_id=pending.message_id,
        text=pending.text,
        thread_id=thread_id,
    )
    _write_codex_state(cursor_path, _CodexQueueState(state.cursor, bound))
    return bound


def _restore_unused_reservation(
    cursor_path: Path,
    acknowledged: _QueueCursor,
    pending: _PendingDelivery,
    error: _CodexHelperError,
) -> None:
    """Reclaim a reserved attempt when the helper confirms no submission."""
    if error.attempts == pending.attempts:
        _write_codex_state(
            cursor_path,
            _CodexQueueState(acknowledged, pending),
        )


def _cursor_payload(cursor: _QueueCursor) -> dict[str, Any]:
    """Return the JSON object for one queue cursor."""
    return {
        "device": cursor.device,
        "inode": cursor.inode,
        "offset": cursor.offset,
        "prefix_sha256": cursor.prefix_sha256,
    }


def _queue_cursor(
    queue: BinaryIO,
    prefix_sha256: str | None = None,
) -> _QueueCursor:
    """Capture the current queue identity and byte position."""
    try:
        opened = os.fstat(queue.fileno())
        offset = queue.tell()
    except OSError as error:
        raise CliError(f"cannot read queue position: {error}") from error
    return _QueueCursor(
        device=opened.st_dev,
        inode=opened.st_ino,
        offset=offset,
        prefix_sha256=prefix_sha256,
    )


def _queue_boundary(queue: BinaryIO) -> _QueueBoundary:
    """Capture bounded bytes at both ends of the consumed queue prefix."""
    try:
        offset = queue.tell()
        width = min(QUEUE_BOUNDARY_BYTES, offset)
        head = os.pread(queue.fileno(), width, 0)
        tail = os.pread(queue.fileno(), width, offset - width)
    except OSError as error:
        raise CliError(f"cannot inspect queue boundary: {error}") from error
    return _QueueBoundary(head=head, offset=offset, tail=tail)


def _queue_boundary_matches(queue: BinaryIO, expected: _QueueBoundary) -> bool:
    """Return whether a live queue still carries its consumed boundary bytes."""
    try:
        return (
            os.pread(queue.fileno(), len(expected.head), 0) == expected.head
            and os.pread(
                queue.fileno(),
                len(expected.tail),
                expected.offset - len(expected.tail),
            )
            == expected.tail
        )
    except OSError as error:
        raise CliError(f"cannot verify queue boundary: {error}") from error


def _same_queue(cursor: _QueueCursor, opened: os.stat_result) -> bool:
    """Return whether a saved cursor belongs to the opened queue."""
    return (cursor.device, cursor.inode) == (opened.st_dev, opened.st_ino)


def _cursor_matches_open_queue(
    cursor: _QueueCursor,
    opened: os.stat_result,
    descriptor: int,
) -> bool:
    """Validate identity, length, and acknowledged bytes before resuming."""
    return (
        cursor.prefix_sha256 is not None
        and _same_queue(cursor, opened)
        and opened.st_size >= cursor.offset
        and _prefix_sha256(descriptor, cursor.offset) == cursor.prefix_sha256
    )


def _prefix_sha256(descriptor: int, length: int) -> str:
    """Hash a queue prefix without changing the descriptor's read position."""
    return _prefix_digest(descriptor, length).hexdigest()


def _prefix_digest(descriptor: int, length: int) -> _Digest:
    """Build an incremental digest for a queue prefix."""
    digest = hashlib.sha256()
    offset = 0
    while offset < length:
        try:
            chunk = os.pread(descriptor, min(64 * 1024, length - offset), offset)
        except OSError as error:
            raise CliError(f"cannot verify queue prefix: {error}") from error
        if not chunk:
            raise CliError("cannot verify queue prefix: queue became shorter")
        digest.update(chunk)
        offset += len(chunk)
    return digest


def resolve_codex_target(
    thread_id: str | None,
    endpoint: str | None,
) -> tuple[str, str]:
    """Resolve and validate the explicit or environment Codex target.

    Args:
        thread_id: Explicit target thread, if supplied.
        endpoint: Explicit app-server endpoint, if supplied.

    Returns:
        The selected thread id and endpoint.

    Raises:
        CliError: If codex-dynamic did not provide a usable local target.
    """
    selected_thread = thread_id or os.environ.get(CODEX_THREAD_ENV)
    selected_endpoint = endpoint or os.environ.get(CODEX_ENDPOINT_ENV)
    if not selected_thread or not selected_endpoint:
        raise CliError(
            "Codex watching requires CODEX_THREAD_ID and "
            "CCTOOLS_CODEX_CALLBACK_ENDPOINT; restart or resume through "
            "codex-dynamic"
        )
    if not selected_endpoint.startswith("unix://"):
        raise CliError(
            "Codex watching supports only local unix:// app-server endpoints; "
            "restart or resume through codex-dynamic"
        )
    return selected_thread, selected_endpoint


def _accepted_question(line: bytes) -> dict[str, Any] | None:
    """Parse one complete queue line without changing its text."""
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("type") != "question":
        return None
    anchor = value.get("anchor_id")
    text = value.get("text")
    parent = value.get("parent_id")
    message_id = value.get("message_id")
    if not isinstance(anchor, str) or not isinstance(text, str):
        return None
    if not text.strip() or len(text) > MAX_QUESTION_LENGTH:
        return None
    if parent is not None and not isinstance(parent, str):
        return None
    if message_id is not None and not isinstance(message_id, str):
        return None
    return value


def _replacement(queue_path: Path, descriptor: int) -> BinaryIO | None:
    """Open a replaced queue at its beginning, or return none."""
    try:
        current = queue_path.stat()
        opened = os.fstat(descriptor)
    except OSError:
        return None
    if (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino):
        return None
    try:
        return queue_path.open("rb")
    except OSError:
        return None


def _was_truncated(queue_path: Path, queue: BinaryIO) -> bool:
    """Return whether an open queue became shorter than its read position."""
    try:
        current = queue_path.stat()
        opened = os.fstat(queue.fileno())
    except OSError:
        return False
    same_file = (current.st_dev, current.st_ino) == (
        opened.st_dev,
        opened.st_ino,
    )
    return same_file and current.st_size < queue.tell()


def _run_instance_id(run_dir: Path) -> str:
    """Read the collision-resistant identity of one run instance."""
    try:
        metadata = json.loads(run_file(run_dir, "meta.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(f"cannot read run identity: {error}") from error
    instance_id = None
    if isinstance(metadata, dict):
        instance_id = metadata.get("instance_id", metadata.get("created_at"))
    if not isinstance(instance_id, str) or not instance_id:
        raise CliError("run metadata has no stable identity; recreate the run")
    return instance_id


def _codex_helper_path() -> Path:
    """Return the committed package-data helper path."""
    return Path(__file__).with_name("static") / "visual-brief-codex.mjs"


def _invoke_codex_helper(
    helper: Path,
    command: str,
    run_id: str,
    instance_id: str,
    thread_id: str,
    endpoint: str,
    *,
    message_id: str | None = None,
    text: str | None = None,
    initial_attempts: int = 0,
    maximum_attempts: int = MAX_CODEX_DELIVERY_ATTEMPTS,
) -> None:
    """Invoke the bundled helper with arguments and optional standard input."""
    arguments = [
        "node",
        str(helper),
        command,
        "--run",
        run_id,
        "--instance-id",
        instance_id,
        "--thread-id",
        thread_id,
        "--endpoint",
        endpoint,
    ]
    if message_id is not None:
        arguments.extend(["--message-id", message_id])
    arguments.extend(["--initial-attempts", str(initial_attempts)])
    arguments.extend(["--maximum-attempts", str(maximum_attempts)])
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            input=text,
            text=True,
        )
    except OSError as error:
        raise _CodexHelperError(
            f"cannot start the Codex bridge: {error}",
            initial_attempts,
        ) from error
    if completed.returncode != 0:
        raise _CodexHelperError(
            "Codex bridge failed; restart or resume the session through "
            "codex-dynamic",
            _helper_attempts(completed.stdout),
        )


def _helper_attempts(output: str) -> int | None:
    """Read a clean helper result without trusting malformed process output."""
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    attempts = result.get("attempts")
    status = result.get("status")
    if (
        type(attempts) is not int
        or not 0 <= attempts <= MAX_CODEX_DELIVERY_ATTEMPTS
        or status not in {"delivered", "failed", "unknown"}
    ):
        return None
    return attempts
