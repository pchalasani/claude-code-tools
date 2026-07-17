"""Resolution and conversion service behind the ``aichat port`` CLI.

Keeps the Click command in :mod:`claude_code_tools.aichat` as thin
wiring: session lookup (by id, name, filename fragment, or direct
path), source-agent detection, ambiguity handling, conversion-error
mapping, and port-result construction all live here. Non-path
lookups are delegated to the shared resolver in
:mod:`claude_code_tools.resolve_session` for both agents.

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
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from claude_code_tools.resolve_session import ResolveResult

from claude_code_tools.session_utils import (
    detect_agent_from_content,
    detect_agent_from_path,
    get_claude_home,
    get_codex_home,
    is_valid_session,
)

_MAX_AMBIGUITY_LINES = 25
_MAX_AMBIGUITY_NAME_CHARS = 60


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
    """Outcome of porting a session to the other agent.

    Attributes:
        new_session_id: The freshly generated target session id.
        output_file: Path of the synthesized target session file.
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


def _resolve_query_for_agent(
    query: str, agent: str, home: Optional[str]
) -> "Optional[ResolveResult]":
    """Resolve a query through the shared resolver for one agent.

    Expected resolver errors (empty query, missing or unreadable
    home) are treated as "no match for this agent" so a broken codex
    home never blocks a valid claude lookup, mirroring the tolerant
    behavior of the old glob path. A corrupt or incomplete Codex
    state database additionally degrades to direct on-disk rollout
    enumeration (``fallback_on_database_error``), so valid rollouts
    still port during database damage or migration.

    Args:
        query: Session id, name, or filename fragment.
        agent: ``"claude"`` or ``"codex"``.
        home: Optional custom home directory for the agent.

    Returns:
        A ``ResolveResult`` or None when resolution failed outright.
    """
    from typing import cast

    from claude_code_tools.resolve_session import (
        Agent,
        ResolverError,
        resolve,
    )

    try:
        return resolve(
            query,
            cast(Agent, agent),
            home,
            fallback_on_database_error=True,
        )
    except ResolverError:
        return None


def _ambiguity_error(
    session: str, results: "list[ResolveResult]"
) -> PortSessionError:
    """Build the multi-candidate rejection error for a port lookup.

    Args:
        session: The original session query.
        results: Non-empty resolver results (single or ambiguous).

    Returns:
        A user-facing error listing every candidate from both agents.
    """
    records = [record for result in results for record in result.records]
    records.sort(key=lambda record: record._modified_timestamp, reverse=True)
    total = sum(
        1 if result.kind == "single" else result.match_count
        for result in results
    )
    lines = []
    for record in records[:_MAX_AMBIGUITY_LINES]:
        name = record.name or ""
        # Codex auto-titles can be entire prompts: keep lines readable.
        name = " ".join(name.split())
        if len(name) > _MAX_AMBIGUITY_NAME_CHARS:
            name = name[: _MAX_AMBIGUITY_NAME_CHARS - 3] + "..."
        name_part = f" ({name})" if name else ""
        lines.append(
            f"  [{record.agent}] {record.session_id}{name_part}"
            f"  modified {record.modified}"
        )
    remaining = total - len(records[:_MAX_AMBIGUITY_LINES])
    if remaining > 0:
        lines.append(f"  ... and {remaining} more")
    listing = "\n".join(lines)
    return PortSessionError(
        f"Ambiguous session '{session}' matches {total} sessions:\n"
        f"{listing}\n"
        "Use a longer unique id, the exact session name, or the "
        "full file path."
    )


def resolve_port_session(
    session: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> ResolvedSession:
    """Resolve a session query to a session file + source agent.

    An existing file path is classified content-first (see
    :func:`_detect_direct_path_agent`). Any other query goes through
    the shared resolver (:mod:`claude_code_tools.resolve_session`)
    against BOTH agent homes, so full ids, id prefixes and
    substrings, session names, and rollout filename fragments all
    work. The query must resolve to exactly one session overall;
    anything ambiguous — within one agent or across the two — is
    rejected with the full candidate list instead of silently
    porting an arbitrary file.

    Args:
        session: Session id (full or partial), session name,
            filename fragment, or session file path.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Returns:
        The resolved session file with its detected agent.

    Raises:
        PortSessionError: When the session cannot be found, is
            ambiguous, or its agent cannot be detected.
    """
    # Arbitrary session names are legitimate queries here, and some
    # (e.g. "~nonexistent-user" or names with NUL bytes) make path
    # probing itself raise. Treat any such input as a non-path query
    # and fall through to resolver lookup instead of crashing.
    try:
        input_path: Optional[Path] = Path(session).expanduser()
        is_direct_file = input_path.is_file()
    except (OSError, RuntimeError, ValueError):
        input_path = None
        is_direct_file = False
    if is_direct_file and input_path is not None:
        agent = _detect_direct_path_agent(
            input_path, claude_home, codex_home
        )
        if agent not in ("claude", "codex"):
            raise PortSessionError(
                f"Could not detect agent for session file: {input_path}"
            )
        return ResolvedSession(agent=agent, session_file=input_path)

    results: "list[ResolveResult]" = []
    for agent_name, home in (("claude", claude_home), ("codex", codex_home)):
        result = _resolve_query_for_agent(session, agent_name, home)
        if result is not None and result.kind != "not_found":
            results.append(result)

    if not results:
        raise PortSessionError(
            f"Session not found in Claude or Codex homes: {session}"
        )
    if len(results) == 1 and results[0].kind == "single":
        record = results[0].records[0]
        return ResolvedSession(
            agent=record.agent, session_file=Path(record.session_file)
        )
    raise _ambiguity_error(session, results)


def _read_output_cwd(output_file: Path) -> str:
    """Read the session cwd recorded on the first output line.

    Claude session lines carry ``cwd`` at the top level; Codex
    rollouts carry it inside the session_meta ``payload``. Both
    placements are checked (top level first).

    Args:
        output_file: Path of the freshly written session file.

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
    if isinstance(cwd, str):
        return cwd
    payload = first_line.get("payload")
    if isinstance(payload, dict):
        cwd = payload.get("cwd")
        if isinstance(cwd, str):
            return cwd
    return ""


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


def port_claude_session(
    session_file: Union[str, Path],
    codex_home: Optional[str] = None,
) -> PortResult:
    """Port a resolved Claude session and build the CLI-facing result.

    Args:
        session_file: Path to the Claude session JSONL file.
        codex_home: Optional custom Codex home directory.

    Returns:
        The port outcome, including the exact resume hint.

    Raises:
        PortSessionError: On any expected conversion failure
            (missing/empty session, filesystem or encoding errors).
    """
    from claude_code_tools.port_claude_to_codex import (
        port_claude_session_to_codex,
    )

    try:
        new_id, out_path = port_claude_session_to_codex(
            session_file, codex_home=codex_home
        )
    except (ValueError, OSError, UnicodeError) as e:
        raise PortSessionError(str(e)) from e

    cwd = _read_output_cwd(out_path)
    resume_hint = f"cd {shlex.quote(cwd)} && codex resume {new_id}"
    return PortResult(
        new_session_id=new_id,
        output_file=out_path,
        cwd=cwd,
        resume_hint=resume_hint,
    )
