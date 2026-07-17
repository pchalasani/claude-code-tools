"""Port a Claude Code session into a resumable Codex rollout.

Reads a Claude Code session JSONL file, flattens it into a plain
text-only user/assistant transcript, and writes it as a new Codex
rollout under ``<codex_home>/sessions/YYYY/MM/DD/`` using the exact
filename convention Codex itself uses
(``rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl``), so ``codex resume
<new-id>`` can pick it up directly.

Flattening rules (mirroring the codex -> claude direction in
:mod:`claude_code_tools.port_codex_to_claude`):

* genuine user text becomes Codex user message items;
* assistant text blocks become Codex assistant message items;
* thinking / redacted-thinking blocks are dropped;
* assistant ``tool_use`` blocks are flattened into labeled text
  (``[claude tool call] name(args)``) and user-line ``tool_result``
  content into ``[claude tool result] ...`` assistant items, so a
  call and its result read together without breaking turn order;
* Claude-internal noise (non-user/assistant line types, sidechain
  lines, command wrappers, task notifications, teammate/agent
  notification wrappers) is skipped. ``<system-reminder>`` handling
  is provenance-based, grounded in how real Claude sessions record
  reminders: an injected reminder always occupies an ENTIRE user
  string / text block / tool-result item (never mixed into genuine
  text), so only pure-reminder texts are dropped, and the one shape
  Claude appends INSIDE genuine tool output -- a terminal reminder
  block whose content matches a known injected signature -- is
  peeled off the end. Any other text mentioning or containing the
  literal tag (a user asking about it, a read file ending with it)
  is ambiguous and preserved verbatim.

Only flattened tool arguments/results are truncated (at
``TOOL_TEXT_CAP`` characters, with an explicit suffix); ordinary
message text is preserved verbatim.

Durability: the rollout is written to a temporary file in the
destination directory and atomically renamed onto its final path only
after the whole transcript was written (reusing the atomic writer of
the codex -> claude direction). The new session is also appended to
Codex's ``history.jsonl`` for discoverability, following the same
convention as the session-clone/trim tooling.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from claude_code_tools.port_claude_noise import (
    _is_noise_text,
    _is_pure_reminder_text,
    _strip_system_reminders,
)
from claude_code_tools.port_codex_flatten import (
    TOOL_TEXT_CAP,
    _stringify_tool_value,
)
from claude_code_tools.port_codex_to_claude import (
    _chain_first,
    _write_transcript_atomic,
)
from claude_code_tools.session_utils import (
    get_codex_home,
    get_session_uuid,
)

# Originator stamped into the synthesized session_meta payload so the
# rollout's provenance is visible in the file itself.
PORT_ORIGINATOR = "aichat_port"

# Claude assistant content block types that are dropped entirely.
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

# Phase stamped on synthesized assistant items (matches the phase of
# user-visible assistant messages in real modern rollouts).
_ASSISTANT_PHASE = "final_answer"


def _new_codex_session_id() -> str:
    """Generate a new session id in the format Codex itself uses.

    Codex session ids are UUIDv7 values (time-ordered: a 48-bit
    Unix-millisecond timestamp followed by random bits). Python's
    :mod:`uuid` module has no ``uuid7`` before 3.14, so the value is
    assembled manually per RFC 9562.

    Returns:
        The canonical string form of a fresh UUIDv7.
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return str(uuid.UUID(int=value))


def _utc_now_codex_timestamp() -> str:
    """Build a Codex-style UTC timestamp string.

    Returns:
        Timestamp like ``2026-07-17T00:41:58.373Z`` (millisecond
        precision, ``Z`` suffix), matching real rollout lines.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{now.microsecond // 1000:03d}Z"
    )


def _iter_claude_records(
    claude_session_file: Path,
) -> Iterator[dict[str, Any]]:
    """Stream the parseable JSON dict records of a Claude session.

    Tolerant of hostile lines: invalid JSON, undecodable bytes,
    pathologically nested JSON, unparseable-but-valid JSON (e.g. huge
    integer literals), and non-dict records are skipped. Only one line
    is materialized at a time, so peak memory is bounded by the
    largest single record.

    Args:
        claude_session_file: Path to the Claude session JSONL file.

    Yields:
        Each successfully parsed top-level dict record, in order.
    """
    with open(
        claude_session_file, "r", encoding="utf-8", errors="replace"
    ) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except (ValueError, RecursionError):
                continue
            if isinstance(data, dict):
                yield data


def harvest_claude_meta(
    claude_session_file: Path,
) -> dict[str, Optional[str]]:
    """Harvest session metadata from a Claude session (streaming pass).

    Each field keeps the first valid value found on any record: the
    working directory (``cwd``), git branch (``gitBranch``), the
    source session id (``sessionId``), and the first timestamp.

    Args:
        claude_session_file: Path to the Claude session JSONL file.

    Returns:
        Dict with ``cwd``, ``branch``, ``source_id`` and ``timestamp``
        keys (values may be None when unavailable).
    """
    meta: dict[str, Optional[str]] = {
        "cwd": None,
        "branch": None,
        "source_id": None,
        "timestamp": None,
    }
    field_map = {
        "cwd": "cwd",
        "branch": "gitBranch",
        "source_id": "sessionId",
        "timestamp": "timestamp",
    }
    for data in _iter_claude_records(claude_session_file):
        for key, field in field_map.items():
            if meta[key] is None:
                value = data.get(field)
                if isinstance(value, str) and value:
                    meta[key] = value
        if all(meta[key] is not None for key in meta):
            break
    return meta


def _flatten_user_content(content: Any) -> list[tuple[str, str]]:
    """Flatten one user line's message content into (role, text) pairs.

    Genuine text blocks are merged (in order) into user messages;
    ``tool_result`` blocks become labeled ASSISTANT messages (matching
    the codex -> claude direction, where tool results live on
    assistant lines) so each result stays adjacent to its call without
    injecting fake user turns. Noise blocks (command wrappers, task
    notifications) and injected system reminders -- which always
    occupy an entire string/block of their own -- are dropped;
    genuine text merely containing the literal reminder tag is
    preserved verbatim.

    Args:
        content: The ``message.content`` value of a user line.

    Returns:
        Ordered list of ``(role, text)`` pairs (possibly empty).
    """

    def is_genuine(text: str) -> bool:
        stripped = text.strip()
        return bool(
            stripped
            and not _is_pure_reminder_text(text)
            and not _is_noise_text(stripped)
        )

    out: list[tuple[str, str]] = []
    if isinstance(content, str):
        if is_genuine(content):
            out.append(("user", content))
        return out
    if not isinstance(content, list):
        return out

    text_parts: list[str] = []

    def flush() -> None:
        if text_parts:
            out.append(("user", "\n".join(text_parts)))
            text_parts.clear()

    for block in content:
        if isinstance(block, str):
            if is_genuine(block):
                text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if not isinstance(btype, str):
            continue
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and is_genuine(text):
                text_parts.append(text)
        elif btype == "tool_result":
            flush()
            raw = block.get("content")
            rendered = _stringify_tool_value(
                _strip_system_reminders(raw), TOOL_TEXT_CAP
            )
            if not rendered:
                rendered = (
                    "(no output)" if raw is None else "(empty output)"
                )
            out.append(
                ("assistant", f"[claude tool result] {rendered}")
            )
    flush()
    return out


def _flatten_assistant_content(content: Any) -> list[tuple[str, str]]:
    """Flatten one assistant line's content into (role, text) pairs.

    Text blocks are merged (in order) into assistant messages;
    ``tool_use`` blocks become labeled assistant messages with capped
    argument text; thinking / redacted-thinking blocks are dropped.

    Args:
        content: The ``message.content`` value of an assistant line.

    Returns:
        Ordered list of ``(role, text)`` pairs (possibly empty).
    """
    out: list[tuple[str, str]] = []
    if isinstance(content, str):
        if content.strip():
            out.append(("assistant", content))
        return out
    if not isinstance(content, list):
        return out

    text_parts: list[str] = []

    def flush() -> None:
        if text_parts:
            out.append(("assistant", "\n".join(text_parts)))
            text_parts.clear()

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if not isinstance(btype, str):
            continue
        if btype in _THINKING_BLOCK_TYPES:
            continue
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
        elif btype == "tool_use":
            flush()
            name = block.get("name")
            if not isinstance(name, str) or not name:
                name = "unknown"
            args_text = _stringify_tool_value(
                block.get("input"), TOOL_TEXT_CAP
            )
            out.append(
                ("assistant", f"[claude tool call] {name}({args_text})")
            )
    flush()
    return out


def _flatten_claude_line(
    data: dict[str, Any],
) -> list[tuple[str, str]]:
    """Flatten one Claude session record into (role, text) pairs.

    Everything that is not a genuine transcript line is dropped:
    non-user/assistant line types (custom-title, agent-name, mode,
    permission-mode, file-history-snapshot, attachment, last-prompt,
    system, summary, ...), sidechain lines, and meta user lines.

    Args:
        data: A parsed top-level Claude session record.

    Returns:
        Ordered list of ``(role, text)`` pairs (possibly empty).
    """
    if data.get("isSidechain") is True:
        return []
    line_type = data.get("type")
    if line_type not in ("user", "assistant"):
        return []
    message = data.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if line_type == "user":
        if data.get("isMeta") is True:
            return []
        return _flatten_user_content(content)
    return _flatten_assistant_content(content)


def iter_flat_claude_messages(
    claude_session_file: Path, default_timestamp: Optional[str]
) -> Iterator[dict[str, Any]]:
    """Stream the flattened transcript messages of a Claude session.

    Second streaming pass: records are flattened one at a time and
    yielded immediately, so the full message list is never accumulated
    in memory. Message text is NEVER truncated here: only tool
    arguments/results were capped (at ``TOOL_TEXT_CAP``) during
    flattening.

    Args:
        claude_session_file: Path to the Claude session JSONL file.
        default_timestamp: Fallback timestamp for records without one.

    Yields:
        Dicts with ``role``, ``text`` and ``timestamp`` keys, in
        transcript order, with no empty texts.
    """
    for data in _iter_claude_records(claude_session_file):
        pairs = _flatten_claude_line(data)
        if not pairs:
            continue
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str):
            timestamp = None
        for role, text in pairs:
            if not text.strip():
                continue
            yield {
                "role": role,
                "text": text,
                "timestamp": timestamp or default_timestamp,
            }


def _ensure_leading_user(
    messages: Iterator[dict[str, Any]], source_id: str
) -> Iterator[dict[str, Any]]:
    """Guarantee the transcript opens with a user message.

    Mirrors the codex -> claude direction: when the first genuine
    content is from the assistant, a synthetic labeled user opener is
    prepended so the rollout starts with a user item.

    Args:
        messages: Stream of role/text/timestamp dicts.
        source_id: Claude session id, used in the synthetic opener.

    Yields:
        The same stream, preceded by a synthetic user opener when
        needed.
    """
    first = True
    for msg in messages:
        if first and msg["role"] != "user":
            yield {
                "role": "user",
                "text": (
                    "[Transcript ported from Claude Code session "
                    f"{source_id}]"
                ),
                "timestamp": msg["timestamp"],
            }
        first = False
        yield msg


def _new_assistant_message_id() -> str:
    """Generate an assistant message id in the modern Codex format.

    Real rollout assistant items carry ids like ``msg_<50 hex chars>``
    (25 random bytes, hex-encoded, behind a ``msg_`` prefix).

    Returns:
        A fresh id string matching ``msg_[0-9a-f]{50}``.
    """
    return "msg_" + secrets.token_hex(25)


def _make_codex_item(
    msg: dict[str, Any], fallback_timestamp: str, turn_id: str
) -> dict[str, Any]:
    """Build one synthesized Codex ``response_item`` rollout line.

    Shapes match real modern rollouts exactly: user messages carry
    ``input_text`` content blocks plus the turn-id metadata
    passthrough; assistant messages carry ``output_text`` content
    plus the modern assistant payload fields (``id``, ``phase`` and
    the same turn-id passthrough) that Codex itself writes.

    Args:
        msg: Flattened message dict (role/text/timestamp).
        fallback_timestamp: Used when the message has no timestamp.
        turn_id: UUIDv7 turn id for the metadata passthrough.

    Returns:
        Dict ready to be JSON-serialized as one rollout line.
    """
    role = msg["role"]
    passthrough = {"turn_id": turn_id}
    payload: dict[str, Any]
    if role == "user":
        payload = {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": msg["text"]}],
            "internal_chat_message_metadata_passthrough": passthrough,
        }
    else:
        payload = {
            "type": "message",
            "id": _new_assistant_message_id(),
            "role": role,
            "content": [
                {"type": "output_text", "text": msg["text"]}
            ],
            "phase": _ASSISTANT_PHASE,
            "internal_chat_message_metadata_passthrough": passthrough,
        }
    return {
        "timestamp": msg.get("timestamp") or fallback_timestamp,
        "type": "response_item",
        "payload": payload,
    }


def _iter_rollout_lines(
    messages: Iterator[dict[str, Any]],
    *,
    session_meta_line: dict[str, Any],
    fallback_timestamp: str,
) -> Iterator[dict[str, Any]]:
    """Yield the session_meta line, then every response_item line.

    Every item carries the UUIDv7 turn-id passthrough real rollouts
    have: a fresh turn id is minted whenever a user message starts a
    new turn (i.e. follows a non-user item), and the items of that
    turn -- its user message(s) plus the assistant items that follow
    -- share it, matching Codex's own turn grouping.

    Args:
        messages: Flattened role/text/timestamp message dicts.
        session_meta_line: The synthesized session_meta record.
        fallback_timestamp: Used when a message has no timestamp.

    Yields:
        Serializable rollout line dicts, session_meta first.
    """
    yield session_meta_line
    turn_id: Optional[str] = None
    prev_role: Optional[str] = None
    for msg in messages:
        if turn_id is None or (
            msg["role"] == "user" and prev_role != "user"
        ):
            turn_id = _new_codex_session_id()
        prev_role = msg["role"]
        yield _make_codex_item(msg, fallback_timestamp, turn_id)


def _rollback_history_append(
    fd: int, original_size: int, entry: bytes
) -> None:
    """Undo a failed history append without touching foreign bytes.

    ``flock`` is advisory and Codex itself does not take it, so
    another process may have appended to ``history.jsonl`` after the
    pre-append size was recorded. The region past ``original_size``
    is therefore re-read first, and the file is truncated back ONLY
    when it contains nothing but this operation's own (possibly
    partial) entry bytes. Any mismatch means concurrent data was
    interleaved: the shared file is left untouched, since destroying
    another writer's entry would be worse than leaving this
    operation's unterminated fragment (which the malformed-tail
    guard isolates on the next append).

    Args:
        fd: Open descriptor of the history file (lock still held).
        original_size: File size recorded before this append began.
        entry: The exact bytes this operation attempted to append.
    """
    try:
        current_size = os.fstat(fd).st_size
        tail_len = current_size - original_size
        if 0 <= tail_len <= len(entry):
            tail = os.pread(fd, tail_len, original_size)
            if tail == entry[:tail_len]:
                os.ftruncate(fd, original_size)
    except OSError:
        pass


def _append_history_transactional(
    codex_home_dir: Path,
    session_id: str,
    first_user_msg: str,
    rollout_path: Path,
) -> None:
    """Append one session entry to ``history.jsonl``, failure-atomic.

    Uses the same entry shape as the clone/trim tooling
    (``session_id`` / ``ts`` / ``text`` capped at 500 chars). The
    append happens under an exclusive :func:`fcntl.flock` on the
    history file: the pre-append size is recorded, the tail of any
    existing content is validated -- when a previous writer left an
    unterminated final line (a complete record missing its newline,
    or an interrupted fragment), a separating newline is written
    first so the new entry always starts its own line and stays
    discoverable -- then the complete entry is written and fsynced.
    On ANY failure -- ``OSError``, ``KeyboardInterrupt`` or any other
    ``BaseException``, including one interrupting a partially-written
    entry -- the rollback (:func:`_rollback_history_append`) first
    verifies that everything past the recorded size is this
    operation's own bytes and only then truncates back (a concurrent
    lock-free writer's data is never destroyed); the just-published
    rollout is removed before the exception propagates. A retry can
    therefore neither concatenate onto a malformed fragment nor
    create a duplicate discoverable session.

    Args:
        codex_home_dir: Codex home directory holding history.jsonl.
        session_id: New session id to record.
        first_user_msg: Preview text for the entry (capped at 500).
        rollout_path: The rollout published just before this append;
            removed again if the append fails.

    Raises:
        OSError: On any filesystem failure (after cleanup).
    """
    history_file = codex_home_dir / "history.jsonl"
    entry = (
        json.dumps(
            {
                "session_id": session_id,
                "ts": int(time.time()),
                "text": first_user_msg[:500],
            }
        )
        + "\n"
    ).encode("utf-8")
    try:
        fd = os.open(
            str(history_file),
            os.O_RDWR | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            original_size = os.fstat(fd).st_size
            if (
                original_size > 0
                and os.pread(fd, 1, original_size - 1) != b"\n"
            ):
                # Separate an unterminated final line so the new
                # entry starts a line of its own.
                entry = b"\n" + entry
            try:
                written = 0
                while written < len(entry):
                    written += os.write(fd, entry[written:])
                os.fsync(fd)
            except BaseException:
                _rollback_history_append(fd, original_size, entry)
                raise
        finally:
            os.close(fd)
    except BaseException:
        rollout_path.unlink(missing_ok=True)
        raise


def port_claude_session_to_codex(
    claude_session_file: Union[str, Path],
    codex_home: Optional[Union[str, Path]] = None,
) -> tuple[str, Path]:
    """Convert a Claude Code session into a new Codex rollout file.

    The resulting rollout is a flattened, text-only transcript that
    Codex can resume with ``codex resume <new-id>`` from the session's
    working directory. The session is read in two streaming passes
    (metadata, then messages) and output lines are streamed to disk,
    so memory use stays bounded for arbitrarily large files. The new
    session is appended to Codex's ``history.jsonl`` afterwards
    (same convention as the clone/trim tooling).

    Args:
        claude_session_file: Path to the Claude session JSONL file.
        codex_home: Optional Codex home directory (defaults to
            ``~/.codex``).

    Returns:
        Tuple ``(new_session_id, new_file_path)``.

    Raises:
        FileNotFoundError: If the Claude session file does not exist.
        ValueError: If no portable messages are found in the file.
        OSError: On filesystem failures while creating or writing the
            output rollout file, or while appending to
            ``history.jsonl`` (the just-written rollout is removed
            first so retries cannot create duplicates).
    """
    claude_path = Path(claude_session_file).expanduser()
    if not claude_path.is_file():
        raise FileNotFoundError(
            f"Claude session not found: {claude_path}"
        )

    meta = harvest_claude_meta(claude_path)
    source_id = meta["source_id"] or get_session_uuid(claude_path.stem)
    now_iso = _utc_now_codex_timestamp()
    fallback_ts = meta["timestamp"] or now_iso

    transcript = _ensure_leading_user(
        iter_flat_claude_messages(claude_path, meta["timestamp"]),
        source_id,
    )
    try:
        first_msg = next(transcript)
    except StopIteration:
        raise ValueError(
            f"No portable messages found in {claude_path}"
        ) from None

    cwd = meta["cwd"]
    if not isinstance(cwd, str) or not cwd:
        cwd = str(Path.cwd())

    new_session_id = _new_codex_session_id()
    home_dir = get_codex_home(
        str(codex_home) if codex_home is not None else None
    )
    now_local = datetime.datetime.now()
    day_dir = home_dir / "sessions" / now_local.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local.strftime("%Y-%m-%dT%H-%M-%S")
    out_path = day_dir / f"rollout-{stamp}-{new_session_id}.jsonl"

    payload: dict[str, Any] = {
        # Real root (non-forked) rollouts carry BOTH `session_id` and
        # `id`, with equal values; forked ones point `session_id` at
        # the root thread. A ported session is a fresh root.
        "session_id": new_session_id,
        "id": new_session_id,
        "timestamp": now_iso,
        "cwd": cwd,
        "originator": PORT_ORIGINATOR,
        "cli_version": "aichat-port",
    }
    if meta["branch"]:
        payload["git"] = {"branch": meta["branch"]}
    session_meta_line: dict[str, Any] = {
        "timestamp": now_iso,
        "type": "session_meta",
        "payload": payload,
        # Lineage marker for aichat tooling, placed at the TOP level
        # of the session_meta line (next to timestamp/type/payload):
        # the same placement codex_continue uses, which Codex is known
        # to tolerate when resuming.
        "continue_metadata": {
            "parent_session_id": source_id,
            "parent_session_file": str(claude_path.absolute()),
            "continued_at": now_iso,
            "ported_from": "claude",
        },
    }

    # Capture the first genuine user text while streaming, for the
    # history.jsonl preview (same convention as the clone/trim flows).
    first_user_text: dict[str, Optional[str]] = {"text": None}

    def capture(
        msgs: Iterator[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        for msg in msgs:
            if (
                first_user_text["text"] is None
                and msg["role"] == "user"
            ):
                first_user_text["text"] = msg["text"]
            yield msg

    _write_transcript_atomic(
        out_path,
        _iter_rollout_lines(
            capture(_chain_first(first_msg, transcript)),
            session_meta_line=session_meta_line,
            fallback_timestamp=fallback_ts,
        ),
    )

    # Append to Codex's history.jsonl so the new session shows up in
    # Codex's own discovery/search, mirroring the clone/trim tooling.
    # The append is transactional: any failure (even a mid-write
    # interruption) truncates the partial entry away and removes the
    # just-published rollout, so a retry cannot corrupt history or
    # create duplicate sessions.
    _append_history_transactional(
        home_dir,
        new_session_id,
        first_user_text["text"] or "Ported Claude session",
        out_path,
    )

    return new_session_id, out_path
