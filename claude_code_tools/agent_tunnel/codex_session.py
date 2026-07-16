"""Codex session discovery and file-level forking.

Codex CLI writes one rollout file per session:
``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<local-ts>-<session-id>.jsonl``.
Unlike Claude Code, ``codex exec resume <id>`` APPENDS to that same file (the
session id stays stable across turns), and codex's native ``fork`` command is
interactive-only (TUI). So the tunnel forks at the FILE level: copy the
expert's rollout under a fresh id — recording ``forked_from_id`` /
``parent_thread_id`` exactly like codex's own fork does — and every remote
turn resumes the copy. The owner's session file is never touched. Validated
live against codex-cli 0.144.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from claude_code_tools.session_utils import (
    extract_session_metadata_codex,
    get_codex_home,
    is_valid_session,
)

# Trailing UUID in a rollout filename stem (rollout-<ts>-<uuid>).
ROLLOUT_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)
# A canonical session id (whole string). Used to reject values before they
# reach a glob — an id like "*" or "a[bc]" would otherwise match unrelated
# rollouts through glob metacharacters.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Meta-line marker stamped on rollouts THIS tunnel forks, so auto-discovery
# can skip them without also skipping a user's native `codex fork` (which
# sets codex's generic forked_from_id). Codex tolerates unknown meta fields.
TUNNEL_FORK_KEY = "agent_tunnel_fork"
# System-injected user "messages" that are not real turns.
_SYNTHETIC_PREFIXES = (
    "<user_instructions>",
    "<environment_context>",
    "<turn_context>",
    "<permissions",
    "<stdin",
)


def uuid7() -> str:
    """A UUIDv7 string (time-ordered), matching codex's session-id style.

    Hand-rolled because the stdlib gains ``uuid.uuid7`` only in 3.14.
    """
    ms = int(time.time() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    h = bytes(raw).hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def rollout_session_id(path: Path) -> str:
    """Session id embedded in a rollout filename ('' if none)."""
    match = ROLLOUT_ID_RE.search(path.stem)
    return match.group(1) if match else ""


def _safe_mtime(path: Path) -> float:
    """Sort key that never raises: a dangling symlink or a file removed
    between globbing and sorting sorts oldest instead of crashing."""
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def _is_file(path: Path) -> bool:
    """True if path is an existing regular file (a broken symlink is False),
    tolerating any stat error."""
    try:
        return path.is_file()
    except OSError:
        return False


def _read_stable_lines(path: Path) -> list[str]:
    """Read a rollout's complete JSONL records as a consistent snapshot.

    Forking an ACTIVELY-RUNNING session races codex's appends: a plain read
    can capture a torn trailing record. Read until two consecutive reads
    agree on (size, mtime), then drop a trailing partial line — codex writes
    newline-terminated JSONL, so a body not ending in a newline means the
    last record is mid-append and must not be copied into the fork.

    Raises:
        ValueError: The file is not valid UTF-8.
    """
    text = ""
    prev_sig = None
    for _ in range(4):
        try:
            sig = path.stat()
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Rollout is not valid UTF-8: {path}") from exc
        cur_sig = (sig.st_size, sig.st_mtime_ns)
        if cur_sig == prev_sig:
            break
        prev_sig = cur_sig
        time.sleep(0.05)  # let an in-flight append settle, then re-read
    # Split ONLY on the JSONL delimiter "\n": str.splitlines() would also
    # break on U+2028/U+2029/U+0085, shattering a record that legitimately
    # contains one of those inside a JSON string.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the empty element after a trailing newline
    # A torn trailing append leaves an incomplete last record: drop the last
    # line only if it does not parse as JSON (a complete record survives even
    # without a trailing newline; codex writes valid JSONL, so a broken tail
    # is always a mid-write capture).
    if lines:
        try:
            json.loads(lines[-1])
        except ValueError:  # JSONDecodeError included
            lines = lines[:-1]
    return lines


def codex_home_for(rollout_file: Path) -> Optional[Path]:
    """The CODEX_HOME a rollout file lives under (parent of ``sessions/``).

    Derived structurally from the dated layout
    (``home/sessions/YYYY/MM/DD/rollout.jsonl``) so a home path that itself
    contains a ``sessions`` segment is never truncated at the wrong spot.
    """
    parents = rollout_file.parents
    if len(parents) >= 5 and parents[3].name == "sessions":
        return parents[4]
    text = str(rollout_file)
    if "/sessions/" not in text:
        return None
    # Nonstandard depth: fall back to the LAST /sessions/ (the dated tree's
    # own segment is always last — its children are digit-named).
    return Path(text.rsplit("/sessions/", 1)[0])


def sessions_dir(codex_home: Optional[Path] = None) -> Path:
    """The dated rollout tree for a codex home.

    Defaults honor the ``CODEX_HOME`` env var (codex itself does), falling
    back to ``~/.codex`` — so discovery finds sessions wherever the owner's
    codex actually writes them.
    """
    home = Path(
        codex_home or os.environ.get("CODEX_HOME") or get_codex_home()
    ).expanduser()
    # Anchor a relative CODEX_HOME to an absolute path (against the CURRENT
    # process's cwd) so the config_dir stored at publish time still resolves
    # when the daemon later launches codex from a DIFFERENT directory — the
    # same absolute-path discipline the rest of the tunnel config follows.
    if not home.is_absolute():
        home = Path(os.path.abspath(home))
    return home / "sessions"


def find_codex_session_file(
    session_id: str, codex_home: Optional[Path] = None
) -> Optional[Path]:
    """Locate a rollout file by exact session id (newest first).

    ``session_id`` must be a canonical UUID; anything else (empty, a glob
    like ``*``, a bracket pattern) is rejected so it can never be
    interpolated into the glob and match an unrelated rollout.
    """
    if not _UUID_RE.match(session_id or ""):
        return None
    root = sessions_dir(codex_home)
    if not root.is_dir():
        return None
    hits = [
        p
        for p in root.glob(f"*/*/*/rollout-*-{session_id}.jsonl")
        # Exact id (case-insensitive) — the glob's trailing `-<id>` is a
        # belt-and-suspenders match; confirm precisely. Skip a dangling
        # symlink / removed file (is_file follows a valid symlink, is False
        # for a broken one): a non-existent path is not a usable session.
        if rollout_session_id(p).lower() == session_id.lower() and _is_file(p)
    ]
    if not hits:
        return None
    hits.sort(key=_safe_mtime, reverse=True)
    return hits[0]


def _first_line(path: Path) -> Optional[dict[str, Any]]:
    """Parsed first JSONL line of a rollout ('meta' line), or None.

    Tolerant of corruption: a non-UTF-8 byte or malformed JSON yields None
    rather than raising (``UnicodeDecodeError`` is a ``ValueError``, so it is
    caught here alongside ``JSONDecodeError``).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline().strip()
        return json.loads(line) if line else None
    except (OSError, ValueError):
        return None


def rollout_cwd(path: Path) -> str:
    """Working directory a rollout was recorded in ('' if unknown).

    Fast path reads only the meta line (modern format:
    ``{"type": "session_meta", "payload": {"cwd": ...}}``); legacy rollouts
    without a meta cwd fall back to a full metadata scan.
    """
    meta = _first_line(path)
    if isinstance(meta, dict):
        payload = meta.get("payload")
        if isinstance(payload, dict) and payload.get("cwd"):
            return str(payload["cwd"])
        if meta.get("cwd"):
            return str(meta["cwd"])
    scanned = extract_session_metadata_codex(path)
    return str((scanned or {}).get("cwd") or "")


def _is_tunnel_fork(path: Path) -> bool:
    """True if a rollout was created by THIS tunnel's file-level fork.

    Keyed on the tunnel-specific ``TUNNEL_FORK_KEY`` marker (verified live:
    codex tolerates unknown meta fields on resume), NOT codex's generic
    ``forked_from_id`` — which a user's own ``codex fork`` also sets, and
    which must remain auto-shareable. Discovery skips tunnel forks so a
    colleague fork (it shares the expert's cwd, and can be the newest file)
    is never auto-selected as an expert, independent of exclusion-set timing
    (closing the copy-then-record race; a partial write with no meta yet is
    also ignored).
    """
    meta = _first_line(path)
    if not isinstance(meta, dict):
        return False
    payload = meta.get("payload")
    scope = payload if isinstance(payload, dict) else meta
    return bool(scope.get(TUNNEL_FORK_KEY))


def find_latest_codex_session(
    project_dir: Path,
    exclude: set[str],
    codex_home: Optional[Path] = None,
) -> Optional[Path]:
    """Newest (by mtime) valid non-fork codex session in `project_dir`.

    Ordered by file MTIME across the WHOLE tree, not by day directory: a
    ``codex exec resume`` appends to a session's original rollout in place,
    so an actively-resumed older-day session can be genuinely newer than a
    later-day one. No day-count horizon; the newest matching session is
    always found. mtime comes from ``stat`` (no read), and only the leading
    candidates are opened, so even a large history stays cheap.

    Args:
        project_dir: Project the session must have run in (cwd match).
        exclude: Session ids to skip (the tunnel's own forks).
        codex_home: Override for ~/.codex (mainly for tests).

    Returns:
        Path of the newest matching rollout, or None.
    """
    root = sessions_dir(codex_home)
    if not root.is_dir():
        return None
    target = Path(project_dir).resolve()
    candidates = [
        p
        for p in root.glob("*/*/*/rollout-*.jsonl")
        if rollout_session_id(p) and rollout_session_id(p) not in exclude
    ]
    candidates.sort(key=_safe_mtime, reverse=True)
    for path in candidates:
        if _is_tunnel_fork(path):
            continue
        cwd = rollout_cwd(path)
        if not cwd:
            continue
        try:
            if Path(cwd).resolve() != target:
                continue
        except (OSError, RuntimeError):
            # RuntimeError: a symlink loop in the recorded cwd (Python 3.11/
            # 3.12). One malformed rollout must not crash discovery.
            continue
        if is_valid_session(path):
            return path
    return None


def fork_codex_session(expert_file: Path) -> tuple[str, Path]:
    """Fork a codex session by copying its rollout under a fresh id.

    Rewrites the meta line's session id (modern ``session_meta`` envelope or
    legacy top-level ``id``) and stamps ``forked_from_id`` /
    ``parent_thread_id`` — the same provenance fields codex's own ``fork``
    writes. The copy lands in the SAME codex home as the expert file (under
    today's date dir), so ``codex exec resume`` with that ``CODEX_HOME``
    finds it; the expert's file is never modified.

    Args:
        expert_file: Rollout file of the session to fork.

    Returns:
        (new_session_id, new_rollout_path).

    Raises:
        ValueError: The file is not a parseable codex rollout.
        OSError: The copy could not be written.
    """
    # Stable snapshot: never copy a torn record from an active session.
    lines = _read_stable_lines(expert_file)
    if not lines:
        raise ValueError(f"Empty rollout file: {expert_file}")
    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unparseable rollout meta line: {expert_file}") from exc
    # json.loads happily returns null/list/scalar meta lines; anything but an
    # object is an invalid rollout, reported as the documented ValueError.
    if not isinstance(meta, dict):
        raise ValueError(f"Unrecognized rollout meta line: {expert_file}")

    # Resolve the expert's (parent) id ONCE, before any stamping: a retry
    # must never mistake a candidate fork id for the parent.
    payload = meta.get("payload")
    modern = meta.get("type") == "session_meta" and isinstance(payload, dict)
    if modern:
        meta_id = payload.get("id")
    elif "id" in meta:
        meta_id = meta.get("id")
    else:
        raise ValueError(f"Unrecognized rollout meta line: {expert_file}")
    old_id = (
        meta_id if isinstance(meta_id, str) and meta_id
        else rollout_session_id(expert_file)
    )

    home = codex_home_for(expert_file)
    if home is None:
        raise ValueError(f"Not under a codex sessions tree: {expert_file}")
    day_dir = sessions_dir(home) / time.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    # An id collision is astronomically unlikely, but never clobber an
    # existing rollout: create exclusively and retry once under a fresh id.
    # Each attempt stamps a FRESH deep copy of the original meta line —
    # dict(meta) would share the nested payload, letting a first attempt's
    # mutation leak its candidate id into the second attempt's provenance.
    for _ in range(2):
        new_id = uuid7()
        stamped = _stamp_fork_meta(json.loads(lines[0]), new_id, old_id)
        ts = time.strftime("%Y-%m-%dT%H-%M-%S")
        dest = day_dir / f"rollout-{ts}-{new_id}.jsonl"
        body = "\n".join([json.dumps(stamped), *lines[1:]]) + "\n"
        # Write to an exclusive temp file, then publish with os.link — which
        # FAILS (never overwrites) if `dest` already exists. This is the
        # atomic no-clobber primitive: unlike os.replace (which clobbers a
        # dest that appears in a TOCTOU window), a concurrent creator of the
        # same id can never be silently overwritten, and a half-written
        # rollout is never discoverable (readers see absence or the linked
        # complete file). temp + dest share the day dir (same filesystem).
        tmp = day_dir / f".rollout-{new_id}.tmp"
        try:
            with open(tmp, "x", encoding="utf-8") as f:
                f.write(body)
        except FileExistsError:
            continue
        try:
            os.link(tmp, dest)  # fails if dest exists — never clobbers
        except FileExistsError:
            continue  # id collided — retry under a new id
        finally:
            tmp.unlink(missing_ok=True)  # drop the temp name either way
        return new_id, dest
    raise OSError(f"Could not allocate a fork rollout in {day_dir}")


def _stamp_fork_meta(
    meta: dict[str, Any], new_id: str, old_id: str
) -> dict[str, Any]:
    """Rewrite a rollout meta line's id + provenance fields for a fork.

    Both formats get ``forked_from_id`` AND ``parent_thread_id`` (the fields
    codex's own fork stamps), per the documented contract. ``old_id`` is
    the caller-resolved parent id — never re-derived here.
    """
    payload = meta.get("payload")
    if meta.get("type") == "session_meta" and isinstance(payload, dict):
        payload["id"] = new_id
        payload["forked_from_id"] = old_id
        payload["parent_thread_id"] = old_id
        payload[TUNNEL_FORK_KEY] = True
    else:  # legacy pre-envelope rollouts (validated by the caller)
        meta["id"] = new_id
        meta["forked_from_id"] = old_id
        meta["parent_thread_id"] = old_id
        meta[TUNNEL_FORK_KEY] = True
    return meta


def count_codex_turns(session_file: Path) -> int:
    """Number of real user turns in a codex rollout.

    Counts ``response_item`` user messages, skipping the system-injected
    blocks codex records as user input (instructions, environment context).
    """
    turns = 0
    try:
        # errors="replace": one bad byte must not crash `forks`/`status`;
        # a mangled line just fails its JSON parse and is skipped.
        with open(session_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Runtime rollouts may hold null/non-object lines, payloads,
                # content lists, or text values — skip, never crash.
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                if (
                    payload.get("type") != "message"
                    or payload.get("role") != "user"
                ):
                    continue
                content = payload.get("content")
                if not isinstance(content, list):
                    continue
                text = "".join(
                    str(blk.get("text") or "")
                    for blk in content
                    if isinstance(blk, dict)
                ).lstrip()
                if text.startswith(_SYNTHETIC_PREFIXES):
                    continue
                turns += 1
    except OSError:
        return 0
    return turns
