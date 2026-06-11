"""Expert-session discovery and fork-transcript parsing.

Claude Code writes session transcripts to
``~/.claude/projects/<encoded-project-dir>/<session-id>.jsonl``. This module
finds the expert session to fork from, and reads answers out of fork
transcripts (the tmux backend never scrapes the terminal screen).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from claude_code_tools.session_utils import (
    encode_claude_project_path,
    is_valid_session,
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def transcript_dir(
    project_dir: Path, claude_home: Optional[Path] = None
) -> Path:
    """Return the transcript directory for a project.

    Args:
        project_dir: Absolute path of the project.
        claude_home: Override for ~/.claude (mainly for tests).
    """
    home = claude_home or (Path.home() / ".claude")
    encoded = encode_claude_project_path(str(project_dir))
    return home / "projects" / encoded


def list_session_files(
    project_dir: Path, claude_home: Optional[Path] = None
) -> list[Path]:
    """All UUID-named session transcripts for a project."""
    tdir = transcript_dir(project_dir, claude_home)
    if not tdir.is_dir():
        return []
    return [p for p in tdir.glob("*.jsonl") if UUID_RE.match(p.stem)]


def find_latest_session(
    project_dir: Path,
    exclude: set[str],
    claude_home: Optional[Path] = None,
) -> Optional[Path]:
    """Newest valid session in a project, excluding given session ids.

    Args:
        project_dir: Project whose transcript dir is searched.
        exclude: Session ids to skip (the tunnel's own forks).
        claude_home: Override for ~/.claude (mainly for tests).

    Returns:
        Path of the newest valid transcript, or None.
    """
    candidates = [
        p
        for p in list_session_files(project_dir, claude_home)
        if p.stem not in exclude
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if is_valid_session(path):
            return path
    return None


def wait_for_new_session_file(
    project_dir: Path,
    before: set[str],
    exclude: set[str],
    deadline: float,
    claude_home: Optional[Path] = None,
    poll_s: float = 0.5,
) -> Optional[Path]:
    """Poll for a transcript file that did not exist before.

    Used by the tmux backend to learn the session id of a fresh fork: the
    forked transcript appears in the project dir once the fork records its
    first message.

    Args:
        project_dir: Project transcript dir to watch.
        before: Session ids (stems) present before the fork launched.
        exclude: Additional ids to ignore (known forks).
        deadline: time.time() value after which to give up.
        claude_home: Override for ~/.claude.
        poll_s: Poll interval in seconds.

    Returns:
        The new transcript path (newest if several appeared), or None.
    """
    skip = before | exclude
    while time.time() < deadline:
        fresh = [
            p
            for p in list_session_files(project_dir, claude_home)
            if p.stem not in skip
        ]
        if fresh:
            fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return fresh[0]
        time.sleep(poll_s)
    return None


def make_marker(question: str) -> str:
    """A short, searchable marker identifying a question in a transcript."""
    first_line = question.strip().splitlines()[0] if question.strip() else ""
    return first_line[:60]


def _iter_entries(session_file: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON entries, skipping blank/malformed lines."""
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _message_text(entry: dict[str, Any], role: str) -> str:
    """Plain text of a user/assistant entry ('' if none or wrong role)."""
    if entry.get("type") != role or entry.get("isSidechain"):
        return ""
    if entry.get("isMeta"):
        return ""
    message = entry.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            blk.get("text", "")
            for blk in content
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _has_block(entry: dict[str, Any], block_type: str) -> bool:
    """True if an assistant entry's content includes a block of a type."""
    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(blk, dict) and blk.get("type") == block_type
        for blk in content
    )


def extract_answer(
    session_file: Path, marker: str
) -> tuple[bool, str]:
    """Extract the assistant answer to the question matching `marker`.

    Scans the transcript for the LAST user entry whose text contains the
    marker, then joins the text of all assistant entries after it.

    Completion heuristic: at least one assistant text block exists after the
    question, and the final content-bearing entry is an assistant entry that
    carries text and is not a dangling tool_use (i.e. the model is not midway
    through a tool round-trip).

    Args:
        session_file: Fork transcript path.
        marker: Output of make_marker() for the question.

    Returns:
        (complete, answer_text); (False, "") when the question or any answer
        text has not appeared yet.
    """
    entries = list(_iter_entries(session_file))
    question_idx: Optional[int] = None
    for i, entry in enumerate(entries):
        text = _message_text(entry, "user")
        if text and marker and marker in text:
            question_idx = i
    if question_idx is None:
        return (False, "")

    answer_parts: list[str] = []
    last_content: Optional[dict[str, Any]] = None
    for entry in entries[question_idx + 1 :]:
        if entry.get("isSidechain") or entry.get("isMeta"):
            continue
        etype = entry.get("type")
        if etype not in ("user", "assistant"):
            continue
        last_content = entry
        text = _message_text(entry, "assistant")
        if text:
            answer_parts.append(text)

    if not answer_parts or last_content is None:
        return (False, "")
    complete = (
        last_content.get("type") == "assistant"
        and not _has_block(last_content, "tool_use")
        and bool(_message_text(last_content, "assistant"))
    )
    return (complete, "\n\n".join(answer_parts))
