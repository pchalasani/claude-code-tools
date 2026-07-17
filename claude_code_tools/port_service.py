"""Resolution and conversion service behind the ``aichat port`` CLI.

Keeps the Click command in :mod:`claude_code_tools.aichat` as thin
wiring: session lookup (by id or direct path), source-agent
detection, ambiguity handling, conversion-error mapping, and
port-result construction all live here.

Direct file paths are classified CONTENT-FIRST: the file's JSONL
records decide which agent it belongs to, and the configured home
directories are only a fallback when the content is genuinely
inconclusive. A location-derived Claude classification is further
validated with :func:`~claude_code_tools.session_utils.is_valid_session`,
so a malformed file (or a Codex rollout copied under the Claude home)
is never misreported as a portable Claude session.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from claude_code_tools.session_utils import (
    detect_agent_from_content,
    detect_agent_from_path,
    find_matching_session_files,
    get_claude_home,
    get_codex_home,
    is_valid_session,
)


class PortSessionError(Exception):
    """A user-facing failure while resolving or porting a session.

    The exception message is suitable for printing directly (the CLI
    prefixes it with ``Error:`` and exits non-zero).
    """


@dataclass
class ResolvedSession:
    """A session file together with its detected source agent.

    Attributes:
        agent: Detected source agent, ``"claude"`` or ``"codex"``.
        session_file: Path to the session JSONL file.
    """

    agent: str
    session_file: Path


@dataclass
class PortResult:
    """Outcome of porting a Codex session to Claude Code.

    Attributes:
        new_session_id: The freshly generated Claude session id.
        output_file: Path of the synthesized Claude session file.
        cwd: The ported session's working directory ("" if unknown).
        resume_hint: Exact shell command to resume the new session.
    """

    new_session_id: str
    output_file: Path
    cwd: str
    resume_hint: str


def _detect_direct_path_agent(
    session_file: Path,
    claude_home: Optional[str],
    codex_home: Optional[str],
) -> Optional[str]:
    """Detect the source agent of an explicitly given session file.

    Content is authoritative: the file's own JSONL records are
    sniffed first, so a Codex rollout copied under the Claude home
    (or vice versa) is classified by what it IS, not where it sits.
    Only when content detection is inconclusive does the file's
    location decide -- and a location-derived Claude classification
    must additionally pass :func:`is_valid_session`, mirroring the
    validation applied to id-based lookups.

    Args:
        session_file: Existing session file path.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Returns:
        ``"claude"``, ``"codex"``, or None when undetectable.
    """
    # Stream the ENTIRE file (memory-bounded): a rollout whose
    # leading records are unrecognized or oversized must still be
    # classified by its later recognizable records.
    agent = detect_agent_from_content(session_file, max_lines=None)
    if agent is not None:
        return agent

    # Content inconclusive: fall back to the file's location
    # (configured homes first, then default path conventions).
    resolved = session_file.resolve()
    claude_root = get_claude_home(claude_home).resolve()
    codex_root = get_codex_home(codex_home).resolve()
    if resolved.is_relative_to(claude_root):
        located: Optional[str] = "claude"
    elif resolved.is_relative_to(codex_root):
        located = "codex"
    else:
        located = detect_agent_from_path(session_file)

    # A location-only Claude claim must be backed by valid content;
    # otherwise a malformed file inside the Claude home would be
    # misreported as portable and silently exit 0.
    if located == "claude" and not is_valid_session(session_file):
        return None
    return located


def resolve_port_session(
    session: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> ResolvedSession:
    """Resolve a session id or file path to a file + source agent.

    An existing file path is classified content-first (see
    :func:`_detect_direct_path_agent`). Otherwise the identifier is
    matched LITERALLY (glob metacharacters escaped) against both
    homes and must match exactly one validated session; ambiguous
    partial ids are rejected with the full match list instead of
    silently porting an arbitrary file.

    Args:
        session: Session id (full or partial) or session file path.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Returns:
        The resolved session file with its detected agent.

    Raises:
        PortSessionError: When the session cannot be found, is
            ambiguous, or its agent cannot be detected.
    """
    input_path = Path(session).expanduser()
    if input_path.is_file():
        agent = _detect_direct_path_agent(
            input_path, claude_home, codex_home
        )
        if agent not in ("claude", "codex"):
            raise PortSessionError(
                f"Could not detect agent for session file: {input_path}"
            )
        return ResolvedSession(agent=agent, session_file=input_path)

    matches = find_matching_session_files(
        session, claude_home=claude_home, codex_home=codex_home
    )
    if not matches:
        raise PortSessionError(
            f"Session not found in Claude or Codex homes: {session}"
        )
    if len(matches) > 1:
        listing = "\n".join(
            f"  [{agent}] {path}" for agent, path in matches
        )
        raise PortSessionError(
            f"Ambiguous session id '{session}' matches "
            f"{len(matches)} session files:\n{listing}\n"
            "Use a longer unique id or the full file path."
        )
    agent, session_file = matches[0]
    return ResolvedSession(agent=agent, session_file=session_file)


def _read_output_cwd(output_file: Path) -> str:
    """Read the session cwd recorded on the first output line.

    Args:
        output_file: Path of the freshly written Claude session file.

    Returns:
        The cwd string, or "" when unavailable.
    """
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            first_line = json.loads(f.readline())
    except (OSError, ValueError):
        return ""
    if not isinstance(first_line, dict):
        return ""
    cwd = first_line.get("cwd")
    return cwd if isinstance(cwd, str) else ""


def port_codex_session(
    session_file: Union[str, Path],
    claude_home: Optional[str] = None,
) -> PortResult:
    """Port a resolved Codex session and build the CLI-facing result.

    Args:
        session_file: Path to the Codex rollout JSONL file.
        claude_home: Optional custom Claude home directory.

    Returns:
        The port outcome, including the exact resume hint.

    Raises:
        PortSessionError: On any expected conversion failure
            (missing/empty session, filesystem or encoding errors).
    """
    from claude_code_tools.port_codex_to_claude import (
        port_codex_session_to_claude,
    )

    try:
        new_id, out_path = port_codex_session_to_claude(
            session_file, claude_home=claude_home
        )
    except (ValueError, OSError, UnicodeError) as e:
        raise PortSessionError(str(e)) from e

    cwd = _read_output_cwd(out_path)
    resume_hint = f"cd {shlex.quote(cwd)} && claude --resume {new_id}"
    return PortResult(
        new_session_id=new_id,
        output_file=out_path,
        cwd=cwd,
        resume_hint=resume_hint,
    )
