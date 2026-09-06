"""Exercise selective Codex transfer with real isolated SQLite profiles."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from claude_code_tools.transfer_codex import (
    SCHEMAS,
    export_session,
    import_databases,
    validate_databases,
)


def profile(path: Path) -> None:
    """Initialize supported column contracts without touching a real account."""
    path.mkdir()
    for database, tables in SCHEMAS.items():
        with sqlite3.connect(path / database) as connection:
            for table, schema in tables.items():
                columns = []
                keys = []
                for _, name, kind, required, default, key in schema:
                    column = f'"{name}" {kind}'
                    if required:
                        column += " NOT NULL"
                    if default is not None:
                        column += f" DEFAULT {default}"
                    columns.append(column)
                    if key:
                        keys.append((key, name))
                if keys:
                    columns.append(
                        "PRIMARY KEY ("
                        + ",".join(f'"{name}"' for _, name in sorted(keys))
                        + ")"
                    )
                connection.execute(f'CREATE TABLE "{table}" ({",".join(columns)})')


def insert(path: Path, database: str, table: str, values: dict[str, Any]) -> None:
    """Insert a fixture row, supplying required scalar fields."""
    row = {}
    for _, name, kind, required, default, _ in SCHEMAS[database][table]:
        if name in values:
            row[name] = values[name]
        elif required and default is None:
            row[name] = 0 if kind == "INTEGER" else ""
    columns = ",".join(f'"{name}"' for name in row)
    placeholders = ",".join("?" for _ in row)
    with sqlite3.connect(path / database) as connection:
        connection.execute(
            f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
            list(row.values()),
        )


def thread(path: Path, sid: str, mode: str = "paginated") -> bytes:
    """Build an indexed rollout plus paginated history."""
    rollout = path / "sessions" / "2026" / f"{sid}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    content = b'{"type":"session_meta","payload":{"cwd":"/old/project"}}\n'
    rollout.write_bytes(content)
    insert(
        path,
        "state_5.sqlite",
        "threads",
        {
            "id": sid,
            "rollout_path": str(rollout),
            "cwd": "/old/project",
            "history_mode": mode,
            "sandbox_policy": '{"type":"workspace-write","writable_roots":["/old/project"]}',
        },
    )
    if mode == "paginated":
        insert(
            path,
            "thread_history_1.sqlite",
            "thread_history_projection_state",
            {
                "thread_id": sid,
                "next_rollout_byte_offset": len(content),
            },
        )
        insert(
            path,
            "thread_history_1.sqlite",
            "thread_turns",
            {
                "thread_id": sid,
                "turn_id": "turn1",
                "status": "completed",
                "rollout_byte_offset": len(content),
            },
        )
        insert(
            path,
            "thread_history_1.sqlite",
            "thread_items",
            {
                "thread_id": sid,
                "turn_id": "turn1",
                "item_id": "item1",
                "item_json": '{"text":"leave /old/project unchanged"}',
            },
        )
    return content


def test_roundtrip_preserves_offsets_and_selects_descendants(tmp_path: Path) -> None:
    """A real export/import retains history and goals without unrelated rows."""
    source, destination = tmp_path / "source", tmp_path / "destination"
    profile(source)
    profile(destination)
    original = thread(source, "root")
    thread(source, "child")
    thread(source, "unrelated")
    insert(
        source,
        "state_5.sqlite",
        "thread_spawn_edges",
        {
            "parent_thread_id": "root",
            "child_thread_id": "child",
            "status": "completed",
        },
    )
    insert(
        source,
        "goals_1.sqlite",
        "thread_goals",
        {
            "thread_id": "root",
            "goal_id": "goal",
            "objective": "keep this",
            "status": "active",
        },
    )
    insert(
        source,
        "memories_1.sqlite",
        "stage1_outputs",
        {
            "thread_id": "root",
            "raw_memory": "remember",
            "rollout_summary": "summary",
        },
    )
    manifest = export_session(
        source, "root", destination, Path("/new/project"), tmp_path
    )
    assert manifest["ok"]
    assert manifest["session_ids"] == ["root", "child"]
    assert len(manifest["files"]) == 2
    assert (tmp_path / "files" / manifest["files"][0]).read_bytes() == original
    validate_databases(manifest, destination)
    assert import_databases(manifest, destination)["ok"]
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM threads").fetchone()[0] == 2
        assert connection.execute(
            'SELECT cwd,rollout_path FROM threads WHERE id="root"'
        ).fetchone() == ("/new/project", str(destination / "sessions/2026/root.jsonl"))
    with sqlite3.connect(destination / "thread_history_1.sqlite") as connection:
        assert connection.execute(
            'SELECT rollout_byte_offset FROM thread_turns WHERE thread_id="root"'
        ).fetchone()[0] == len(original)
        assert (
            "/old/project"
            in connection.execute(
                'SELECT item_json FROM thread_items WHERE thread_id="root"'
            ).fetchone()[0]
        )
    with sqlite3.connect(destination / "goals_1.sqlite") as connection:
        assert connection.execute("SELECT status FROM thread_goals").fetchone()[0] == (
            "paused"
        )
    assert (source / "sessions/2026/root.jsonl").read_bytes() == original
    with pytest.raises(ValueError, match="exists"):
        import_databases(manifest, destination)


@pytest.mark.parametrize("problem", ["active", "queue", "schema", "projection"])
def test_rejects_incomplete_or_unknown_state(tmp_path: Path, problem: str) -> None:
    """Unsupported formats and pending work fail before producing a manifest."""
    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    if problem == "active":
        database, sql = (
            "thread_history_1.sqlite",
            ('UPDATE thread_turns SET status="inProgress"'),
        )
    elif problem == "schema":
        database, sql = "state_5.sqlite", "ALTER TABLE threads ADD COLUMN future TEXT"
    elif problem == "projection":
        database, sql = (
            "thread_history_1.sqlite",
            ("DELETE FROM thread_history_projection_state"),
        )
    else:
        insert(
            source,
            "queue_1.sqlite",
            "queued_items",
            {
                "id": "q",
                "thread_id": "root",
                "payload_json": "{}",
            },
        )
        database, sql = "queue_1.sqlite", "SELECT 1"
    with sqlite3.connect(source / database) as connection:
        connection.execute(sql)
    with pytest.raises(ValueError):
        export_session(source, "root", Path("/dest"), Path("/new"), tmp_path)


def test_sql_failure_rolls_back_all_databases(tmp_path: Path) -> None:
    """A target constraint failure leaves even earlier attached inserts undone."""
    source, destination = tmp_path / "source", tmp_path / "destination"
    profile(source)
    profile(destination)
    thread(source, "root")
    manifest = export_session(source, "root", destination, Path("/new"), tmp_path)
    with sqlite3.connect(destination / "thread_history_1.sqlite") as connection:
        connection.execute(
            "CREATE TRIGGER refuse BEFORE INSERT ON thread_turns "
            "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        import_databases(manifest, destination)
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM threads").fetchone()[0] == 0


def test_dry_run_and_unknown_destination(tmp_path: Path) -> None:
    """Validation neither inserts records nor creates missing databases."""
    source, destination = tmp_path / "source", tmp_path / "destination"
    profile(source)
    profile(destination)
    thread(source, "root", mode="legacy")
    manifest = export_session(source, "root", destination, Path("/new"), tmp_path)
    assert import_databases(manifest, destination, dry_run=True)["dry_run"]
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    (destination / "state_5.sqlite").unlink()
    with pytest.raises(ValueError, match="Initialize"):
        validate_databases(manifest, destination)
    assert not (destination / "state_5.sqlite").exists()


def test_rejects_projection_beyond_rollout(tmp_path: Path) -> None:
    """Incomplete rollout bytes cannot satisfy a newer history snapshot."""
    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    with sqlite3.connect(source / "thread_history_1.sqlite") as connection:
        connection.execute(
            "UPDATE thread_history_projection_state SET next_rollout_byte_offset=99999"
        )
    with pytest.raises(ValueError, match="offset"):
        export_session(source, "root", Path("/dest"), Path("/new"), tmp_path)


def test_remaps_managed_sandbox_metadata(tmp_path: Path) -> None:
    """Operational policy paths move, including profile memory access."""
    import json

    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    policy = {
        "type": "managed",
        "file_system": {
            "type": "restricted",
            "entries": [
                {"path": {"type": "path", "path": "/old/project/.git"}},
                {"path": {"type": "path", "path": str(source / "memories")}},
                {"path": {"type": "special", "value": {"kind": "project_roots"}}},
            ],
        },
    }
    with sqlite3.connect(source / "state_5.sqlite") as connection:
        connection.execute("UPDATE threads SET sandbox_policy=?", (json.dumps(policy),))
    result = export_session(source, "root", Path("/dest"), Path("/new"), tmp_path)
    saved = json.loads(result["databases"][0]["tables"][0]["rows"][0]["sandbox_policy"])
    entries = saved["file_system"]["entries"]
    assert entries[0]["path"]["path"] == "/new/.git"
    assert entries[1]["path"]["path"] == "/dest/memories"
    assert entries[2] == policy["file_system"]["entries"][2]
