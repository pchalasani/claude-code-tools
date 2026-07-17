"""Non-interactive, JSON-first session resolution for agents and humans."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_code_tools.session_utils import (
    format_session_id_display,
    get_claude_home,
    get_codex_home,
    is_valid_session,
)

Agent = Literal["claude", "codex"]
MatchKind = Literal["id", "partial-id", "name"]
ResultKind = Literal["single", "ambiguous", "not_found"]
OutputFormat = Literal["auto", "json", "pretty"]

_PARTIAL_ID_RE = re.compile(r"^[0-9a-f-]+$", re.IGNORECASE)
_CODEX_STATE_RE = re.compile(r"state_(\d+)\.sqlite$")


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


def _has_claude_session_record(session_file: Path) -> bool:
    """Return whether a transcript contains a Claude conversation record.

    Filename-based exact-ID resolution can tolerate missing or wrong-typed
    record metadata, including ``sessionId``. It still requires at least one
    parseable Claude conversation record so empty, unreadable, or wholly
    truncated files are not presented as resumable sessions.
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
            for line in transcript:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
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


def _enumerate_codex_database(
    home: Path, database: Path
) -> list[SessionRecord]:
    """Enumerate Codex threads from a read-only state database."""
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
            if not is_valid_session(session_file):
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


def _enumerate_codex_fallback(home: Path) -> list[SessionRecord]:
    """Enumerate rollout files when Codex has no state database."""
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
    try:
        found = codex_sessions.find_sessions(
            home,
            [],
            num_matches=None,
            global_search=True,
        )
        for session in found:
            raw_session_file = session.get("file_path")
            if not isinstance(raw_session_file, str) or not raw_session_file:
                continue
            session_file = _absolute(Path(raw_session_file))
            session_id = codex_sessions.extract_session_id_from_filename(
                session_file.name
            )
            if not isinstance(session_id, str) or not session_id:
                continue
            if not is_valid_session(session_file):
                continue

            try:
                modified, timestamp = _mtime(session_file)
            except ResolverError as error:
                if _is_unreadable_session(error):
                    continue
                raise

            directory = _normalize_directory(session.get("cwd"))
            records.append(
                SessionRecord(
                    agent="codex",
                    session_id=session_id,
                    name=None,
                    directory=directory,
                    home=str(home),
                    session_file=str(session_file),
                    matched_by=None,
                    modified=modified,
                    archived=False,
                    _modified_timestamp=timestamp,
                )
            )
    except OSError as error:
        raise ResolverError(
            "unreadable_home",
            f"Cannot scan Codex sessions {sessions_root}: {error}",
        ) from error
    return _deduplicate_records(records)


def enumerate_codex_sessions(home: Path) -> list[SessionRecord]:
    """Enumerate Codex sessions from SQLite or recursive rollout fallback."""
    _validate_home(home)
    database = _codex_state_database(home)
    if database is None:
        return _enumerate_codex_fallback(home)

    # The highest-numbered state database is authoritative whenever present.
    # Rollout discovery is only a fallback for homes with no state database.
    return _enumerate_codex_database(home, database)


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
    """Apply ordered resolution tiers and return only the winning tier."""
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

    if len(query) >= 4 and _PARTIAL_ID_RE.fullmatch(query):
        partial_ids = [
            record
            for record in records
            if record._eligible
            and record.session_id.casefold().startswith(lowered)
        ]
        if partial_ids:
            return partial_ids, "partial-id"

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
) -> ResolveResult:
    """Resolve a query to one session, ambiguity, or no result.

    Args:
        query: Session name, full ID, or ID prefix.
        agent: Agent whose home should be searched.
        home: Optional explicit agent home.

    Returns:
        A tagged resolution result.
    """
    if not query.strip():
        raise ResolverError("invalid_query", "Query must not be empty.")

    resolved_home = _resolved_home(agent, home)
    records = (
        enumerate_claude_sessions(resolved_home)
        if agent == "claude"
        else enumerate_codex_sessions(resolved_home)
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


def _result_payload(result: ResolveResult) -> dict[str, object]:
    """Convert a tagged result to its exact JSON payload."""
    if result.kind == "single":
        return result.records[0].to_dict()
    if result.kind == "ambiguous":
        return {
            "error": "ambiguous",
            "query": result.query,
            "agent": result.agent,
            "match_count": result.match_count,
            "candidates": [record.to_dict() for record in result.records],
        }
    return {
        "error": "not_found",
        "query": result.query,
        "agent": result.agent,
        "home": result.home,
    }


def render_json(result: ResolveResult) -> None:
    """Print one JSON object for a tagged resolution result."""
    print(json.dumps(_result_payload(result)))


def _success_table(record: SessionRecord) -> Table:
    """Build the human-readable table for one resolved session."""
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")
    values = (
        ("Agent", record.agent),
        (
            "Session ID",
            format_session_id_display(
                record.session_id,
                truncate_length=len(record.session_id),
            ).removesuffix("..."),
        ),
        ("Name", record.name or "—"),
        ("Directory", record.directory or "—"),
        ("Home", record.home),
        ("Session file", record.session_file),
        ("Matched by", record.matched_by or "—"),
        ("Modified", record.modified),
        ("Archived", "yes" if record.archived else "no"),
    )
    for label, value in values:
        table.add_row(label, Text(value))
    return table


def _candidate_table(records: tuple[SessionRecord, ...]) -> Table:
    """Build the disambiguation table for candidate sessions."""
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Session ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Directory")
    table.add_column("Modified", no_wrap=True)
    table.add_column("Archived", no_wrap=True)
    for record in records:
        table.add_row(
            Text(format_session_id_display(record.session_id)),
            Text(record.name or "—"),
            Text(record.directory or "—"),
            Text(record.modified),
            "yes" if record.archived else "no",
        )
    return table


def render_pretty(result: ResolveResult) -> None:
    """Print a Rich panel or table for a tagged resolution result."""
    console = Console()
    if result.kind == "single":
        console.print(Panel(_success_table(result.records[0]), title="Session"))
    elif result.kind == "ambiguous":
        message = Text(style="yellow")
        message.append(f"{result.match_count} sessions match '")
        message.append(result.query)
        message.append("' — disambiguate:")
        console.print(message)
        console.print(_candidate_table(result.records))
    else:
        message = Text(style="yellow")
        message.append("No session found for '")
        message.append(result.query)
        message.append(f"' in {result.home}")
        console.print(message)


def _render_error(code: str, detail: str, pretty: bool) -> None:
    """Render an expected operational error."""
    if pretty:
        message = Text()
        message.append("Error:", style="red")
        message.append(f" {detail}")
        Console().print(message)
    else:
        print(json.dumps({"error": code, "detail": detail}))


def run(
    query: str,
    agent: str,
    home: str | Path | None,
    fmt: str = "auto",
) -> int:
    """Resolve, render, and return the command's process exit code.

    Args:
        query: Session name, full ID, or ID prefix.
        agent: ``claude`` or ``codex``.
        home: Optional explicit agent home.
        fmt: ``auto``, ``json``, or ``pretty``.

    Returns:
        Zero for a unique match, two for ambiguity, or one otherwise.
    """
    pretty = fmt == "pretty" or (fmt == "auto" and sys.stdout.isatty())
    try:
        if agent not in ("claude", "codex"):
            raise ResolverError("invalid_agent", f"Unsupported agent: {agent}")
        if fmt not in ("auto", "json", "pretty"):
            raise ResolverError("invalid_format", f"Unsupported format: {fmt}")
        result = resolve(query, cast(Agent, agent), home)
    except ResolverError as error:
        _render_error(error.code, error.detail, pretty)
        return 1
    except (OSError, sqlite3.Error) as error:
        _render_error(type(error).__name__, str(error), pretty)
        return 1
    except Exception as error:
        _render_error("resolver_error", str(error) or type(error).__name__, pretty)
        return 1

    if pretty:
        render_pretty(result)
    else:
        render_json(result)
    if result.kind == "single":
        return 0
    if result.kind == "ambiguous":
        return 2
    return 1
