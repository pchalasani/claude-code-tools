"""Selective Codex session export and transactional database import.

Supports the explicitly recognized Codex SQLite schemas. This module uses only
stdlib so the transfer transport can execute it on a remote Python interpreter.
Structural rollout paths are mapped with corresponding history byte offsets.
"""

from __future__ import annotations

import json
import posixpath
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from claude_code_tools.transfer_codex_artifacts import CodexArtifacts

# PRAGMA table_info contracts, captured from Codex 0.153.4. Never guess how to
# migrate an unknown internal schema; initialize matching Codex on the target.
SCHEMAS: dict[str, dict[str, list[list[Any]]]] = {
    "state_5.sqlite": {
        "threads": [
            [0, "id", "TEXT", 0, None, 1],
            [1, "rollout_path", "TEXT", 1, None, 0],
            [2, "created_at", "INTEGER", 1, None, 0],
            [3, "updated_at", "INTEGER", 1, None, 0],
            [4, "source", "TEXT", 1, None, 0],
            [5, "model_provider", "TEXT", 1, None, 0],
            [6, "cwd", "TEXT", 1, None, 0],
            [7, "title", "TEXT", 1, None, 0],
            [8, "sandbox_policy", "TEXT", 1, None, 0],
            [9, "approval_mode", "TEXT", 1, None, 0],
            [10, "tokens_used", "INTEGER", 1, "0", 0],
            [11, "has_user_event", "INTEGER", 1, "0", 0],
            [12, "archived", "INTEGER", 1, "0", 0],
            [13, "archived_at", "INTEGER", 0, None, 0],
            [14, "git_sha", "TEXT", 0, None, 0],
            [15, "git_branch", "TEXT", 0, None, 0],
            [16, "git_origin_url", "TEXT", 0, None, 0],
            [17, "cli_version", "TEXT", 1, "''", 0],
            [18, "first_user_message", "TEXT", 1, "''", 0],
            [19, "agent_nickname", "TEXT", 0, None, 0],
            [20, "agent_role", "TEXT", 0, None, 0],
            [21, "memory_mode", "TEXT", 1, "'enabled'", 0],
            [22, "model", "TEXT", 0, None, 0],
            [23, "reasoning_effort", "TEXT", 0, None, 0],
            [24, "agent_path", "TEXT", 0, None, 0],
            [25, "created_at_ms", "INTEGER", 0, None, 0],
            [26, "updated_at_ms", "INTEGER", 0, None, 0],
            [27, "thread_source", "TEXT", 0, None, 0],
            [28, "preview", "TEXT", 1, "''", 0],
            [29, "recency_at", "INTEGER", 1, "0", 0],
            [30, "recency_at_ms", "INTEGER", 1, "0", 0],
            [31, "history_mode", "TEXT", 1, "'legacy'", 0],
            [32, "name", "TEXT", 0, None, 0],
            [33, "is_pinned", "INTEGER", 1, "0", 0],
            [34, "thread_section_id", "TEXT", 0, None, 0],
            [35, "section_position", "INTEGER", 0, None, 0],
            [36, "section_entered_at_ms", "INTEGER", 0, None, 0],
            [37, "project_id", "TEXT", 0, None, 0],
        ],
        "thread_dynamic_tools": [
            [0, "thread_id", "TEXT", 1, None, 1],
            [1, "position", "INTEGER", 1, None, 2],
            [2, "name", "TEXT", 1, None, 0],
            [3, "description", "TEXT", 1, None, 0],
            [4, "input_schema", "TEXT", 1, None, 0],
            [5, "defer_loading", "INTEGER", 1, "0", 0],
            [6, "namespace", "TEXT", 0, None, 0],
        ],
        "thread_spawn_edges": [
            [0, "parent_thread_id", "TEXT", 1, None, 0],
            [1, "child_thread_id", "TEXT", 1, None, 1],
            [2, "status", "TEXT", 1, None, 0],
        ],
        "thread_artifacts": [
            [0, "id", "TEXT", 0, None, 1],
            [1, "thread_id", "TEXT", 1, None, 0],
            [2, "artifact_type", "TEXT", 1, None, 0],
            [3, "identity_key", "TEXT", 1, None, 0],
            [4, "payload", "TEXT", 1, None, 0],
            [5, "created_at", "INTEGER", 1, None, 0],
        ],
    },
    "thread_history_1.sqlite": {
        "thread_turns": [
            [0, "thread_id", "TEXT", 1, None, 1],
            [1, "turn_id", "TEXT", 1, None, 2],
            [2, "rollout_ordinal", "INTEGER", 1, None, 0],
            [3, "status", "TEXT", 1, None, 0],
            [4, "error_json", "TEXT", 0, None, 0],
            [5, "started_at", "INTEGER", 0, None, 0],
            [6, "completed_at", "INTEGER", 0, None, 0],
            [7, "duration_ms", "INTEGER", 0, None, 0],
            [8, "first_user_item_id", "TEXT", 0, None, 0],
            [9, "final_agent_item_id", "TEXT", 0, None, 0],
            [10, "rollout_byte_offset", "INTEGER", 0, None, 0],
            [11, "rollout_end_ordinal", "INTEGER", 0, None, 0],
            [12, "rollout_end_byte_offset", "INTEGER", 0, None, 0],
        ],
        "thread_items": [
            [0, "thread_id", "TEXT", 1, None, 1],
            [1, "turn_id", "TEXT", 1, None, 2],
            [2, "item_id", "TEXT", 1, None, 3],
            [3, "rollout_ordinal", "INTEGER", 1, None, 0],
            [4, "created_at_ms", "INTEGER", 1, None, 0],
            [5, "item_json", "TEXT", 1, None, 0],
            [6, "item_type", "TEXT", 1, "''", 0],
            [7, "updated_at_ordinal", "INTEGER", 1, "0", 0],
        ],
        "thread_history_projection_state": [
            [0, "thread_id", "TEXT", 0, None, 1],
            [1, "next_rollout_byte_offset", "INTEGER", 1, None, 0],
            [2, "next_rollout_ordinal", "INTEGER", 1, None, 0],
        ],
        "thread_realtime_items": [
            [0, "thread_id", "TEXT", 1, None, 1],
            [1, "item_id", "TEXT", 1, None, 2],
            [2, "rollout_ordinal", "INTEGER", 1, None, 0],
            [3, "created_at_ms", "INTEGER", 1, None, 0],
            [4, "item_type", "TEXT", 1, None, 0],
            [5, "item_json", "TEXT", 1, None, 0],
        ],
    },
    "goals_1.sqlite": {
        "thread_goals": [
            [0, "thread_id", "TEXT", 1, None, 1],
            [1, "goal_id", "TEXT", 1, None, 0],
            [2, "objective", "TEXT", 1, None, 0],
            [3, "status", "TEXT", 1, None, 0],
            [4, "token_budget", "INTEGER", 0, None, 0],
            [5, "tokens_used", "INTEGER", 1, "0", 0],
            [6, "time_used_seconds", "INTEGER", 1, "0", 0],
            [7, "created_at_ms", "INTEGER", 1, None, 0],
            [8, "updated_at_ms", "INTEGER", 1, None, 0],
        ],
        "thread_goal_continuation_deferrals": [[0, "thread_id", "TEXT", 1, None, 1]],
    },
    "memories_1.sqlite": {
        "stage1_outputs": [
            [0, "thread_id", "TEXT", 0, None, 1],
            [1, "source_updated_at", "INTEGER", 1, None, 0],
            [2, "raw_memory", "TEXT", 1, None, 0],
            [3, "rollout_summary", "TEXT", 1, None, 0],
            [4, "rollout_slug", "TEXT", 0, None, 0],
            [5, "generated_at", "INTEGER", 1, None, 0],
            [6, "usage_count", "INTEGER", 0, None, 0],
            [7, "last_usage", "INTEGER", 0, None, 0],
            [8, "selected_for_phase2", "INTEGER", 1, "0", 0],
            [9, "selected_for_phase2_source_updated_at", "INTEGER", 0, None, 0],
        ]
    },
    "queue_1.sqlite": {
        "queued_items": [
            [0, "id", "TEXT", 1, None, 1],
            [1, "thread_id", "TEXT", 1, None, 0],
            [2, "payload_json", "TEXT", 1, None, 0],
            [3, "queue_order", "INTEGER", 1, None, 0],
            [4, "created_at_ms", "INTEGER", 1, None, 0],
            [5, "updated_at_ms", "INTEGER", 1, None, 0],
        ]
    },
}


def _connect(path: Path) -> sqlite3.Connection:
    """Open an existing database without creating or modifying it."""
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _schema(connection: sqlite3.Connection, table: str) -> list[list[Any]]:
    """Return a table's complete column contract."""
    return [list(row) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _check_schema(connection: sqlite3.Connection, database: str) -> None:
    """Reject unknown formats instead of silently dropping persistent state."""
    for table, expected in SCHEMAS[database].items():
        if _schema(connection, table) != expected:
            raise ValueError(f"Unsupported Codex schema: {database}/{table}")
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = row[0]
        # New per-thread tables may contain essential state we do not understand.
        if database == "queue_1.sqlite" and table == "queued_thread_revisions":
            # A wake-up counter, not queued content; never restart remote work.
            continue
        if table not in SCHEMAS[database]:
            if not table.replace("_", "").isalnum():
                raise ValueError(f"Unsupported Codex table name: {table!r}")
            columns = {column[1] for column in _schema(connection, table)}
            if "thread_id" in columns:
                raise ValueError(f"Unsupported Codex thread table: {database}/{table}")


def _rows(
    connection: sqlite3.Connection, table: str, ids: list[str], key: str = "thread_id"
) -> list[dict[str, Any]]:
    """Select only the requested conversations."""
    placeholders = ",".join("?" for _ in ids)
    query = f'SELECT * FROM "{table}" WHERE "{key}" IN ({placeholders})'
    return [dict(row) for row in connection.execute(query, ids)]


def _destination_rows(
    connection: sqlite3.Connection, table: str, ids: list[str], key: str
) -> list[dict[str, Any]]:
    """Include both endpoints when checking destination graph relationships."""
    if table != "thread_spawn_edges":
        return _rows(connection, table, ids, key)
    placeholders = ",".join("?" for _ in ids)
    query = (
        "SELECT * FROM thread_spawn_edges "
        f"WHERE parent_thread_id IN ({placeholders}) "
        f"OR child_thread_id IN ({placeholders})"
    )
    return [dict(row) for row in connection.execute(query, ids + ids)]


def _mapped_cwd(cwd: str, source: Path, destination: Path) -> str:
    """Map working directories within the selected project only."""
    try:
        relative = Path(posixpath.normpath(cwd)).relative_to(
            Path(posixpath.normpath(str(source)))
        )
        return str(Path(posixpath.normpath(str(destination))) / relative)
    except ValueError as exc:
        raise ValueError(
            f"Descendant working directory needs a mapping: {cwd}"
        ) from exc


def export_session(
    source_home: Path,
    session_id: str,
    destination_home: Path,
    destination_project: Path,
    staging: Path,
    path_mappings: list[tuple[str, str]] | dict[str, str] | None = None,
) -> dict[str, Any]:
    """Stage a session and descendants without copying unrelated account data.

    Args:
        source_home: Existing local Codex profile directory.
        session_id: Exact indexed thread identifier.
        destination_home: Absolute profile directory on the destination machine.
        destination_project: Absolute relocated working directory.
        staging: Private transfer staging directory; files are stored below files/.

    Returns:
        A JSON-serializable manifest describing files and selected database rows.

    Raises:
        ValueError: State is active, incomplete, unsupported, or cannot be mapped.
    """
    source_home = source_home.resolve()
    if not destination_home.is_absolute() or not destination_project.is_absolute():
        raise ValueError("Destination home and project must be absolute paths")
    connections: dict[str, sqlite3.Connection] = {}
    try:
        for database in SCHEMAS:
            path = source_home / database
            if not path.exists():
                if database == "state_5.sqlite":
                    raise ValueError("Codex state_5.sqlite is required")
                continue
            connection = _connect(path)
            connections[database] = connection
            connection.execute("BEGIN")
            _check_schema(connection, database)
        state = connections["state_5.sqlite"]
        root = state.execute(
            "SELECT * FROM threads WHERE id=?", (session_id,)
        ).fetchone()
        if root is None:
            raise ValueError(f"Codex session not indexed: {session_id}")
        ids = [session_id]
        for current in ids:
            children = state.execute(
                "SELECT child_thread_id FROM thread_spawn_edges "
                "WHERE parent_thread_id=?",
                (current,),
            )
            for child in children:
                if child[0] not in ids:
                    ids.append(child[0])
        threads = _rows(state, "threads", ids, "id")
        if len(threads) != len(ids):
            raise ValueError("A descendant thread is missing from the source index")
        source_project = Path(root["cwd"])
        from claude_code_tools.transfer_paths import normalize_path_mappings

        mappings = normalize_path_mappings(path_mappings).items()
        for old, new in mappings:
            if Path(old).is_relative_to(source_home):
                expected_home_path = Path(destination_home) / Path(old).relative_to(
                    source_home
                )
                if posixpath.normpath(new) != posixpath.normpath(
                    str(expected_home_path)
                ):
                    raise ValueError(
                        "Account home mapping conflicts with destination_home; "
                        "profile paths must retain their location within the selected account."
                    )
            if posixpath.normpath(old) == posixpath.normpath(str(source_project)) and (
                posixpath.normpath(new) != posixpath.normpath(str(destination_project))
            ):
                raise ValueError(
                    "Primary project mapping conflicts with destination_project; "
                    "use the same destination for the primary project."
                )
        path_mappings = [
            (old, new)
            for old, new in mappings
            if posixpath.normpath(old) != posixpath.normpath(str(source_project))
            and old != str(source_home)
        ]
        artifacts = CodexArtifacts(
            source_home,
            destination_home,
            source_project,
            destination_project,
            staging,
            session_id,
            path_mappings,
        )
        rollout_stats = {}
        for thread in threads:
            path = Path(thread["rollout_path"])
            stat = path.stat()
            rollout_stats[thread["id"]] = (stat.st_size, stat.st_mtime_ns)
        warnings = [
            (
                "Historical conversation prose is preserved; "
                "resume with --cd pointing to the destination project."
            ),
            (
                "Shared account settings, plugins, and consolidated memories are "
                "not transferred. Configure them separately."
            ),
        ]
        databases: list[dict[str, Any]] = []
        source_rows: list[tuple[str, str, str, list[dict[str, Any]]]] = []
        for database, connection in connections.items():
            tables: list[dict[str, Any]] = []
            for table, schema in SCHEMAS[database].items():
                if table == "threads":
                    rows = threads
                elif table == "thread_spawn_edges":
                    rows = _rows(connection, table, ids, "parent_thread_id")
                else:
                    rows = _rows(connection, table, ids)
                key = (
                    "id"
                    if table == "threads"
                    else (
                        "parent_thread_id"
                        if table == "thread_spawn_edges"
                        else "thread_id"
                    )
                )
                source_rows.append((database, table, key, json.loads(json.dumps(rows))))
                if table == "queued_items" and rows:
                    raise ValueError(
                        "Session has queued input; drain it before transfer"
                    )
                if table == "thread_dynamic_tools" and rows:
                    warnings.append(
                        "Dynamic tool definitions are copied; their implementations "
                        "must be configured separately on the destination."
                    )
                if table == "thread_artifacts":
                    for row in rows:
                        artifacts.discover(row["payload"])
                if table == "thread_turns" and any(
                    row["status"] not in ("completed", "interrupted", "failed")
                    for row in rows
                ):
                    raise ValueError("Session has an unfinished turn; stop it first")
                for row in rows:
                    artifacts.discover(json.dumps(row))
                if rows:
                    tables.append({"name": table, "schema": schema, "rows": rows})
            if tables:
                databases.append({"name": database, "tables": tables})
        history = connections.get("thread_history_1.sqlite")
        for thread in threads:
            if thread["history_mode"] not in ("legacy", "paginated"):
                raise ValueError("Unsupported Codex history mode")
            if thread["history_mode"] == "paginated" and (
                history is None
                or not _rows(history, "thread_history_projection_state", [thread["id"]])
            ):
                raise ValueError("Paginated session is missing its history projection")
        files: list[str] = []
        for thread in threads:
            rollout = Path(thread["rollout_path"])
            try:
                relative = rollout.resolve().relative_to(source_home)
            except ValueError as exc:
                raise ValueError("Rollout is outside the source profile") from exc
            if relative.parts[0] not in ("sessions", "archived_sessions"):
                raise ValueError("Unexpected Codex rollout location")
            if rollout.is_symlink() or not rollout.is_file():
                raise ValueError(f"Missing or symlinked rollout: {relative}")
            target = staging / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = rollout.stat()
            if (before.st_size, before.st_mtime_ns) != rollout_stats[thread["id"]]:
                raise ValueError(
                    "Rollout changed during export; stop the session first"
                )
            if history is not None:
                for table, fields in (
                    ("thread_history_projection_state", ["next_rollout_byte_offset"]),
                    (
                        "thread_turns",
                        ["rollout_byte_offset", "rollout_end_byte_offset"],
                    ),
                ):
                    for row in _rows(history, table, [thread["id"]]):
                        if any(
                            row[field] is not None
                            and not (0 <= row[field] <= before.st_size)
                            for field in fields
                        ):
                            raise ValueError(
                                "History offset exceeds the source rollout"
                            )
            offset_mapping = artifacts.rollout(rollout, target)
            for database in databases:
                if database["name"] != "thread_history_1.sqlite":
                    continue
                for table in database["tables"]:
                    for row in table["rows"]:
                        if row["thread_id"] != thread["id"]:
                            continue
                        for field in (
                            "rollout_byte_offset",
                            "rollout_end_byte_offset",
                            "next_rollout_byte_offset",
                        ):
                            if row.get(field) is not None:
                                if row[field] not in offset_mapping:
                                    raise ValueError(
                                        "History offset is not a record boundary"
                                    )
                                row[field] = offset_mapping[row[field]]
            after = rollout.stat()
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError(
                    "Rollout changed during export; stop the session first"
                )
            files.append(str(relative))
            thread["rollout_path"] = str(destination_home / relative)
            policy = json.loads(thread["sandbox_policy"])
            if not isinstance(policy, dict):
                raise ValueError(  # noqa: TRY004
                    "Unsupported sandbox policy; configure portable policy"
                )
            if "file_system" in policy:
                filesystem = policy["file_system"]
                if not isinstance(filesystem, dict) or filesystem.get("type") != (
                    "restricted"
                ):
                    raise ValueError("Unsupported managed sandbox policy")
                for entry in filesystem.get("entries", []):
                    location = entry["path"]
                    if location["type"] == "path":
                        original_path = Path(posixpath.normpath(location["path"]))
                        if original_path.is_relative_to(source_home):
                            location["path"] = str(
                                destination_home
                                / original_path.relative_to(source_home)
                            )
                        else:
                            location["path"] = artifacts.map_required(
                                str(original_path)
                            )
                    elif location["type"] != "special":
                        raise ValueError("Unsupported managed sandbox path")
            if "writable_roots" in policy:
                if not isinstance(policy["writable_roots"], list):
                    raise ValueError("Unsupported sandbox writable roots")
                roots = []
                for root_path in policy["writable_roots"]:
                    if not isinstance(root_path, str):
                        raise ValueError("Unsupported sandbox writable root")  # noqa: TRY004
                    roots.append(artifacts.map_required(root_path))
                policy["writable_roots"] = roots
            thread["sandbox_policy"] = json.dumps(policy)
            artifacts.operational_cwds.add(
                (thread["cwd"], artifacts.map_required(thread["cwd"]))
            )
            thread["cwd"] = artifacts.map_required(thread["cwd"])
            # Destination UI collections belong to that machine/account.
            for key in (
                "project_id",
                "thread_section_id",
                "section_position",
                "section_entered_at_ms",
            ):
                thread[key] = None
        support = artifacts.finish(ids)
        # Re-read outside each original snapshot: any selected state change means
        # the independent database snapshots cannot be treated as one export.
        for database, table, key, original_rows in source_rows:
            with closing(_connect(source_home / database)) as fresh:
                actual_rows = _rows(fresh, table, ids, key)
                canonical = lambda rows: sorted(
                    json.dumps(row, sort_keys=True) for row in rows
                )
                if canonical(actual_rows) != canonical(original_rows):
                    raise ValueError(
                        "Source database changed during export; stop it first"
                    )
        for thread_id, (size, modified) in rollout_stats.items():
            original_thread = next(
                row
                for db, table, key, rows in source_rows
                if table == "threads"
                for row in rows
                if row["id"] == thread_id
            )
            final_stat = Path(original_thread["rollout_path"]).stat()
            if (final_stat.st_size, final_stat.st_mtime_ns) != (size, modified):
                raise ValueError("Source rollout changed during export; stop it first")
        files.extend(support.pop("files"))
        return {
            **support,
            "ran": True,
            "ok": True,
            "agent": "codex",
            "session_id": session_id,
            "session_ids": ids,
            "source_project": str(source_project),
            "files": files,
            "databases": databases,
            "warnings": warnings,
        }
    finally:
        for connection in connections.values():
            connection.close()


def validate_databases(manifest: dict[str, Any], destination_home: Path) -> None:
    """Check destination schemas and conflicts without changing any database."""
    # Absence is selected state too: an omitted source table/database must not
    # inherit an unrelated destination goal, queue, memory, or history row.
    selected_ids = manifest.get("session_ids", [])
    exported = {
        database["name"]: {table["name"]: table["rows"] for table in database["tables"]}
        for database in manifest.get("databases", [])
    }
    for name, tables in SCHEMAS.items():
        path = destination_home / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Destination database must be a regular file")
        with closing(_connect(path)) as connection:
            _check_schema(connection, name)
            for table in tables:
                if table in exported.get(name, {}):
                    continue
                key = (
                    "id"
                    if table == "threads"
                    else (
                        "parent_thread_id"
                        if table == "thread_spawn_edges"
                        else "thread_id"
                    )
                )
                if selected_ids and _destination_rows(
                    connection, table, selected_ids, key
                ):
                    raise ValueError(
                        f"Destination-only session state exists and differs: {name}/{table}. "
                        "Use a separate account home; conversations are never merged."
                    )
    for database in manifest.get("databases", []):
        name = database["name"]
        if name not in SCHEMAS:
            raise ValueError(f"Unsupported Codex database: {name}")
        path = destination_home / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(
                f"Initialize matching Codex on destination: missing {name}"
            )
        with closing(_connect(path)) as connection:
            _check_schema(connection, name)
            for table in database["tables"]:
                table_name = table["name"]
                if table_name not in SCHEMAS[name]:
                    raise ValueError(f"Unsupported table: {table_name}")
                if table["schema"] != SCHEMAS[name][table_name]:
                    raise ValueError(f"Incompatible transfer schema: {table_name}")
                selection_key = (
                    "id"
                    if table_name == "threads"
                    else (
                        "parent_thread_id"
                        if table_name == "thread_spawn_edges"
                        else "thread_id"
                    )
                )
                selected_ids = manifest.get("session_ids", [])
                if selected_ids:
                    existing_rows = _destination_rows(
                        connection, table_name, selected_ids, selection_key
                    )
                    expected_rows = {
                        json.dumps(row, sort_keys=True) for row in table["rows"]
                    }
                    if any(
                        json.dumps(row, sort_keys=True) not in expected_rows
                        for row in existing_rows
                    ):
                        raise ValueError(
                            f"Destination session state exists and differs: {table_name}. "
                            "Use a separate account home; conversations are never merged."
                        )
                columns = [column[1] for column in table["schema"]]
                keys = [column[1] for column in table["schema"] if column[5]]
                for row in table["rows"]:
                    if set(row) != set(columns):
                        raise ValueError(f"Invalid row columns: {table_name}")
                    where = " AND ".join(f'"{key}"=?' for key in keys)
                    existing = connection.execute(
                        f'SELECT * FROM "{table_name}" WHERE {where}',
                        [row[key] for key in keys],
                    ).fetchone()
                    if existing and dict(existing) != row:
                        raise ValueError(
                            f"Destination session state exists and differs: {table_name}. "
                            "Use a separate destination account home; independently "
                            "continued conversations are never merged."
                        )


def import_databases(
    manifest: dict[str, Any],
    destination_home: Path,
    dry_run: bool = False,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Insert selected rows atomically on SQL failure, refusing existing state.

    Files must already be installed and verified by the caller. This transaction
    is the last import step. ``before_commit`` marks when callers must retain
    files on interruption because the commit outcome may be unknown.
    SQLite rolls back all attached databases on SQL
    errors; crash atomicity across WAL databases is not guaranteed by SQLite.
    """
    validate_databases(manifest, destination_home)
    if dry_run:
        return {"ok": True, "dry_run": True}
    with closing(sqlite3.connect(":memory:")) as connection:
        try:
            for number, database in enumerate(manifest.get("databases", [])):
                connection.execute(
                    f"ATTACH DATABASE ? AS d{number}",
                    (str(destination_home / database["name"]),),
                )
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for number, database in enumerate(manifest.get("databases", [])):
                for table in database["tables"]:
                    columns = [column[1] for column in table["schema"]]
                    names = ",".join(f'"{column}"' for column in columns)
                    placeholders = ",".join("?" for _ in columns)
                    query = (
                        f'INSERT INTO d{number}."{table["name"]}" ({names}) '
                        f"VALUES ({placeholders})"
                    )
                    keys = [c[1] for c in table["schema"] if c[5]]
                    where = " AND ".join(f'"{key}"=?' for key in keys)
                    for row in table["rows"]:
                        existing = connection.execute(
                            f'SELECT {names} FROM d{number}."{table["name"]}" WHERE {where}',
                            [row[key] for key in keys],
                        ).fetchone()
                        values = [row[column] for column in columns]
                        if existing is None:
                            connection.execute(query, values)
                        elif list(existing) != values:
                            raise ValueError("Destination changed during import")
            if before_commit is not None:
                before_commit()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    verify_databases(manifest, destination_home)
    return {"ran": True, "ok": True, "dry_run": False}


def verify_databases(manifest: dict[str, Any], destination_home: Path) -> None:
    """Require every imported row to exist and equal the selected snapshot."""
    for database in manifest.get("databases", []):
        with closing(_connect(destination_home / database["name"])) as connection:
            for table in database["tables"]:
                keys = [c[1] for c in table["schema"] if c[5]]
                where = " AND ".join(f'"{key}"=?' for key in keys)
                for row in table["rows"]:
                    actual = connection.execute(
                        f'SELECT * FROM "{table["name"]}" WHERE {where}',
                        [row[key] for key in keys],
                    ).fetchone()
                    if actual is None or dict(actual) != row:
                        raise ValueError("Imported database row verification failed")
