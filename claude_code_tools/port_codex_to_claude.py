"""Port a Codex rollout session into a resumable Claude Code session.

Reads a Codex session JSONL file (modern payload-wrapped format, with
best-effort support for the legacy 2025 format), flattens it into a
plain text-only user/assistant transcript, and writes it as a new
Claude Code session under ``<claude_home>/projects/<encoded-cwd>/``.

Tool calls and tool results are flattened into clearly labeled text on
assistant lines; reasoning items, encrypted payloads, and Codex
bookkeeping records are dropped entirely.

Memory bounds: the rollout is processed in two streaming passes (a
metadata-harvesting pass, then a message pass), so the full transcript
is never accumulated in memory -- at most one merged same-role turn is
held at a time. Ordinary user/assistant message text is NEVER
truncated (the transcript is preserved verbatim); only flattened tool
arguments/results are capped, at ``TOOL_TEXT_CAP`` characters, with
structured tool values serialized incrementally (never materializing
a full ``json.dumps`` copy). Input records are parsed one line at a
time, so peak memory is proportional to the largest single record or
merged turn; output lines are streamed to disk as they are finalized.

Durability: the output session is written to a temporary file in the
destination directory and atomically renamed onto its final
discoverable path only after the whole transcript was written, so a
mid-stream failure never leaves a partial session that Claude Code
could treat as resumable.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional, Union

# The per-item flattening rules (tool-call/result labeling, the
# tool-text cap, encrypted-content removal) live in
# port_codex_flatten; the cap is re-exported here as part of this
# module's public constants.
from claude_code_tools.port_codex_flatten import (  # noqa: F401
    TOOL_TEXT_CAP,
    _flatten_payload,
    _join_text_blocks,
)
from claude_code_tools.session_utils import (
    encode_claude_project_path,
    get_claude_home,
    get_session_uuid,
)

# Constant "version" string stamped on every synthesized Claude line.
CLAUDE_LINE_VERSION = "2.1.211"

# Maximum byte length of the encoded per-project directory name.
# Common filesystems bound a single path component at 255 bytes.
MAX_PROJECT_COMPONENT_BYTES = 255

# Top-level line types in modern rollouts that carry no transcript
# content and are dropped entirely.
_DROPPED_LINE_TYPES = frozenset(
    {
        "event_msg",
        "turn_context",
        "world_state",
        "inter_agent_communication_metadata",
        "compacted",
    }
)

# Rollouts without session_meta embed the cwd inside
# <environment_context>: real legacy 2025 rollouts use a plain
# "Current working directory: <path>" line, while newer environment
# contexts wrap it in a <cwd>...</cwd> tag. Both are supported.
_CWD_TAG_RE = re.compile(r"<cwd>\s*(.*?)\s*</cwd>", re.DOTALL)
_CWD_LINE_RE = re.compile(
    r"^\s*Current working directory:[ \t]*(.+?)[ \t\r]*$",
    re.MULTILINE,
)

# Legacy bare item types that map to response_item payloads.
_LEGACY_ITEM_TYPES = frozenset(
    {
        "message",
        "agent_message",
        "reasoning",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
    }
)


def _harvest_environment_cwd(
    payload: dict[str, Any], meta: dict[str, Any]
) -> None:
    """Harvest cwd from an ``<environment_context>`` message if needed.

    Legacy rollouts have no session_meta line; their cwd only appears
    inside the injected environment-context user message. Real legacy
    2025 rollouts state it as a plain
    ``Current working directory: <path>`` line; newer environment
    contexts use a ``<cwd>...</cwd>`` tag. Both forms are recognized.

    Args:
        payload: A Codex message payload.
        meta: Session metadata dict, updated in place.
    """
    if meta.get("cwd"):
        return
    text = _join_text_blocks(payload.get("content"))
    if not text.lstrip().startswith("<environment_context>"):
        return
    match = _CWD_TAG_RE.search(text) or _CWD_LINE_RE.search(text)
    if match and match.group(1):
        meta["cwd"] = match.group(1)


def _session_meta_id(payload: dict[str, Any]) -> Optional[str]:
    """Extract the validated session id of a session_meta payload.

    Checks ``id`` first, then ``session_id`` (some rollout variants
    use the latter). Absent, null, or non-string values are ignored.

    Args:
        payload: A session_meta payload dict.

    Returns:
        The session id string, or None if neither field is valid.
    """
    for key in ("id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _harvest_session_meta(
    data: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any]
) -> None:
    """Harvest session metadata fields from one session_meta record.

    Every field is validated before use: absent, null, or wrongly
    typed values are ignored (callers reset ``meta`` first when the
    record must replace previously harvested values atomically).

    Args:
        data: The full session_meta line.
        payload: The line's payload dict.
        meta: Session metadata dict, updated in place.
    """
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        meta["cwd"] = cwd
    git_info = payload.get("git")
    if isinstance(git_info, dict):
        branch = git_info.get("branch")
        if isinstance(branch, str) and branch:
            meta["branch"] = branch
    session_id = _session_meta_id(payload)
    if session_id:
        meta["source_id"] = session_id
    timestamp = data.get("timestamp")
    if not isinstance(timestamp, str):
        timestamp = payload.get("timestamp")
    if isinstance(timestamp, str) and timestamp:
        meta["timestamp"] = timestamp


def _iter_rollout_records(
    codex_session_file: Path,
) -> Iterator[dict[str, Any]]:
    """Stream the parseable JSON dict records of a rollout file.

    Tolerant of malformed lines: invalid JSON, undecodable bytes,
    pathologically nested JSON, unparseable-but-valid JSON (e.g. an
    integer literal exceeding Python's digit limit, which raises a
    plain ``ValueError``), and non-dict records are skipped.
    Oversized-but-valid records (e.g. a huge tool result) are parsed
    normally, never dropped: only one line is materialized at a time,
    so peak memory is bounded by the largest single record.

    Args:
        codex_session_file: Path to the Codex rollout JSONL file.

    Yields:
        Each successfully parsed top-level dict record, in order.
    """
    with open(
        codex_session_file, "r", encoding="utf-8", errors="replace"
    ) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except (ValueError, RecursionError):
                # ValueError covers json.JSONDecodeError plus
                # non-decode failures like huge integer literals.
                continue
            if isinstance(data, dict):
                yield data


def _extract_item_payload(
    data: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Extract the transcript-item payload of one rollout record.

    Args:
        data: A parsed top-level rollout record.

    Returns:
        The payload dict of a modern ``response_item`` line, the
        record itself for a legacy bare item, or None for any other
        (metadata/bookkeeping/malformed) record.
    """
    line_type = data.get("type")
    if not isinstance(line_type, str):
        # Malformed record whose type is a list/dict/number: not a
        # transcript item (unhashable shapes would also crash the
        # set-membership test below).
        return None
    if line_type == "response_item":
        candidate = data.get("payload")
        if isinstance(candidate, dict):
            return candidate
        return None
    if line_type in _LEGACY_ITEM_TYPES and "payload" not in data:
        # Legacy 2025 format: the line itself is the item.
        return data
    return None


def _is_legacy_header(data: dict[str, Any]) -> bool:
    """Check whether a record is a legacy 2025 rollout header line.

    Args:
        data: A parsed top-level rollout record.

    Returns:
        True for a type-less ``{id, timestamp, ...}`` header line.
    """
    return (
        data.get("type") is None
        and "record_type" not in data
        and "id" in data
        and "timestamp" in data
    )


def harvest_rollout_meta(codex_session_file: Path) -> dict[str, Any]:
    """Harvest session metadata from a rollout file (streaming pass).

    Session metadata is harvested from the first session_meta record,
    except that a session_meta whose id (``id`` or ``session_id``
    field) matches the rollout filename UUID is authoritative:
    rollouts of forked sessions embed ancestor session_meta records
    too, so when the authoritative record is found all previously
    harvested metadata is reset first and replaced atomically, and
    later records (including legacy-looking header lines) can no
    longer overwrite it. Fields the authoritative record lacks stay
    unset so the caller's documented fallbacks apply (cwd -> current
    directory, branch -> "", timestamp -> now).

    Args:
        codex_session_file: Path to the Codex rollout JSONL file.

    Returns:
        Dict with ``cwd``, ``branch``, ``source_id`` and ``timestamp``
        keys (values may be None when unavailable).
    """
    file_uuid = get_session_uuid(codex_session_file.stem)
    meta: dict[str, Any] = {
        "cwd": None,
        "branch": None,
        "source_id": None,
        "timestamp": None,
    }
    meta_harvested = False
    meta_authoritative = False

    for data in _iter_rollout_records(codex_session_file):
        line_type = data.get("type")

        if line_type == "session_meta":
            payload = data.get("payload")
            if not isinstance(payload, dict):
                continue
            is_file_meta = _session_meta_id(payload) == file_uuid
            if is_file_meta and not meta_authoritative:
                for key in meta:
                    meta[key] = None
                _harvest_session_meta(data, payload, meta)
                meta_authoritative = True
                meta_harvested = True
            elif not meta_harvested:
                _harvest_session_meta(data, payload, meta)
                meta_harvested = True
            continue

        if _is_legacy_header(data) and not meta_authoritative:
            header_id = data.get("id")
            if isinstance(header_id, str) and header_id:
                meta["source_id"] = header_id
            timestamp = data.get("timestamp")
            if meta["timestamp"] is None and isinstance(timestamp, str):
                meta["timestamp"] = timestamp
            git_info = data.get("git")
            if isinstance(git_info, dict):
                branch = git_info.get("branch")
                if isinstance(branch, str) and branch:
                    meta["branch"] = branch
            continue

        payload = _extract_item_payload(data)
        if (
            payload is not None
            and payload.get("type") == "message"
            and not meta_authoritative
        ):
            # Legacy fallback only: once an authoritative session_meta
            # was seen, an (ancestor) environment context must not
            # refill a deliberately unset cwd.
            _harvest_environment_cwd(payload, meta)

    return meta


def iter_flat_messages(
    codex_session_file: Path, default_timestamp: Optional[str]
) -> Iterator[dict[str, Any]]:
    """Stream the flattened transcript messages of a rollout file.

    Second streaming pass: transcript items are flattened one at a
    time and yielded immediately, so the full message list is never
    accumulated in memory regardless of file size. Message text is
    NEVER truncated here: only tool arguments/results were already
    capped (at ``TOOL_TEXT_CAP``) during flattening.

    Args:
        codex_session_file: Path to the Codex rollout JSONL file.
        default_timestamp: Fallback timestamp for records without one
            (typically the harvested session_meta timestamp).

    Yields:
        Dicts with ``role``, ``text`` and ``timestamp`` keys.
    """
    for data in _iter_rollout_records(codex_session_file):
        line_type = data.get("type")
        if not isinstance(line_type, str):
            # Every transcript item carries a string type (modern
            # response_item lines and legacy bare items alike);
            # non-string shapes are unrecognized records, and
            # unhashable ones would crash the set-membership test.
            continue
        if line_type == "session_meta" or line_type in _DROPPED_LINE_TYPES:
            continue
        payload = _extract_item_payload(data)
        if payload is None:
            continue
        flattened = _flatten_payload(payload)
        if flattened is None:
            continue
        role, text = flattened
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str):
            timestamp = None
        yield {
            "role": role,
            "text": text,
            "timestamp": timestamp or default_timestamp,
        }


def merge_and_alternate(
    messages: Iterator[dict[str, Any]], source_id: str
) -> Iterator[dict[str, Any]]:
    """Merge consecutive same-role messages into an alternating stream.

    Guarantees the result starts with a user message (prepending a
    synthetic context line if the first content is from the
    assistant), ends with an assistant message (appending a synthetic
    closer if the transcript ends on an unanswered user message, so
    resuming with a new prompt never produces two consecutive user
    turns), and contains no empty messages.

    Merged text is preserved COMPLETELY: no cap is applied while
    merging, so arbitrarily long turns survive verbatim (only tool
    arguments/results were truncated earlier, during flattening).
    Only one pending turn is held in memory at a time, as a LIST of
    fragments joined once when the role changes -- never by repeated
    string concatenation, which would be quadratic for turns made of
    many consecutive same-role records.

    Args:
        messages: Stream of role/text/timestamp dicts.
        source_id: Codex session id, used in the synthetic lines.

    Yields:
        Strictly alternating role/text/timestamp dicts starting with
        a user message and ending with an assistant message.
    """

    def finalize(turn: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": turn["role"],
            "text": "\n\n".join(turn["parts"]),
            "timestamp": turn["timestamp"],
        }

    pending: Optional[dict[str, Any]] = None
    for msg in messages:
        if not msg["text"].strip():
            continue
        if pending is None:
            if msg["role"] != "user":
                yield {
                    "role": "user",
                    "text": (
                        "[Transcript ported from Codex session "
                        f"{source_id}]"
                    ),
                    "timestamp": msg["timestamp"],
                }
            pending = {
                "role": msg["role"],
                "parts": [msg["text"]],
                "timestamp": msg["timestamp"],
            }
            continue
        if pending["role"] == msg["role"]:
            pending["parts"].append(msg["text"])
        else:
            yield finalize(pending)
            pending = {
                "role": msg["role"],
                "parts": [msg["text"]],
                "timestamp": msg["timestamp"],
            }

    if pending is None:
        return
    yield finalize(pending)
    if pending["role"] == "user":
        yield {
            "role": "assistant",
            "text": (
                "[Transcript ported from Codex session "
                f"{source_id} ended before this user message "
                "was answered.]"
            ),
            "timestamp": pending["timestamp"],
        }


def _make_claude_line(
    msg: dict[str, Any],
    *,
    parent_uuid: Optional[str],
    session_id: str,
    cwd: str,
    branch: str,
    fallback_timestamp: str,
) -> dict[str, Any]:
    """Build one synthesized Claude session JSONL record.

    Args:
        msg: Flattened message dict (role/text/timestamp).
        parent_uuid: uuid of the previous line, or None for the first.
        session_id: The new Claude session id.
        cwd: Session working directory.
        branch: Git branch name ("" if unknown).
        fallback_timestamp: Used when the message has no timestamp.

    Returns:
        Dict ready to be JSON-serialized as one session line.
    """
    role = msg["role"]
    return {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": CLAUDE_LINE_VERSION,
        "gitBranch": branch,
        "type": role,
        "message": {
            "role": role,
            "content": [{"type": "text", "text": msg["text"]}],
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": msg.get("timestamp") or fallback_timestamp,
    }


def _safe_project_component(cwd: str) -> Optional[str]:
    """Encode a cwd into a validated, filesystem-safe directory name.

    A harvested cwd is untrusted data: an unpaired surrogate, an
    embedded NUL character, or an overlong value would make ``mkdir``
    raise ``UnicodeEncodeError``, ``ValueError``, or ``OSError`` after
    porting already began. The encoded component is therefore
    validated before any filesystem operation.

    Args:
        cwd: Session working directory (string, may be hostile).

    Returns:
        The encoded project directory name, or None when the value
        cannot be used as a single directory name (empty, containing
        NUL, not UTF-8-encodable, or over
        ``MAX_PROJECT_COMPONENT_BYTES``).
    """
    component = encode_claude_project_path(cwd)
    if not component or "\x00" in component:
        return None
    try:
        encoded = component.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > MAX_PROJECT_COMPONENT_BYTES:
        return None
    return component


def port_codex_session_to_claude(
    codex_session_file: Union[str, Path],
    claude_home: Optional[Union[str, Path]] = None,
) -> tuple[str, Path]:
    """Convert a Codex rollout into a new Claude Code session file.

    The resulting session is a flattened, text-only transcript that
    Claude Code can resume with ``claude --resume <new-id>`` from the
    session's working directory. The rollout is read in two streaming
    passes (metadata, then messages) and output lines are streamed to
    disk, so memory use stays bounded for arbitrarily large files.

    Args:
        codex_session_file: Path to the Codex rollout JSONL file.
        claude_home: Optional Claude home directory (defaults to
            ``~/.claude`` or ``$CLAUDE_CONFIG_DIR``).

    Returns:
        Tuple ``(new_session_id, new_file_path)``.

    Raises:
        FileNotFoundError: If the Codex session file does not exist.
        ValueError: If no portable messages are found in the file, or
            no usable project directory name can be derived.
        OSError: On filesystem failures while creating or writing the
            output session file.
    """
    codex_path = Path(codex_session_file).expanduser()
    if not codex_path.is_file():
        raise FileNotFoundError(f"Codex session not found: {codex_path}")

    meta = harvest_rollout_meta(codex_path)
    source_id = meta["source_id"] or get_session_uuid(codex_path.stem)
    transcript = merge_and_alternate(
        iter_flat_messages(codex_path, meta["timestamp"]), source_id
    )
    try:
        first_msg = next(transcript)
    except StopIteration:
        raise ValueError(
            f"No portable messages found in {codex_path}"
        ) from None

    # The harvested cwd is untrusted: values whose encoded project
    # component is not filesystem-safe (unpaired surrogates, overlong
    # names) fall back to the current directory, like a missing cwd.
    cwd = meta["cwd"]
    component: Optional[str] = None
    if isinstance(cwd, str) and cwd:
        component = _safe_project_component(cwd)
    if component is None:
        cwd = str(Path.cwd())
        component = _safe_project_component(cwd)
        if component is None:
            raise ValueError(
                "Cannot derive a usable Claude project directory "
                f"name from cwd: {cwd!r}"
            )
    branch = meta["branch"] or ""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    fallback_ts = meta["timestamp"] or now_utc.isoformat()

    new_session_id = str(uuid.uuid4())
    home_dir = get_claude_home(
        str(claude_home) if claude_home is not None else None
    )
    project_dir = home_dir / "projects" / component
    project_dir.mkdir(parents=True, exist_ok=True)
    out_path = project_dir / f"{new_session_id}.jsonl"

    continue_metadata = {
        "parent_session_id": source_id,
        "parent_session_file": str(codex_path.absolute()),
        "continued_at": now_utc.isoformat(),
        "ported_from": "codex",
    }

    _write_transcript_atomic(
        out_path,
        _iter_claude_lines(
            _chain_first(first_msg, transcript),
            session_id=new_session_id,
            cwd=cwd,
            branch=branch,
            fallback_timestamp=fallback_ts,
            continue_metadata=continue_metadata,
        ),
    )

    return new_session_id, out_path


def _iter_claude_lines(
    messages: Iterator[dict[str, Any]],
    *,
    session_id: str,
    cwd: str,
    branch: str,
    fallback_timestamp: str,
    continue_metadata: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Build the uuid-chained Claude session line dicts of a transcript.

    Args:
        messages: Alternating role/text/timestamp message dicts.
        session_id: The new Claude session id.
        cwd: Session working directory.
        branch: Git branch name ("" if unknown).
        fallback_timestamp: Used when a message has no timestamp.
        continue_metadata: Lineage metadata attached to the first
            line only.

    Yields:
        One serializable Claude session line dict per message, with
        ``parentUuid`` linking each line to the previous one.
    """
    parent_uuid: Optional[str] = None
    first = True
    for msg in messages:
        line = _make_claude_line(
            msg,
            parent_uuid=parent_uuid,
            session_id=session_id,
            cwd=cwd,
            branch=branch,
            fallback_timestamp=fallback_timestamp,
        )
        if first:
            line["continue_metadata"] = continue_metadata
            first = False
        yield line
        parent_uuid = line["uuid"]


def _write_transcript_atomic(
    out_path: Path, lines: Iterator[dict[str, Any]]
) -> None:
    """Write session lines to ``out_path`` atomically.

    The transcript is streamed to a temporary file in the destination
    directory, flushed and fsynced, then atomically renamed onto the
    final path only after every line was written successfully. A
    failure at any point (disk full, interruption, serialization
    error, a raising ``lines`` iterator) therefore never leaves a
    partial file at the discoverable final path; the temporary file
    is removed on every exception before it is re-raised.

    Args:
        out_path: Final session file path (its parent must exist).
        lines: Stream of JSON-serializable session line dicts.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=out_path.parent, prefix=f".{out_path.stem}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _chain_first(
    first: dict[str, Any], rest: Iterator[dict[str, Any]]
) -> Iterator[dict[str, Any]]:
    """Re-attach a peeked first element to the front of a stream.

    Args:
        first: The element already consumed from the stream.
        rest: The remainder of the stream.

    Yields:
        ``first``, then every element of ``rest``.
    """
    yield first
    yield from rest
