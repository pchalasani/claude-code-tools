"""Non-interactive, JSON-first session resolution for agents and humans."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, TextIO

from claude_code_tools.resolve_session_names import codex_thread_names
from claude_code_tools.session_utils import (
    get_claude_home,
    get_codex_home,
    is_valid_session,
)

Agent = Literal["claude", "codex"]
MatchKind = Literal["id", "partial-id", "id-substring", "name", "filename"]
ResultKind = Literal["single", "ambiguous", "not_found"]
OutputFormat = Literal["auto", "json", "pretty"]

_PARTIAL_ID_RE = re.compile(r"^[0-9a-f-]+$", re.IGNORECASE)
_CODEX_STATE_RE = re.compile(r"state_(\d+)\.sqlite$")

# Queries containing path separators or glob metacharacters never
# match ANY tier, even when a session name literally contains them:
# such strings are paths or patterns, not session references.
_REJECTED_QUERY_CHARS = frozenset("/\\*?[]")

# Maximum characters materialized for any single transcript line while
# sniffing session files; longer lines are discarded in bounded chunks.
_MAX_RECORD_CHARS = 1_000_000


class ResolverError(Exception):
    """An expected error that should be rendered without a traceback."""

    def __init__(self, code: str, detail: str) -> None:
        """Initialize an expected resolver error.

        Args:
            code: Stable machine-facing error code.
            detail: Human-readable error detail.
        """
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SessionRecord:
    """Agent-facing metadata for one resumable session."""

    agent: Agent
    session_id: str
    name: str | None
    directory: str | None
    home: str
    session_file: str
    matched_by: MatchKind | None
    modified: str
    archived: bool
    _eligible: bool = True
    _modified_timestamp: float = 0.0

    def with_match(self, matched_by: MatchKind) -> SessionRecord:
        """Return a copy tagged with the winning match tier.

        Args:
            matched_by: Match tier responsible for this result.

        Returns:
            A copy carrying the match classification.
        """
        return replace(self, matched_by=matched_by)

    def to_dict(self) -> dict[str, object]:
        """Return the exact public JSON shape for a session record."""
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "name": self.name,
            "directory": self.directory,
            "home": self.home,
            "session_file": self.session_file,
            "matched_by": self.matched_by,
            "modified": self.modified,
            "archived": self.archived,
        }


@dataclass(frozen=True)
class ResolveResult:
    """Tagged result returned by the pure resolution layer."""

    kind: ResultKind
    query: str
    agent: Agent
    home: str
    records: tuple[SessionRecord, ...] = ()
    match_count: int = 0


def _absolute(path: Path) -> Path:
    """Return an expanded absolute path without requiring it to exist."""
    return path.expanduser().resolve(strict=False)


def _validate_home(home: Path) -> None:
    """Validate a resolver home directory.

    Args:
        home: Resolved Claude or Codex home.

    Raises:
        ResolverError: If the home cannot be searched.
    """
    if not home.exists():
        raise ResolverError("invalid_home", f"Home does not exist: {home}")
    if not home.is_dir():
        raise ResolverError("invalid_home", f"Home is not a directory: {home}")
    try:
        next(home.iterdir(), None)
    except OSError as error:
        raise ResolverError(
            "unreadable_home", f"Cannot read home {home}: {error}"
        ) from error


def _mtime(path: Path) -> tuple[str, float]:
    """Return a session file's local ISO mtime and numeric timestamp."""
    try:
        timestamp = path.stat().st_mtime
        modified = datetime.fromtimestamp(timestamp).astimezone().isoformat()
    except (OSError, OverflowError, ValueError) as error:
        raise ResolverError(
            "unreadable_session", f"Cannot stat session file {path}: {error}"
        ) from error
    return modified, timestamp


def _is_unreadable_session(error: ResolverError) -> bool:
    """Return whether an expected error is isolated to one session file."""
    return error.code == "unreadable_session"


def _normalize_archived(value: object) -> bool:
    """Normalize a SQLite archived value without relying on truthiness."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    return False


def _decode_sqlite_text(value: bytes) -> str | bytes:
    """Decode valid UTF-8 SQLite text and preserve invalid text as bytes."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value


def _normalize_directory(value: object) -> str | None:
    """Return a usable session directory, or None for malformed metadata."""
    return value if isinstance(value, str) and value.strip() else None


def _iter_bounded_lines(transcript: TextIO) -> Iterator[str]:
    """Yield transcript lines while bounding per-line memory.

    Reads with ``readline`` so a single enormous unterminated JSONL
    record never materializes more than :data:`_MAX_RECORD_CHARS`
    characters at a time; oversized lines are discarded in bounded
    chunks and skipped entirely.

    Args:
        transcript: An open text stream over a JSONL transcript.

    Yields:
        Each physical line no longer than the per-line bound.
    """
    while True:
        line = transcript.readline(_MAX_RECORD_CHARS + 1)
        if not line:
            return
        if len(line) > _MAX_RECORD_CHARS and not line.endswith("\n"):
            # Oversized line: discard its remainder in bounded chunks.
            while True:
                rest = transcript.readline(_MAX_RECORD_CHARS + 1)
                if not rest or rest.endswith("\n"):
                    break
            continue
        yield line


def _has_claude_session_record(session_file: Path) -> bool:
    """Return whether a transcript contains a Claude conversation record.

    Filename-based exact-ID resolution can tolerate missing or wrong-typed
    record metadata, including ``sessionId``. It still requires at least one
    parseable Claude conversation record so empty, unreadable, or wholly
    truncated files are not presented as resumable sessions.

    Hostile records are isolated to this file: oversized numeric
    literals (ValueError) and pathologically nested JSON
    (RecursionError) skip only the offending line, and per-line reads
    are memory bounded via :func:`_iter_bounded_lines`.
    """
    conversation_types = {
        "assistant",
        "system",
        "tool_result",
        "tool_use",
        "user",
    }
    try:
        with session_file.open("r", encoding="utf-8") as transcript:
            for line in _iter_bounded_lines(transcript):
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(record, dict):
                    continue
                record_type = record.get("type")
                if (
                    isinstance(record_type, str)
                    and record_type in conversation_types
                ):
                    return True
    except (OSError, UnicodeError):
        return False
    return False


def _claude_record(
    session_file: Path,
    home: Path,
    directory: str | None,
    eligible: bool,
) -> SessionRecord:
    """Build one Claude record using the established metadata helpers."""
    from claude_code_tools.find_claude_session import get_custom_title

    path = _absolute(session_file)
    modified, timestamp = _mtime(path)
    name = None
    if eligible and directory:
        name = get_custom_title(
            path.stem,
            directory,
            str(home),
            session_file=path,
        ) or None
    return SessionRecord(
        agent="claude",
        session_id=path.stem,
        name=name,
        directory=directory,
        home=str(home),
        session_file=str(path),
        matched_by=None,
        modified=modified,
        archived=False,
        _eligible=eligible,
        _modified_timestamp=timestamp,
    )


def enumerate_claude_sessions(home: Path) -> list[SessionRecord]:
    """Enumerate Claude sessions, including exact-ID-only excluded sessions.

    Args:
        home: Absolute Claude home directory.

    Returns:
        All discovered Claude session records.
    """
    from claude_code_tools import find_claude_session as claude_sessions
    from claude_code_tools.session_utils import extract_cwd_from_session

    _validate_home(home)
    projects = home / "projects"
    if not projects.exists():
        return []
    if not projects.is_dir():
        raise ResolverError(
            "invalid_home", f"Claude projects path is not a directory: {projects}"
        )

    rich_available = claude_sessions.RICH_AVAILABLE
    claude_sessions.RICH_AVAILABLE = False
    try:
        found = claude_sessions.find_sessions(
            [],
            global_search=True,
            claude_home=str(home),
        )
    finally:
        claude_sessions.RICH_AVAILABLE = rich_available

    records_by_path: dict[Path, SessionRecord] = {}
    for session in found:
        session_id = session[0]
        directory = _normalize_directory(session[6])
        if not isinstance(session_id, str) or not session_id or directory is None:
            continue
        try:
            path = Path(
                claude_sessions.get_session_file_path(
                    session_id, directory, str(home)
                )
            )
            if not _has_claude_session_record(path):
                continue
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        try:
            eligible = not claude_sessions.is_sidechain_session(path)
            eligible = (
                eligible
                and not claude_sessions.is_malformed_session(path)
            )
        except (OSError, UnicodeError, TypeError, ValueError):
            eligible = False
        absolute_path = _absolute(path)
        try:
            records_by_path[absolute_path] = _claude_record(
                absolute_path, home, directory, eligible
            )
        except ResolverError as error:
            if _is_unreadable_session(error):
                continue
            raise

    try:
        all_files = projects.glob("*/*.jsonl")
        for session_file in all_files:
            absolute_path = _absolute(session_file)
            if absolute_path in records_by_path:
                continue
            directory = _normalize_directory(
                extract_cwd_from_session(session_file, agent="claude")
            )
            if not _has_claude_session_record(session_file):
                continue
            try:
                eligible = not claude_sessions.is_sidechain_session(
                    session_file
                )
                eligible = (
                    eligible
                    and not claude_sessions.is_malformed_session(session_file)
                    and directory is not None
                )
            except (OSError, UnicodeError, TypeError, ValueError):
                eligible = False
            try:
                records_by_path[absolute_path] = _claude_record(
                    absolute_path, home, directory, eligible
                )
            except ResolverError as error:
                if _is_unreadable_session(error):
                    continue
                raise
    except OSError as error:
        raise ResolverError(
            "unreadable_home", f"Cannot scan Claude projects {projects}: {error}"
        ) from error
    return _deduplicate_records(list(records_by_path.values()))


def _codex_state_database(home: Path) -> Path | None:
    """Return the highest-numbered Codex state database, if present."""
    candidates: list[tuple[int, Path]] = []
    try:
        for path in home.glob("state_*.sqlite"):
            match = _CODEX_STATE_RE.fullmatch(path.name)
            if match and path.is_file():
                candidates.append((int(match.group(1)), path))
    except OSError as error:
        raise ResolverError(
            "unreadable_home", f"Cannot scan Codex home {home}: {error}"
        ) from error
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _codex_path(raw_path: object, home: Path) -> Path | None:
    """Normalize a rollout path read from the Codex database."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = home / path
        return _absolute(path)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _deduplicate_records(
    records: list[SessionRecord],
) -> list[SessionRecord]:
    """Keep the newest record for each session ID or canonical file path."""
    unique: list[SessionRecord] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    newest_usable_first = sorted(
        records,
        key=lambda record: (
            record._eligible,
            record._modified_timestamp,
        ),
        reverse=True,
    )
    for record in newest_usable_first:
        session_id = record.session_id.casefold()
        session_path = str(_absolute(Path(record.session_file)))
        if session_id in seen_ids or session_path in seen_paths:
            continue
        seen_ids.add(session_id)
        seen_paths.add(session_path)
        unique.append(record)
    return unique


def _merge_database_and_disk(
    database_records: list[SessionRecord],
    disk_records: list[SessionRecord],
) -> list[SessionRecord]:
    """Merge Codex records with the state database taking priority.

    Disk-fallback records are appended only when neither their
    session ID nor their canonical rollout path is already
    represented by a database record, so the database's
    authoritative metadata (title, cwd, archived) survives
    duplicate rollout copies regardless of which file was modified
    most recently. Both inputs arrive already deduplicated by
    their enumerators.
    """
    merged = list(database_records)
    seen_ids = {record.session_id.casefold() for record in merged}
    seen_paths = {
        str(_absolute(Path(record.session_file))) for record in merged
    }
    for record in disk_records:
        session_id = record.session_id.casefold()
        session_path = str(_absolute(Path(record.session_file)))
        if session_id in seen_ids or session_path in seen_paths:
            continue
        seen_ids.add(session_id)
        seen_paths.add(session_path)
        merged.append(record)
    return merged


def _enumerate_codex_database(
    home: Path, database: Path
) -> list[SessionRecord]:
    """Enumerate Codex threads from a read-only state database.

    Rollouts are accepted when they parse as modern sessions or as
    legacy 2025-format rollouts — the same validity rule the disk
    fallback applies — so legacy threads indexed in the database
    keep their authoritative title, cwd, and archived metadata.

    Rows whose claimed ID contradicts the ID embedded in the
    referenced rollout's filename are stale or corrupt and are
    rejected, so ID A can never silently resolve to rollout B's
    transcript.
    """
    from claude_code_tools import find_codex_session as codex_sessions

    uri = _absolute(database).as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise ResolverError(
            "unreadable_database", f"Cannot open {database}: {error}"
        ) from error

    # SQLite's default text decoder aborts fetchall() when any selected TEXT
    # value is invalid UTF-8. Preserve undecodable values as bytes so the
    # hostile row can be rejected or normalized without losing valid rows.
    connection.text_factory = _decode_sqlite_text
    connection.row_factory = sqlite3.Row
    try:
        table_info = connection.execute(
            "PRAGMA table_info(threads)"
        ).fetchall()
        columns = {str(row[1]) for row in table_info}
        required = {"id", "rollout_path"}
        if not required.issubset(columns):
            missing = ", ".join(sorted(required - columns))
            raise ResolverError(
                "invalid_database",
                f"Codex threads table in {database} lacks: {missing}",
            )
        selected = [
            column
            for column in (
                "id",
                "rollout_path",
                "cwd",
                "title",
                "archived",
                "git_branch",
                "updated_at",
            )
            if column in columns
        ]
        rows = connection.execute(
            f"SELECT {', '.join(selected)} FROM threads"
        ).fetchall()
    except sqlite3.Error as error:
        raise ResolverError(
            "unreadable_database", f"Cannot query {database}: {error}"
        ) from error
    finally:
        connection.close()

    records: list[SessionRecord] = []
    for row in rows:
        try:
            session_id = row["id"]
            if not isinstance(session_id, str) or not session_id.strip():
                continue
            session_file = _codex_path(row["rollout_path"], home)
            if session_file is None or not session_file.is_file():
                continue
            canonical_id = codex_sessions.extract_session_id_from_filename(
                session_file.name
            )
            if (
                isinstance(canonical_id, str)
                and canonical_id.casefold() != session_id.casefold()
            ):
                # Stale/corrupt row: it claims one ID but points at a
                # rollout whose filename encodes a different one.
                continue
            if not is_valid_session(session_file) and not (
                _is_legacy_codex_rollout(session_file)
            ):
                continue
            modified, timestamp = _mtime(session_file)
            title = row["title"] if "title" in selected else None
            directory = row["cwd"] if "cwd" in selected else None
            records.append(
                SessionRecord(
                    agent="codex",
                    session_id=session_id,
                    name=title if isinstance(title, str) and title else None,
                    directory=(
                        directory
                        if isinstance(directory, str) and directory
                        else None
                    ),
                    home=str(home),
                    session_file=str(session_file),
                    matched_by=None,
                    modified=modified,
                    archived=_normalize_archived(row["archived"])
                    if "archived" in selected
                    else False,
                    _modified_timestamp=timestamp,
                )
            )
        except (
            OSError,
            ResolverError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            # Database fields are hostile input. Skip only this row when its
            # rollout path cannot be normalized, checked, or read.
            continue
    return _deduplicate_records(records)


def _is_legacy_codex_rollout(session_file: Path) -> bool:
    """Return whether a rollout uses the legacy 2025 header format.

    Legacy rollouts start with a header record carrying ``id`` and
    ``timestamp`` plus ``git``/``instructions``; they contain none of
    the modern line types that :func:`is_valid_session` recognizes,
    so they need their own acceptance check. Only the first parseable
    records are inspected, with per-line memory bounded via
    :func:`_iter_bounded_lines`; garbage files never qualify.

    A header is accepted only when ``id`` and ``timestamp`` are
    nonempty strings, the ``id`` agrees with the ID embedded in the
    rollout filename (when the filename encodes one), and the record
    carries recognizable legacy content — a ``git`` object or string
    ``instructions``. Truncated files whose surviving header holds
    only null values are therefore never exposed as resumable.

    Args:
        session_file: Candidate rollout file.

    Returns:
        True when a valid legacy header record is found.
    """
    from claude_code_tools.find_codex_session import (
        extract_session_id_from_filename,
    )

    filename_id = extract_session_id_from_filename(session_file.name)
    try:
        with session_file.open(
            "r", encoding="utf-8", errors="replace"
        ) as transcript:
            checked = 0
            for line in _iter_bounded_lines(transcript):
                if checked >= 25:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                checked += 1
                try:
                    record = json.loads(stripped)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(record, dict):
                    continue
                header_id = record.get("id")
                timestamp = record.get("timestamp")
                if not isinstance(header_id, str) or not header_id.strip():
                    continue
                if not isinstance(timestamp, str) or not timestamp.strip():
                    continue
                if (
                    isinstance(filename_id, str)
                    and header_id.casefold() != filename_id.casefold()
                ):
                    continue
                if isinstance(record.get("git"), dict) or isinstance(
                    record.get("instructions"), str
                ):
                    return True
    except OSError:
        return False
    return False


def _codex_rollout_files(sessions_root: Path) -> list[Path]:
    """List rollout files beneath a Codex sessions tree by name only.

    Rollouts normally use YYYY/MM/DD directories, but any depth is
    accepted. Paths yielded before an inaccessible directory
    interrupts traversal are preserved; per-file guards downstream
    isolate failures in those paths.
    """
    rollout_files: list[Path] = []
    try:
        for path in sessions_root.rglob("rollout-*.jsonl"):
            rollout_files.append(path)
    except OSError:
        pass
    rollout_files.sort(reverse=True)
    return rollout_files


def _codex_fallback_directory(session_file: Path) -> str | None:
    """Extract a rollout's cwd for a disk-fallback record, or None."""
    from claude_code_tools import find_codex_session as codex_sessions

    try:
        metadata = codex_sessions.extract_session_metadata(session_file)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(metadata, dict):
        return None
    return _normalize_directory(metadata.get("cwd"))


def _enumerate_codex_fallback(
    home: Path,
    *,
    skip_ids: frozenset[str] = frozenset(),
    skip_paths: frozenset[str] = frozenset(),
) -> list[SessionRecord]:
    """Enumerate Codex rollout files directly from disk.

    Used on its own when a Codex home has no state database, and
    merged with the database enumeration otherwise so rollouts the
    database has not indexed (e.g. files written by ``aichat port``)
    still resolve.

    Discovery walks rollout FILENAMES first: each session ID comes
    from the filename, so rollouts already indexed by the database
    (``skip_ids``/``skip_paths``, casefolded IDs and absolute paths)
    are skipped without ever being opened. Only genuinely unindexed
    rollouts are read for validation and metadata, keeping healthy
    database-backed homes from re-reading every transcript.
    """
    from claude_code_tools import find_codex_session as codex_sessions

    sessions_root = home / "sessions"
    if not sessions_root.exists():
        return []
    if not sessions_root.is_dir():
        raise ResolverError(
            "invalid_home",
            f"Codex sessions path is not a directory: {sessions_root}",
        )

    records: list[SessionRecord] = []
    for rollout in _codex_rollout_files(sessions_root):
        session_file = _absolute(rollout)
        if str(session_file) in skip_paths:
            continue
        session_id = codex_sessions.extract_session_id_from_filename(
            session_file.name
        )
        if not isinstance(session_id, str) or not session_id:
            continue
        if session_id.casefold() in skip_ids:
            continue
        if not is_valid_session(session_file) and not (
            _is_legacy_codex_rollout(session_file)
        ):
            continue

        try:
            modified, timestamp = _mtime(session_file)
        except ResolverError as error:
            if _is_unreadable_session(error):
                continue
            raise

        records.append(
            SessionRecord(
                agent="codex",
                session_id=session_id,
                name=None,
                directory=_codex_fallback_directory(session_file),
                home=str(home),
                session_file=str(session_file),
                matched_by=None,
                modified=modified,
                archived=False,
                _modified_timestamp=timestamp,
            )
        )
    return _deduplicate_records(records)


def enumerate_codex_sessions(
    home: Path, *, fallback_on_database_error: bool = False
) -> list[SessionRecord]:
    """Enumerate Codex sessions from SQLite merged with on-disk rollouts.

    The highest-numbered state database is authoritative for session
    metadata (title, cwd, archived) whenever present, but rollout
    files missing from it — e.g. sessions written directly to disk by
    ``aichat port`` — are still enumerated from the sessions tree.
    Database records win deduplication over their disk counterparts,
    and rollouts the database already indexed are skipped by filename
    during disk discovery so they are never re-read.

    Args:
        home: Absolute Codex home directory.
        fallback_on_database_error: When True, a corrupt, incomplete,
            or locked state database degrades to disk-only rollout
            enumeration instead of raising, so valid rollouts still
            resolve during database damage or migration.

    Returns:
        All discovered Codex session records.
    """
    _validate_home(home)
    database = _codex_state_database(home)
    if database is None:
        return _apply_codex_thread_names(_enumerate_codex_fallback(home), home)
    try:
        database_records = _enumerate_codex_database(home, database)
    except ResolverError as error:
        if fallback_on_database_error and error.code in (
            "invalid_database",
            "unreadable_database",
        ):
            return _apply_codex_thread_names(
                _enumerate_codex_fallback(home), home
            )
        raise
    disk_records = _enumerate_codex_fallback(
        home,
        skip_ids=frozenset(
            record.session_id.casefold() for record in database_records
        ),
        skip_paths=frozenset(
            str(_absolute(Path(record.session_file)))
            for record in database_records
        ),
    )
    return _apply_codex_thread_names(
        _merge_database_and_disk(database_records, disk_records), home
    )


def _apply_codex_thread_names(
    records: list[SessionRecord], home: Path
) -> list[SessionRecord]:
    """Overlay explicit ``session_index.jsonl`` thread names.

    An explicit, user-assigned thread name is authoritative over the
    state database's auto-captured first-message title; records
    without an index entry keep their existing name.
    """
    names = codex_thread_names(home)
    if not names:
        return records
    return [
        replace(record, name=names[record.session_id.casefold()])
        if record.session_id.casefold() in names
        else record
        for record in records
    ]


def _resolved_home(agent: Agent, home: str | Path | None) -> Path:
    """Resolve an agent home through the shared precedence helpers."""
    if home is not None and not str(home).strip():
        raise ResolverError("invalid_home", "Home must not be empty.")
    cli_arg = str(home) if home is not None else None
    selected = (
        get_claude_home(cli_arg=cli_arg)
        if agent == "claude"
        else get_codex_home(cli_arg=cli_arg)
    )
    return _absolute(selected)


def _matches(
    records: list[SessionRecord], query: str
) -> tuple[list[SessionRecord], MatchKind | None]:
    """Apply ordered resolution tiers and return only the winning tier.

    Tier order: exact ID, exact name, ID prefix, ID substring,
    session-file name substring, then name substring. Only the first
    non-empty tier is returned, so an ID prefix match always beats a
    mid-ID substring match. Filename fragments rank above name
    substrings because they are structural (a timestamp fragment like
    "2026-03-25T14-50" identifies one rollout file) while session
    names are free text that may incidentally contain the same
    fragment and would otherwise drown the precise match.

    Queries containing path separators or glob metacharacters are
    rejected before ANY tier runs, so they match nothing even when a
    session is literally named ``a/b`` or ``has*star``.
    """
    if any(char in _REJECTED_QUERY_CHARS for char in query):
        return [], None
    lowered = query.casefold()
    exact_ids = [
        record
        for record in records
        if record.session_id.casefold() == lowered
    ]
    if exact_ids:
        return exact_ids, "id"

    named = [record for record in records if record._eligible and record.name]
    exact_names = [
        record
        for record in named
        if record.name is not None and record.name.casefold() == lowered
    ]
    if exact_names:
        return exact_names, "name"

    eligible = [record for record in records if record._eligible]
    if len(query) >= 4 and _PARTIAL_ID_RE.fullmatch(query):
        partial_ids = [
            record
            for record in eligible
            if record.session_id.casefold().startswith(lowered)
        ]
        if partial_ids:
            return partial_ids, "partial-id"
        id_substrings = [
            record
            for record in eligible
            if lowered in record.session_id.casefold()
        ]
        if id_substrings:
            return id_substrings, "id-substring"

    # Filename fragments (e.g. codex "rollout-..." prefixes or
    # "2026-03-25T14-50" timestamp fragments) are matched as literal
    # substrings of the already-enumerated session file basenames;
    # nothing is ever interpolated into a glob pattern. Path
    # separators were already rejected above for every tier. This tier
    # outranks name substrings: names are free text that can
    # incidentally contain a timestamp fragment (e.g. a session whose
    # first message quotes a rollout filename) and would otherwise
    # shadow the single structural match.
    if len(query) >= 4:
        filenames = [
            record
            for record in eligible
            if lowered in Path(record.session_file).name.casefold()
        ]
        if filenames:
            return filenames, "filename"

    substring_names = [
        record
        for record in named
        if record.name is not None and lowered in record.name.casefold()
    ]
    if substring_names:
        return substring_names, "name"
    return [], None


def resolve(
    query: str,
    agent: Agent,
    home: str | Path | None = None,
    *,
    fallback_on_database_error: bool = False,
) -> ResolveResult:
    """Resolve a query to one session, ambiguity, or no result.

    Args:
        query: Session name, full ID, ID prefix or substring, or a
            session-file name fragment (e.g. a codex rollout
            timestamp such as ``2026-03-25T14-50``). Queries with
            path separators or glob metacharacters match nothing.
        agent: Agent whose home should be searched.
        home: Optional explicit agent home.
        fallback_on_database_error: When True, a broken Codex state
            database degrades to disk-only rollout enumeration
            instead of raising a structured database error.

    Returns:
        A tagged resolution result.
    """
    if not query.strip():
        raise ResolverError("invalid_query", "Query must not be empty.")

    resolved_home = _resolved_home(agent, home)
    records = (
        enumerate_claude_sessions(resolved_home)
        if agent == "claude"
        else enumerate_codex_sessions(
            resolved_home,
            fallback_on_database_error=fallback_on_database_error,
        )
    )
    matches, matched_by = _matches(records, query)
    if not matches or matched_by is None:
        return ResolveResult(
            kind="not_found",
            query=query,
            agent=agent,
            home=str(resolved_home),
        )

    tagged = [record.with_match(matched_by) for record in matches]
    tagged.sort(key=lambda record: record._modified_timestamp, reverse=True)
    if len(tagged) == 1:
        return ResolveResult(
            kind="single",
            query=query,
            agent=agent,
            home=str(resolved_home),
            records=(tagged[0],),
        )
    return ResolveResult(
        kind="ambiguous",
        query=query,
        agent=agent,
        home=str(resolved_home),
        records=tuple(tagged[:25]),
        match_count=len(tagged),
    )

