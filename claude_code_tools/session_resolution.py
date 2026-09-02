"""Shared session-query resolution for command-line actions.

Direct paths are classified content-first: JSONL records identify what a
session is, while its location is only a fallback when content is
inconclusive. A location-derived Claude result must still be a valid session,
so malformed files and Codex rollouts copied into a Claude home are not
misclassified.

Symlinked in-home transcripts are only partially handled. One discovered link
to an external transcript is preserved and several are ambiguous, but exotic
topologies may resolve to the canonical target. This is a known, accepted
limitation because agent homes are not expected to contain such links.
Fast-candidate validation also retains legacy unbounded physical-line reads.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

if TYPE_CHECKING:
    from claude_code_tools.resolve_session import (
        ResolveResult,
        ResolverError,
        SessionRecord,
    )

from claude_code_tools.session_utils import (
    _iter_named_session_files,
    detect_agent_from_content,
    detect_agent_from_path,
    extract_cwd_from_session,
    extract_git_branch_claude,
    extract_session_metadata_codex,
    get_claude_home,
    get_codex_home,
    get_session_uuid,
    is_malformed_session,
    is_valid_session,
)

_ID_FRAGMENT_RE = re.compile(r"^[0-9a-fA-F-]{4,}$")
_FULL_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_FAST_PATH_CANDIDATES = 25
_MAX_AMBIGUITY_LINES = 25
_MAX_AMBIGUITY_NAME_CHARS = 60


class SessionQueryError(Exception):
    """A user-facing failure while resolving a session query."""


class SessionQueryAmbiguity(SessionQueryError):
    """A session query with several eligible matches.

    Attributes:
        query: Original user-supplied query.
        records: Newest-first candidates available for selection.
        match_count: Total matches, including candidates omitted by the cap.
        display_limit: Maximum candidates carried for interactive display.
    """

    def __init__(
        self,
        query: str,
        records: "tuple[SessionRecord, ...]",
        match_count: int,
        message: str,
    ) -> None:
        """Initialize a structured ambiguity.

        Args:
            query: Original user-supplied query.
            records: Newest-first selectable candidates.
            match_count: Total matches before display capping.
            message: Human-readable ambiguity listing.
        """
        super().__init__(message)
        self.query = query
        self.records = records
        self.match_count = match_count
        self.display_limit = _MAX_AMBIGUITY_LINES


@dataclass(frozen=True)
class ResolvedSessionQuery:
    """A resolved session file and metadata needed by CLI actions.

    Attributes:
        agent: ``"claude"`` or ``"codex"``.
        session_file: Absolute path to the selected transcript.
        directory: Project working directory recorded by the session.
        git_branch: Git branch recorded by the session, when available.
    """

    agent: str
    session_file: Path
    directory: str | None
    git_branch: str | None


@dataclass(frozen=True)
class _ValidatedFastCandidate:
    """A fast-path result paired with its content-confirmed identity."""

    resolved: ResolvedSessionQuery
    session_id: str


def _detect_direct_path_agent(
    session_file: Path,
    claude_home: Optional[str],
    codex_home: Optional[str],
) -> Optional[str]:
    """Detect the agent of an explicitly supplied session file.

    Content is authoritative, so a rollout copied beneath the other agent's
    home is classified by what it is rather than where it is stored. Location
    is used only when content detection is inconclusive, and a
    location-derived Claude classification must pass
    :func:`is_valid_session`, just as it must for id-based lookup.

    Args:
        session_file: Existing session file path.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Returns:
        ``"claude"``, ``"codex"``, or None when undetectable.
    """
    # Stream the entire file, while remaining memory-bounded. Recognizable
    # records may follow leading records that are unknown or oversized.
    detected = detect_agent_from_content(session_file, max_lines=None)
    if detected is not None:
        return detected

    # Content was inconclusive. Prefer configured homes before falling back
    # to the default path conventions.
    resolved = session_file.resolve()
    claude_root = get_claude_home(claude_home).resolve()
    codex_root = get_codex_home(codex_home).resolve()
    if resolved.is_relative_to(claude_root):
        located: Optional[str] = "claude"
    elif resolved.is_relative_to(codex_root):
        located = "codex"
    else:
        located = detect_agent_from_path(session_file)

    # A location alone cannot make malformed content a portable Claude
    # session, or a bad file beneath the Claude home could silently succeed.
    if located == "claude" and not is_valid_session(session_file):
        return None
    return located


def _resolve_query_for_agent(
    query: str,
    agent: str,
    home: Optional[str],
    errors: list["ResolverError"] | None = None,
) -> "Optional[ResolveResult]":
    """Resolve a query for one agent, tolerating expected resolver errors.

    A missing or unreadable home counts as no match for that agent, allowing
    the other agent to resolve successfully. Codex database failures also
    fall back to on-disk rollout enumeration. Callers that need to surface an
    operational failure when no agent matches can collect it in ``errors``.

    Args:
        query: Session id, name, or filename fragment.
        agent: ``"claude"`` or ``"codex"``.
        home: Optional custom home directory for the agent.
        errors: Optional destination for tolerated resolver errors.

    Returns:
        A resolver result, or None when resolution failed for this agent.
    """
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
    except ResolverError as error:
        if errors is not None:
            errors.append(error)
        return None


def ambiguity_candidates(
    results: "list[ResolveResult]",
) -> "tuple[tuple[SessionRecord, ...], int]":
    """Return the displayable candidates for an ambiguous query.

    Single source of truth for how ambiguous matches are ordered and
    capped, so a caller that RENDERS the candidates (an interactive
    picker) and one that only formats the message agree on which
    sessions they are talking about.

    Args:
        results: Non-empty resolver results from one or both agents.

    Returns:
        Newest-first candidates capped at ``_MAX_AMBIGUITY_LINES``,
        paired with the total match count before capping.
    """
    records = [record for result in results for record in result.records]
    records.sort(key=lambda record: record._modified_timestamp, reverse=True)
    total = sum(
        1 if result.kind == "single" else result.match_count for result in results
    )
    return tuple(records[:_MAX_AMBIGUITY_LINES]), total


def build_ambiguity_message(session: str, results: "list[ResolveResult]") -> str:
    """Build the shared multi-candidate ambiguity message.

    Args:
        session: Original session query.
        results: Non-empty resolver results from one or both agents.

    Returns:
        A user-facing message listing the matching candidates.
    """
    records, total = ambiguity_candidates(results)
    lines = []
    for record in records:
        # Codex auto-titles can contain entire prompts. Normalize whitespace
        # and truncate them to keep every candidate on a readable line.
        name = " ".join((record.name or "").split())
        if len(name) > _MAX_AMBIGUITY_NAME_CHARS:
            name = name[: _MAX_AMBIGUITY_NAME_CHARS - 3] + "..."
        name_part = f" ({name})" if name else ""
        lines.append(
            f"  [{record.agent}] {record.session_id}{name_part}"
            f"  modified {record.modified}"
        )
    remaining = total - len(records)
    if remaining > 0:
        lines.append(f"  ... and {remaining} more")
    listing = "\n".join(lines)
    return (
        f"Ambiguous session '{session}' matches {total} sessions:\n"
        f"{listing}\n"
        "Use a longer unique id, the exact session name, or the "
        "full file path."
    )


def _metadata_for_path(agent: str, session_file: Path) -> tuple[str | None, str | None]:
    """Read the cwd and branch metadata for a selected session file."""
    if agent == "claude":
        return (
            extract_cwd_from_session(session_file, agent="claude"),
            extract_git_branch_claude(session_file),
        )
    metadata = extract_session_metadata_codex(session_file)
    if not metadata:
        return None, None
    cwd = metadata.get("cwd")
    branch = metadata.get("branch")
    return (
        cwd if isinstance(cwd, str) and cwd else None,
        branch if isinstance(branch, str) and branch else None,
    )


def resolved_session_from_record(
    record: "SessionRecord",
) -> ResolvedSessionQuery:
    """Build action metadata for a user-selected resolver record.

    Args:
        record: Candidate selected from a structured ambiguity.

    Returns:
        The resolved session metadata used by direct ``aichat`` actions.
    """
    session_file = Path(record.session_file).absolute()
    directory, branch = _metadata_for_path(record.agent, session_file)
    return ResolvedSessionQuery(
        agent=record.agent,
        session_file=session_file,
        directory=record.directory or directory,
        git_branch=branch,
    )


def _session_id_from_content(agent: str, session_file: Path) -> str | None:
    """Extract a session identity from authoritative transcript content."""
    import json

    try:
        with session_file.open("r", encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(record, dict):
                    continue
                if agent == "claude":
                    session_id = record.get("sessionId")
                elif record.get("type") == "session_meta":
                    payload = record.get("payload")
                    session_id = (
                        payload.get("id") if isinstance(payload, dict) else None
                    )
                elif "timestamp" in record and (
                    isinstance(record.get("git"), dict)
                    or isinstance(record.get("instructions"), str)
                ):
                    session_id = record.get("id")
                else:
                    session_id = None
                if isinstance(session_id, str) and session_id.strip():
                    return session_id.strip()
    except (OSError, UnicodeError):
        return None
    return None


def _validated_fast_candidates(
    session: str,
    agent: str | None,
    claude_home: str | None,
    codex_home: str | None,
) -> list[_ValidatedFastCandidate] | None:
    """Return bounded, resolver-eligible filename matches.

    None means the raw candidate count exceeded the validation bound.
    """
    raw_candidates: list[tuple[str, Path]] = []
    try:
        for candidate in _iter_named_session_files(
            session, claude_home=claude_home, codex_home=codex_home
        ):
            if agent is not None and candidate[0] != agent:
                continue
            raw_candidates.append(candidate)
            if len(raw_candidates) > _MAX_FAST_PATH_CANDIDATES:
                return None
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return []

    from claude_code_tools.find_claude_session import is_sidechain_session
    from claude_code_tools.resolve_session import _is_legacy_codex_rollout

    validated: dict[Path, _ValidatedFastCandidate] = {}
    lowered_query = session.casefold()
    for candidate_agent, candidate_path in raw_candidates:
        try:
            discovered_path = candidate_path.absolute()
            validation_path = candidate_path.resolve()
            if candidate_agent == "claude":
                if is_malformed_session(validation_path) or is_sidechain_session(
                    validation_path
                ):
                    continue
                directory, branch = _metadata_for_path(candidate_agent, validation_path)
                if not directory:
                    continue
            else:
                if not is_valid_session(validation_path) and not (
                    _is_legacy_codex_rollout(validation_path)
                ):
                    continue
                metadata = extract_session_metadata_codex(validation_path)
                if metadata is None:
                    continue
                cwd = metadata.get("cwd")
                branch_value = metadata.get("branch")
                directory = cwd if isinstance(cwd, str) and cwd else None
                branch = (
                    branch_value
                    if isinstance(branch_value, str) and branch_value
                    else None
                )
            content_id = _session_id_from_content(candidate_agent, validation_path)
            if content_id is None or lowered_query not in content_id.casefold():
                continue
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            continue
        validated[validation_path] = _ValidatedFastCandidate(
            resolved=ResolvedSessionQuery(
                candidate_agent, discovered_path, directory, branch
            ),
            session_id=content_id,
        )
    return list(validated.values())


def _claude_has_exact_name(query: str, home: str | None) -> bool:
    """Return whether a Claude transcript has the exact custom name."""
    from claude_code_tools.find_claude_session import get_custom_title

    projects = get_claude_home(home) / "projects"
    if not projects.is_dir():
        return False
    lowered = query.casefold()
    try:
        paths = projects.glob("*/*.jsonl")
        for path in paths:
            title = get_custom_title(
                path.stem,
                "",
                claude_home=home,
                session_file=path,
            )
            if title.casefold() == lowered:
                return True
    except (OSError, RuntimeError, ValueError):
        return False
    return False


def _codex_has_exact_name(query: str, home: str | None) -> bool | None:
    """Return whether Codex has an exact name, or None if checking failed."""
    from claude_code_tools.resolve_session import (
        ResolverError,
        _codex_state_database,
    )
    from claude_code_tools.resolve_session_names import codex_thread_names

    root = get_codex_home(home)
    lowered = query.casefold()
    try:
        explicit_names = codex_thread_names(root)
        database = _codex_state_database(root)
    except (
        OSError,
        ResolverError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        return None
    if any(name.casefold() == lowered for name in explicit_names.values()):
        return True
    if database is None:
        return False
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT id, title FROM threads WHERE title IS NOT NULL"
            )
            return any(
                isinstance(session_id, str)
                and session_id.casefold() not in explicit_names
                and isinstance(title, str)
                and title.casefold() == lowered
                for session_id, title in rows
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, UnicodeError, ValueError):
        return None


def _has_exact_name(
    query: str,
    agent: str | None,
    claude_home: str | None,
    codex_home: str | None,
) -> bool | None:
    """Check the higher-priority exact-name tier without full resolution."""
    if agent in (None, "claude") and _claude_has_exact_name(query, claude_home):
        return True
    if agent in (None, "codex"):
        return _codex_has_exact_name(query, codex_home)
    return False


def _codex_has_outranking_id(
    query: str,
    candidate: _ValidatedFastCandidate,
    home: str | None,
) -> bool | None:
    """Return whether a database ID prefix outranks the fast candidate."""
    from claude_code_tools.resolve_session import (
        ResolverError,
        _codex_state_database,
    )

    try:
        database = _codex_state_database(get_codex_home(home))
    except (
        OSError,
        ResolverError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        return None
    if database is None:
        return False

    candidate_id = candidate.session_id.casefold()
    lowered = query.casefold()
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT id FROM threads")
            for (session_id,) in rows:
                if not isinstance(session_id, str):
                    continue
                normalized_id = session_id.casefold()
                if not normalized_id.startswith(lowered):
                    continue
                if candidate.resolved.agent != "codex" or normalized_id != candidate_id:
                    return True
            return False
        finally:
            connection.close()
    except (OSError, sqlite3.Error, UnicodeError, ValueError):
        return None


def _has_fast_path_conflict(
    query: str,
    candidate: _ValidatedFastCandidate,
    agent: str | None,
    claude_home: str | None,
    codex_home: str | None,
) -> bool | None:
    """Check cheap higher-priority name and database ID tiers."""
    exact_name = _has_exact_name(query, agent, claude_home, codex_home)
    if exact_name is not False:
        return exact_name
    if agent in (None, "codex"):
        return _codex_has_outranking_id(query, candidate, codex_home)
    return False


def _restore_discovered_path(
    session: str,
    agent: str,
    canonical_path: Path,
    claude_home: str | None,
    codex_home: str | None,
) -> Path:
    """Map a canonical path to one unique discovered in-home path."""
    root = (
        get_claude_home(claude_home) / "projects"
        if agent == "claude"
        else get_codex_home(codex_home)
    )
    pattern = "*/*.jsonl" if agent == "claude" else "**/*.jsonl"
    try:
        canonical = canonical_path.resolve()
        if canonical.is_relative_to(root.absolute()):
            return canonical_path
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return canonical_path
    matches: list[Path] = []
    try:
        for path in root.glob(pattern):
            try:
                if path.is_symlink() and path.resolve() == canonical:
                    matches.append(path.absolute())
            except (OSError, RuntimeError, UnicodeError, ValueError):
                continue
    except (OSError, RuntimeError, UnicodeError, ValueError):
        if not matches:
            return canonical_path
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        all_candidates = [
            _record_for_resolved_candidate(
                ResolvedSessionQuery(agent, path, None, None),
                matched_by="filename",
                claude_home=claude_home,
                codex_home=codex_home,
            )
            for path in matches
        ]
        all_candidates.sort(
            key=lambda record: record._modified_timestamp,
            reverse=True,
        )
        candidates = tuple(all_candidates[:_MAX_AMBIGUITY_LINES])
        raise SessionQueryAmbiguity(
            session,
            candidates,
            len(all_candidates),
            _fast_candidate_ambiguity(session, all_candidates),
        )
    return canonical_path


def _fast_candidate_ambiguity(
    session: str, candidates: list["SessionRecord"]
) -> str:
    """Build a fallback ambiguity message for resolver-invisible files."""
    lines = [
        f"  [{record.agent}] {record.session_file}"
        for record in candidates[:_MAX_AMBIGUITY_LINES]
    ]
    remaining = len(candidates) - len(lines)
    if remaining:
        lines.append(f"  ... and {remaining} more")
    listing = "\n".join(lines)
    return (
        f"Ambiguous session '{session}' matches {len(candidates)} sessions:\n"
        f"{listing}\n"
        "Use a longer unique id, the exact session name, or the "
        "full file path."
    )


def _record_for_resolved_candidate(
    candidate: ResolvedSessionQuery,
    *,
    matched_by: str,
    claude_home: str | None,
    codex_home: str | None,
) -> "SessionRecord":
    """Convert a validated filename candidate into selector metadata.

    Args:
        candidate: Validated session candidate.
        matched_by: Resolver match classification for display.
        claude_home: Optional Claude home override.
        codex_home: Optional Codex home override.

    Returns:
        A session record suitable for structured ambiguity selection.
    """
    from datetime import datetime

    from claude_code_tools.resolve_session import Agent, MatchKind, SessionRecord

    path = candidate.session_file
    timestamp = path.stat().st_mtime
    home = (
        get_claude_home(claude_home)
        if candidate.agent == "claude"
        else get_codex_home(codex_home)
    )
    return SessionRecord(
        agent=cast(Agent, candidate.agent),
        session_id=get_session_uuid(path.stem),
        name=None,
        directory=candidate.directory,
        home=str(home),
        session_file=str(path),
        matched_by=cast("MatchKind", matched_by),
        modified=datetime.fromtimestamp(timestamp).astimezone().isoformat(),
        archived="archived_sessions" in path.parts,
        _modified_timestamp=timestamp,
    )


def _result_tier(result: ResolveResult, query: str) -> int:
    """Return the global precedence tier for one agent resolver result."""
    matched_by = result.records[0].matched_by
    if matched_by == "id":
        return 0
    if matched_by == "name":
        name = result.records[0].name
        if name is not None and name.casefold() == query.casefold():
            return 1
        return 5
    return {
        "partial-id": 2,
        "id-substring": 3,
        "filename": 4,
    }[matched_by]


def _retain_winning_tier(
    results: list[ResolveResult], query: str
) -> list[ResolveResult]:
    """Discard cross-agent results weaker than the best resolver tier."""
    if not results:
        return results
    winning_tier = min(_result_tier(result, query) for result in results)
    return [result for result in results if _result_tier(result, query) == winning_tier]


def _validate_resolver_content_ids(
    result: ResolveResult,
    claude_home: str | None,
    codex_home: str | None,
    *,
    fast_candidates_overflowed: bool,
) -> ResolveResult | None:
    """Remove resolver records whose content contradicts their filename."""
    valid_records = []
    for record in result.records:
        path = Path(record.session_file)
        discovered_path = _restore_discovered_path(
            result.query,
            record.agent,
            path,
            claude_home,
            codex_home,
        )
        content_id = _session_id_from_content(record.agent, path)
        filename_id = get_session_uuid(discovered_path.stem)
        if content_id is not None and content_id.casefold() != filename_id.casefold():
            continue
        valid_records.append(
            replace(record, session_file=str(discovered_path))
        )

    removed_count = len(result.records) - len(valid_records)
    remaining_count = (
        result.match_count - removed_count
        if result.kind == "ambiguous"
        else len(valid_records)
    )
    if remaining_count <= 0:
        if fast_candidates_overflowed:
            return replace(
                result,
                kind="ambiguous",
                match_count=_MAX_FAST_PATH_CANDIDATES + 1,
            )
        return None
    if not valid_records:
        # ResolveResult retains at most 25 ambiguity records. If additional
        # records were truncated, their content identities are unknown here,
        # so preserve the ambiguity rather than reporting a false not-found.
        return result
    if remaining_count == 1:
        return replace(
            result,
            kind="single",
            records=(valid_records[0],),
            match_count=0,
        )
    return replace(
        result,
        kind="ambiguous",
        records=tuple(valid_records),
        match_count=remaining_count,
    )


def resolve_session_query(
    session: str,
    *,
    agent: str | None = None,
    claude_home: str | None = None,
    codex_home: str | None = None,
) -> ResolvedSessionQuery:
    """Resolve a path, id fragment, filename fragment, or session name.

    Args:
        session: User-supplied session query.
        agent: Optional hard agent constraint.
        claude_home: Optional Claude home override.
        codex_home: Optional Codex home override.

    Returns:
        The uniquely resolved session and its project metadata.

    Raises:
        SessionQueryError: If no unique eligible session can be selected.
    """
    for home_name, home in (
        ("Claude", claude_home),
        ("Codex", codex_home),
    ):
        if home is not None and not home.strip():
            raise SessionQueryError(f"{home_name} home must not be empty.")

    try:
        input_path: Path | None = Path(session).expanduser()
        is_direct_file = input_path.is_file()
    except (OSError, RuntimeError, ValueError):
        input_path = None
        is_direct_file = False
    if is_direct_file and input_path is not None:
        detected = _detect_direct_path_agent(input_path, claude_home, codex_home)
        if detected == "codex":
            from claude_code_tools.resolve_session import (
                _is_legacy_codex_rollout,
            )

            if not is_valid_session(input_path) and not (
                _is_legacy_codex_rollout(input_path)
            ):
                detected = None
        if detected not in ("claude", "codex"):
            raise SessionQueryError(
                f"Could not detect agent for session file: {input_path}"
            )
        if agent is not None and agent != detected:
            raise SessionQueryError(
                f"Session file is a {detected} session, but --agent "
                f"{agent} was specified: {input_path}"
            )
        supplied_path = input_path.absolute()
        directory, branch = _metadata_for_path(detected, supplied_path)
        return ResolvedSessionQuery(detected, supplied_path, directory, branch)

    fast_candidates: list[_ValidatedFastCandidate] = []
    fast_candidates_overflowed = False
    if _ID_FRAGMENT_RE.fullmatch(session):
        bounded = _validated_fast_candidates(session, agent, claude_home, codex_home)
        fast_candidates_overflowed = bounded is None
        if bounded is not None:
            fast_candidates = bounded
            if len(fast_candidates) == 1:
                if _FULL_ID_RE.fullmatch(session):
                    database_conflict = (
                        _codex_has_outranking_id(
                            session,
                            fast_candidates[0],
                            codex_home,
                        )
                        if agent in (None, "codex")
                        else False
                    )
                    if database_conflict is False:
                        return fast_candidates[0].resolved
                conflict = _has_fast_path_conflict(
                    session,
                    fast_candidates[0],
                    agent,
                    claude_home,
                    codex_home,
                )
                if conflict is False:
                    return fast_candidates[0].resolved

    results: list[ResolveResult] = []
    resolver_errors: list[ResolverError] = []
    homes = (
        ((agent, claude_home if agent == "claude" else codex_home),)
        if agent is not None
        else (("claude", claude_home), ("codex", codex_home))
    )
    for agent_name, home in homes:
        result = _resolve_query_for_agent(session, agent_name, home, resolver_errors)
        if result is not None and result.kind != "not_found":
            validated_result = _validate_resolver_content_ids(
                result,
                claude_home,
                codex_home,
                fast_candidates_overflowed=fast_candidates_overflowed,
            )
            if validated_result is not None:
                results.append(validated_result)

    results = _retain_winning_tier(results, session)
    if len(results) == 1 and results[0].kind == "single":
        record = results[0].records[0]
        path = Path(record.session_file)
        discovered_path = _restore_discovered_path(
            session, record.agent, path, claude_home, codex_home
        )
        _, branch = _metadata_for_path(record.agent, path)
        return ResolvedSessionQuery(
            record.agent, discovered_path, record.directory, branch
        )
    if results:
        records, match_count = ambiguity_candidates(results)
        raise SessionQueryAmbiguity(
            session,
            records,
            match_count,
            build_ambiguity_message(session, results),
        )
    if len(fast_candidates) > 1:
        all_records = [
            _record_for_resolved_candidate(
                candidate.resolved,
                matched_by="id-substring",
                claude_home=claude_home,
                codex_home=codex_home,
            )
            for candidate in fast_candidates
        ]
        all_records.sort(
            key=lambda record: record._modified_timestamp,
            reverse=True,
        )
        records = tuple(all_records[:_MAX_AMBIGUITY_LINES])
        raise SessionQueryAmbiguity(
            session,
            records,
            len(all_records),
            _fast_candidate_ambiguity(session, all_records),
        )
    if resolver_errors:
        raise SessionQueryError(str(resolver_errors[0]))
    raise SessionQueryError(f"Session not found: {session}")
