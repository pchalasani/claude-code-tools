"""Exercise selective Codex transfer with real isolated SQLite profiles."""

from __future__ import annotations

import json
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

    with sqlite3.connect(path / "queue_1.sqlite") as connection:
        connection.execute(
            "CREATE TABLE queued_thread_revisions "
            "(revision INTEGER PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE)"
        )


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
    mapped = (tmp_path / "files" / manifest["files"][0]).read_bytes()
    assert json.loads(mapped)["payload"]["cwd"] == "/new/project"
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
        ).fetchone()[0] == len(mapped)
        assert (
            "/old/project"
            in connection.execute(
                'SELECT item_json FROM thread_items WHERE thread_id="root"'
            ).fetchone()[0]
        )
    with sqlite3.connect(destination / "goals_1.sqlite") as connection:
        assert connection.execute("SELECT status FROM thread_goals").fetchone()[0] == (
            "active"
        )
    assert (source / "sessions/2026/root.jsonl").read_bytes() == original
    assert import_databases(manifest, destination)["ok"]
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        connection.execute("UPDATE threads SET title='continued' WHERE id='root'")
    with pytest.raises(ValueError, match="exists and differs"):
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


@pytest.mark.parametrize("kind", ["descendant", "writable_root", "managed_profile"])
def test_rejects_parent_escape_in_operational_paths(tmp_path: Path, kind: str) -> None:
    """A lexical prefix cannot grant containment to a parent-escaping path."""
    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    with sqlite3.connect(source / "state_5.sqlite") as db:
        if kind == "descendant":
            thread(source, "child")
            db.execute(
                "UPDATE threads SET cwd='/old/project/../outside' WHERE id='child'"
            )
            db.execute(
                "INSERT INTO thread_spawn_edges VALUES ('root','child','completed')"
            )
        else:
            if kind == "writable_root":
                policy = {
                    "type": "workspace-write",
                    "writable_roots": ["/old/project/../outside"],
                }
            else:
                policy = {
                    "file_system": {
                        "type": "restricted",
                        "entries": [
                            {
                                "path": {
                                    "type": "path",
                                    "path": str(source / "../outside"),
                                }
                            }
                        ],
                    }
                }
            db.execute("UPDATE threads SET sandbox_policy=?", (json.dumps(policy),))
    with pytest.raises(ValueError, match="needs a mapping"):
        export_session(
            source, "root", Path("/new/home"), Path("/new/project"), tmp_path / "bundle"
        )


def test_normalizes_valid_project_paths(tmp_path: Path) -> None:
    """Normalization preserves valid project-relative cwd and writable roots."""
    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    thread(source, "child")
    with sqlite3.connect(source / "state_5.sqlite") as db:
        db.execute("INSERT INTO thread_spawn_edges VALUES ('root','child','completed')")
        db.execute("UPDATE threads SET cwd='/old/project/sub/../src' WHERE id='child'")
        policy = {
            "type": "workspace-write",
            "writable_roots": ["/old/project/sub/../src"],
        }
        db.execute("UPDATE threads SET sandbox_policy=?", (json.dumps(policy),))
    result = export_session(
        source, "root", Path("/new/home"), Path("/new/project"), tmp_path / "bundle"
    )
    rows = next(
        table["rows"]
        for db in result["databases"]
        for table in db["tables"]
        if table["name"] == "threads"
    )
    child = next(row for row in rows if row["id"] == "child")
    assert child["cwd"] == "/new/project/src"
    assert json.loads(child["sandbox_policy"])["writable_roots"] == ["/new/project/src"]


def test_selected_support_and_metadata(tmp_path: Path) -> None:
    """Preserve prose, native goals and selected attachments with exact offsets."""
    source, destination = tmp_path / "source", tmp_path / "destination"
    profile(source)
    thread(source, "root", mode="legacy")
    attachment = source / "attachments" / "root" / "goal.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("native goal supporting document")
    missing = source / "attachments" / "root" / "absent.txt"
    rollout = source / "sessions/2026/root.jsonl"
    prose = {
        "type": "response_item",
        "payload": {
            "text": f"Read {attachment} and {missing}; /old/project stays historical"
        },
    }
    original_line = (json.dumps(prose) + "\n").encode()
    with rollout.open("ab") as stream:
        stream.write(original_line)
    (source / "session_index.jsonl").write_text(
        json.dumps({"id": "root", "thread_name": "example"})
        + "\n"
        + json.dumps({"id": "unrelated", "thread_name": "private"})
        + "\n"
    )
    (source / "external_agent_session_imports.json").write_text(
        json.dumps(
            {"records": [{"imported_thread_id": "root", "source_session_id": "old"}]}
        )
    )
    manifest = export_session(
        source, "root", destination, Path("/new/project"), tmp_path / "stage"
    )
    assert str(missing) in manifest["missing_at_source"]
    assert "attachments/root/goal.txt" in manifest["files"]
    assert (
        (tmp_path / "stage/files/sessions/2026/root.jsonl")
        .read_bytes()
        .endswith(original_line)
    )
    assert len(manifest["metadata_updates"]) == 2
    assert manifest["metadata_updates"][0]["rows"] == [
        {"id": "root", "thread_name": "example"}
    ]


def test_explicit_descendant_mapping(tmp_path: Path) -> None:
    """Linked-worktree descendants may have explicit independent project mappings."""
    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    thread(source, "child")
    with sqlite3.connect(source / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET cwd='/another/worktree' WHERE id='child'")
    insert(
        source,
        "state_5.sqlite",
        "thread_spawn_edges",
        {"parent_thread_id": "root", "child_thread_id": "child", "status": "completed"},
    )
    manifest = export_session(
        source,
        "root",
        tmp_path / "target",
        Path("/new/project"),
        tmp_path / "stage",
        path_mappings=[("/another/worktree", "/new/linked")],
    )
    rows = manifest["databases"][0]["tables"][0]["rows"]
    assert next(row for row in rows if row["id"] == "child")["cwd"] == "/new/linked"


def test_index_rename_exports_only_latest(tmp_path: Path) -> None:
    """Append-only native rename history must not become a transfer conflict."""
    from claude_code_tools.transfer_codex_artifacts import latest_session_index

    source = tmp_path / "source"
    profile(source)
    thread(source, "root")
    (source / "session_index.jsonl").write_text(
        json.dumps({"id": "root", "thread_name": "old name"})
        + "\n"
        + json.dumps({"id": "root", "thread_name": "current name"})
        + "\n"
    )
    assert latest_session_index(source)["root"]["thread_name"] == "current name"
    manifest = export_session(
        source, "root", tmp_path / "target", Path("/new/project"), tmp_path / "stage"
    )
    assert manifest["metadata_updates"][0]["rows"] == [
        {"id": "root", "thread_name": "current name"}
    ]


def test_second_transfer_recovers_historical_attachment(tmp_path: Path) -> None:
    """Round trips resolve preserved prose through the previous transfer map."""
    source = tmp_path / "second-machine"
    profile(source)
    thread(source, "root", mode="legacy")
    original = "/first-machine/.codex/attachments/root/goal.txt"
    attachment = source / "attachments/root/goal.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("goal attachment")
    prior = source / "transfer-support/root/path-map.json"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        json.dumps(
            {
                "path_mappings": [
                    {"source": "/first-machine/.codex", "destination": str(source)}
                ]
            }
        )
    )
    with (source / "sessions/2026/root.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {"type": "response_item", "payload": {"text": "Read " + original}}
            )
            + "\n"
        )
    destination = tmp_path / "third-machine"
    manifest = export_session(
        source, "root", destination, Path("/new/project"), tmp_path / "stage"
    )
    assert "attachments/root/goal.txt" in manifest["files"]
    assert {"source": "/first-machine/.codex", "destination": str(destination)} in (
        manifest["path_mappings"]
    )
    assert not manifest["missing_at_source"]
