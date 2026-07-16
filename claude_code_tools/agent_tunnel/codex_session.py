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


def codex_home_for(rollout_file: Path) -> Optional[Path]:
    """The CODEX_HOME a rollout file lives under (parent of ``sessions/``)."""
    text = str(rollout_file)
    if "/sessions/" not in text:
        return None
    return Path(text.split("/sessions/")[0])


def sessions_dir(codex_home: Optional[Path] = None) -> Path:
    """The dated rollout tree for a codex home (default ``~/.codex``)."""
    home = codex_home or get_codex_home()
    return Path(home) / "sessions"


def find_codex_session_file(
    session_id: str, codex_home: Optional[Path] = None
) -> Optional[Path]:
    """Locate a rollout file by exact session id (newest first)."""
    root = sessions_dir(codex_home)
    if not root.is_dir():
        return None
    hits = list(root.glob(f"*/*/*/rollout-*-{session_id}.jsonl"))
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


def _first_line(path: Path) -> Optional[dict[str, Any]]:
    """Parsed first JSONL line of a rollout ('meta' line), or None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        return json.loads(line) if line else None
    except (OSError, json.JSONDecodeError):
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


def find_latest_codex_session(
    project_dir: Path,
    exclude: set[str],
    codex_home: Optional[Path] = None,
    max_day_dirs: int = 45,
) -> Optional[Path]:
    """Newest valid codex session recorded in `project_dir`.

    Args:
        project_dir: Project the session must have run in (cwd match).
        exclude: Session ids to skip (the tunnel's own forks).
        codex_home: Override for ~/.codex (mainly for tests).
        max_day_dirs: How many day directories (newest first) to scan before
            giving up, so an old giant history isn't walked end to end.

    Returns:
        Path of the newest matching rollout, or None.
    """
    root = sessions_dir(codex_home)
    if not root.is_dir():
        return None
    target = Path(project_dir).resolve()
    day_dirs = sorted(root.glob("*/*/*"), key=lambda p: str(p), reverse=True)
    for day_dir in day_dirs[:max_day_dirs]:
        if not day_dir.is_dir():
            continue
        rollouts = [
            p
            for p in day_dir.glob("rollout-*.jsonl")
            if rollout_session_id(p) and rollout_session_id(p) not in exclude
        ]
        rollouts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in rollouts:
            cwd = rollout_cwd(path)
            if not cwd:
                continue
            try:
                if Path(cwd).resolve() != target:
                    continue
            except OSError:
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
    lines = expert_file.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"Empty rollout file: {expert_file}")
    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unparseable rollout meta line: {expert_file}") from exc

    old_id = rollout_session_id(expert_file)
    new_id = uuid7()
    payload = meta.get("payload")
    if meta.get("type") == "session_meta" and isinstance(payload, dict):
        old_id = payload.get("id") or old_id
        payload["id"] = new_id
        payload["forked_from_id"] = old_id
        payload["parent_thread_id"] = old_id
    elif "id" in meta:  # legacy pre-envelope rollouts
        old_id = meta.get("id") or old_id
        meta["id"] = new_id
        meta["forked_from_id"] = old_id
    else:
        raise ValueError(f"Unrecognized rollout meta line: {expert_file}")
    lines[0] = json.dumps(meta)

    home = codex_home_for(expert_file)
    if home is None:
        raise ValueError(f"Not under a codex sessions tree: {expert_file}")
    day_dir = sessions_dir(home) / time.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    dest = day_dir / f"rollout-{ts}-{new_id}.jsonl"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new_id, dest


def count_codex_turns(session_file: Path) -> int:
    """Number of real user turns in a codex rollout.

    Counts ``response_item`` user messages, skipping the system-injected
    blocks codex records as user input (instructions, environment context).
    """
    turns = 0
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload") or {}
                if (
                    payload.get("type") != "message"
                    or payload.get("role") != "user"
                ):
                    continue
                text = "".join(
                    blk.get("text", "")
                    for blk in payload.get("content") or []
                    if isinstance(blk, dict)
                ).lstrip()
                if text.startswith(_SYNTHETIC_PREFIXES):
                    continue
                turns += 1
    except OSError:
        return 0
    return turns
