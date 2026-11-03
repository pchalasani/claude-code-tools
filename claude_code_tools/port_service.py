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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from claude_code_tools.resolve_session import SessionRecord

from claude_code_tools.session_resolution import (
    SessionQueryAmbiguity,
    SessionQueryError,
    resolve_session_query,
)


class PortSessionError(Exception):
    """A user-facing failure while resolving or porting a session.

    The exception message is suitable for printing directly (the CLI
    prefixes it with ``Error:`` and exits non-zero).
    """


class PortAmbiguityError(PortSessionError):
    """A port query with more than one valid source session.

    Carries the candidates alongside the printable message so the CLI
    can offer them interactively instead of only reporting them.

    Attributes:
        query: Original user query.
        records: Newest matching records available for selection.
        match_count: Total matches, including any omitted by the cap.
    """

    def __init__(
        self,
        query: str,
        records: "tuple[SessionRecord, ...]",
        match_count: int,
        message: str,
    ) -> None:
        """Initialize an ambiguity with structured candidate data.

        Args:
            query: Original user query.
            records: Newest matching records available for selection.
            match_count: Total matches before the display cap.
            message: User-facing message listing the candidates.
        """
        super().__init__(message)
        self.query = query
        self.records = records
        self.match_count = match_count


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
        target_agent: Agent the new session belongs to (``"claude"``
            or ``"codex"``); a Codex session re-targeted at another
            provider stays ``"codex"``.
        provider: Codex model provider of the new session ("" for a
            Claude target).
        notes: Extra lines for the user, e.g. what was dropped.
    """

    new_session_id: str
    output_file: Path
    cwd: str
    resume_hint: str
    target_agent: str = ""
    provider: str = ""
    notes: list[str] = field(default_factory=list)


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
        PortAmbiguityError: When the query matches more than one
            session; carries the candidates for interactive choice.
        PortSessionError: When the session cannot be found or its
            agent cannot be detected.
    """
    try:
        resolved = resolve_session_query(
            session,
            claude_home=claude_home,
            codex_home=codex_home,
        )
    except SessionQueryAmbiguity as error:
        raise PortAmbiguityError(
            error.query,
            error.records,
            error.match_count,
            str(error),
        ) from error
    except SessionQueryError as error:
        if not session.strip() or str(error).startswith("Session not found:"):
            raise PortSessionError(
                f"Session not found in Claude or Codex homes: {session}"
            ) from error
        raise PortSessionError(str(error)) from error
    return ResolvedSession(
        agent=resolved.agent,
        session_file=resolved.session_file,
    )


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
        target_agent="claude",
    )


def _codex_resume_hint(
    cwd: str, session_id: str, provider: str, model: str = ""
) -> str:
    """Shell command that resumes a Codex session under ``provider``.

    The built-in ``openai`` provider needs no flag; any other provider
    is named with ``-c model_provider=...`` (it must exist in the Codex
    config, or be supplied the same way, e.g. by a wrapper).

    Args:
        cwd: Directory to resume from.
        session_id: Codex session id.
        provider: Model provider the session is bound to.
        model: Model to resume with ("" keeps the session's own).

    Returns:
        A ``cd ... && codex resume`` command line.
    """
    flag = "" if provider == "openai" else (
        f"-c model_provider={shlex.quote(provider)} ")
    if model:
        flag += f"-m {shlex.quote(model)} "
    return f"cd {shlex.quote(cwd)} && codex {flag}resume {session_id}"


def port_claude_session(
    session_file: Union[str, Path],
    codex_home: Optional[str] = None,
    provider: str = "openai",
    model: str = "",
) -> PortResult:
    """Port a resolved Claude session and build the CLI-facing result.

    Args:
        session_file: Path to the Claude session JSONL file.
        codex_home: Optional custom Codex home directory.
        provider: Codex model provider for the new session.
        model: Model for the resume command ("" for Codex's default).

    Returns:
        The port outcome, including the exact resume hint.

    Raises:
        PortSessionError: On any expected conversion failure
            (missing/empty session, filesystem or encoding errors,
            an invalid provider id).
    """
    from claude_code_tools.port_claude_to_codex import (
        port_claude_session_to_codex,
    )
    from claude_code_tools.port_codex_provider import validate_provider

    try:
        validate_provider(provider)
        new_id, out_path = port_claude_session_to_codex(
            session_file, codex_home=codex_home, model_provider=provider
        )
    except (ValueError, OSError, UnicodeError) as e:
        raise PortSessionError(str(e)) from e

    cwd = _read_output_cwd(out_path)
    return PortResult(
        new_session_id=new_id,
        output_file=out_path,
        cwd=cwd,
        resume_hint=_codex_resume_hint(cwd, new_id, provider, model),
        target_agent="codex",
        provider=provider,
    )


def retarget_codex_session(
    session_file: Union[str, Path],
    provider: str,
    codex_home: Optional[str] = None,
    model: str = "",
) -> PortResult:
    """Copy a Codex session onto another model provider (Codex -> Codex).

    Args:
        session_file: Path to the source Codex rollout.
        provider: Model provider for the new session.
        codex_home: Codex home for the new rollout; defaults to the
            source rollout's own home.
        model: Model for the resume command. Without it Codex resumes
            with the session's last model, which belongs to the old
            provider, so a note says to pass ``-m``.

    Returns:
        The port outcome, with what was dropped listed in ``notes``.

    Raises:
        PortSessionError: On any expected failure (invalid provider,
            same provider, missing or malformed file, filesystem
            errors).
    """
    from claude_code_tools.port_codex_provider import (
        retarget_codex_session as _retarget,
    )

    try:
        new_id, out_path, stats = _retarget(
            session_file, provider, codex_home=codex_home)
    except (ValueError, OSError, UnicodeError) as e:
        raise PortSessionError(str(e)) from e

    cwd = _read_output_cwd(out_path)
    notes = [f"Provider: {stats.source_provider} -> {provider}"]
    if stats.dropped:
        dropped = ", ".join(f"{n} {kind}" for kind, n in sorted(stats.dropped.items()))
        notes.append(f"Dropped provider-specific items: {dropped}")
    if not model and stats.last_model:
        notes.append(
            f"Codex resumes with the session's last model ({stats.last_model}). "
            f"If {provider} does not serve it, add -m MODEL to the command "
            "(or pass --model to aichat port).")
        if stats.dropped.get("compaction"):
            notes.append(
                "Earlier context that was only in an encrypted compaction "
                "summary is not visible to the new provider.")
    return PortResult(
        new_session_id=new_id,
        output_file=out_path,
        cwd=cwd,
        resume_hint=_codex_resume_hint(cwd, new_id, provider, model),
        target_agent="codex",
        provider=provider,
        notes=notes,
    )
