"""Codex database and disk-fallback tests for ``aichat resolve``.

Split from ``test_resolve_session.py`` for the repo's 1000-line
file limit; fixtures and helpers come from
:mod:`tests.resolve_session_helpers`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.resolve_session_helpers import (
    _create_threads_database,
    _insert_codex_thread,
    _invoke,
    _json,
    _write_codex_session,
    runner,
)

__all__ = ["runner"]


def test_codex_fallback_without_database(runner: CliRunner, tmp_path: Path) -> None:
    home = tmp_path / "fallback-codex"
    session_id = "eeee7777-7777-4777-8777-777777777777"
    cwd = str(tmp_path / "fallback-project")
    session_file = _write_codex_session(
        home,
        session_id,
        cwd,
        1_720_000_300.0,
    )

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)
    name_result = _invoke(runner, "Fallback Name", home, agent="codex")
    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert payload["session_file"] == str(session_file.resolve())
    assert payload["name"] is None
    assert payload["archived"] is False
    assert name_result.exit_code == 1
    assert _json(name_result)["error"] == "not_found"


def test_codex_fallback_discovers_rollouts_at_arbitrary_depth(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    session_id = "eeee7766-7766-4766-8766-776677667766"
    rollout = _write_codex_session(
        home,
        session_id,
        str(tmp_path / "deep-project"),
        1_720_000_305.0,
    )
    deep_directory = home / "sessions" / "a" / "b" / "c" / "d" / "e"
    deep_directory.mkdir(parents=True)
    deeply_nested_rollout = deep_directory / rollout.name
    rollout.replace(deeply_nested_rollout)

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_file"] == str(deeply_nested_rollout.resolve())


def test_codex_empty_database_still_resolves_unindexed_rollout(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Rollouts missing from the state database still resolve."""
    home = tmp_path / "codex"
    home.mkdir()
    _create_threads_database(home / "state_1.sqlite")
    session_id = "eeee7788-7788-4788-8788-778877887788"
    cwd = str(tmp_path / "fallback-project")
    session_file = _write_codex_session(home, session_id, cwd, 1_720_000_310.0)

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert payload["session_file"] == str(session_file.resolve())
    assert payload["name"] is None


def test_codex_database_metadata_wins_but_unindexed_rollout_resolves(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The database stays authoritative for indexed sessions' metadata,
    while rollouts it has not indexed still resolve from disk."""
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    indexed_id = "eeee7799-7799-4799-8799-779977997799"
    omitted_id = "ffff7799-7799-4799-8799-779977997799"
    indexed = _write_codex_session(
        home, indexed_id, "/rollout-cwd", 1_720_000_320.0
    )
    _write_codex_session(
        home, omitted_id, "/omitted-cwd", 1_720_000_321.0
    )
    _insert_codex_thread(
        database,
        indexed_id,
        indexed,
        "/database-cwd",
        "Database Title",
        1_720_000_320,
        archived=True,
    )

    indexed_result = _invoke(runner, "Database Title", home, agent="codex")
    omitted_result = _invoke(runner, omitted_id, home, agent="codex")

    assert indexed_result.exit_code == 0
    assert _json(indexed_result)["directory"] == "/database-cwd"
    assert _json(indexed_result)["archived"] is True
    omitted_payload = _json(omitted_result)
    assert omitted_result.exit_code == 0
    assert omitted_payload["session_id"] == omitted_id
    assert omitted_payload["directory"] == "/omitted-cwd"
    assert omitted_payload["name"] is None
    assert omitted_payload["archived"] is False


def test_codex_database_beats_newer_disk_duplicate_of_same_id(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A newer on-disk duplicate never displaces database metadata.

    When the same session ID exists at two rollout paths and the
    unindexed copy has the newer mtime, the state database's record
    must still win the merge so its title, cwd, and archived state
    survive; otherwise name resolution silently breaks.
    """
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    session_id = "eeee8811-8811-4811-8811-881188118811"
    indexed = _write_codex_session(
        home, session_id, "/indexed-cwd", 1_720_000_000.0
    )
    copy_dir = home / "sessions" / "2026" / "07" / "15"
    copy_dir.mkdir(parents=True)
    newer_copy = copy_dir / (
        f"rollout-2026-07-15T09-00-00-{session_id}.jsonl"
    )
    newer_copy.write_text(
        indexed.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.utime(newer_copy, (1_720_050_000.0, 1_720_050_000.0))
    _insert_codex_thread(
        database,
        session_id,
        indexed,
        "/database-cwd",
        "Authoritative Title",
        1_720_000_000,
        archived=True,
    )

    id_result = _invoke(runner, session_id, home, agent="codex")
    name_result = _invoke(runner, "Authoritative Title", home, agent="codex")

    payload = _json(id_result)
    assert id_result.exit_code == 0
    assert payload["session_file"] == str(indexed.resolve())
    assert payload["name"] == "Authoritative Title"
    assert payload["directory"] == "/database-cwd"
    assert payload["archived"] is True
    name_payload = _json(name_result)
    assert name_result.exit_code == 0
    assert name_payload["session_id"] == session_id
    assert name_payload["session_file"] == str(indexed.resolve())


@pytest.mark.parametrize(
    "hostile_id",
    [None, sqlite3.Binary(b"not-a-text-id")],
    ids=["null", "blob"],
)
def test_codex_sqlite_non_string_ids_are_skipped(
    runner: CliRunner, tmp_path: Path, hostile_id: object
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    valid_id = "eeee7800-7800-4800-8800-780078007800"
    valid_rollout = _write_codex_session(
        home, valid_id, "/valid", 1_720_000_330.0
    )
    hostile_rollout = _write_codex_session(
        home,
        "ffff7800-7800-4800-8800-780078007800",
        "/hostile",
        1_720_000_331.0,
    )
    _insert_codex_thread(
        database,
        hostile_id,
        hostile_rollout,
        "/hostile",
        "Hostile Thread",
        1_720_000_331,
    )
    _insert_codex_thread(
        database,
        valid_id,
        valid_rollout,
        "/valid",
        "Valid Thread",
        1_720_000_330,
    )

    result = _invoke(runner, "Valid Thread", home, agent="codex")

    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id
    assert "Traceback" not in result.output


def test_codex_sqlite_invalid_utf8_text_is_isolated(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    hostile_id = "ffff7801-7801-4801-8801-780178017801"
    valid_id = "eeee7801-7801-4801-8801-780178017801"
    hostile_rollout = _write_codex_session(
        home, hostile_id, "/hostile", 1_720_000_331.0
    )
    valid_rollout = _write_codex_session(
        home, valid_id, "/valid", 1_720_000_330.0
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, cwd, title, archived,
                git_branch, updated_at
            ) VALUES (?, ?, ?, CAST(X'80' AS TEXT), ?, ?, ?)
            """,
            (
                hostile_id,
                str(hostile_rollout),
                "/hostile",
                0,
                "feat/session-finder",
                1_720_000_331,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _insert_codex_thread(
        database,
        valid_id,
        valid_rollout,
        "/valid",
        "Valid Thread After Invalid UTF-8",
        1_720_000_330,
    )

    result = _invoke(
        runner,
        "Valid Thread After Invalid UTF-8",
        home,
        agent="codex",
    )
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == valid_id
    assert "Traceback" not in result.output


def test_codex_hostile_database_paths_are_isolated_per_row(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    valid_id = "eeee7811-7811-4811-8811-781178117811"
    valid_rollout = _write_codex_session(
        home, valid_id, "/valid", 1_720_000_340.0
    )
    _insert_codex_thread(
        database,
        "ffff7811-7811-4811-8811-781178117811",
        "~codex-resolver-user-that-does-not-exist/rollout.jsonl",
        "/hostile",
        "Unexpandable Path",
        1_720_000_342,
    )
    _insert_codex_thread(
        database,
        "ffff7822-7822-4822-8822-782278227822",
        "rollout-with-embedded-nul\x00.jsonl",
        "/hostile",
        "Embedded Nul Path",
        1_720_000_341,
    )
    _insert_codex_thread(
        database,
        valid_id,
        valid_rollout,
        "/valid",
        "Valid After Hostile Paths",
        1_720_000_340,
    )

    result = _invoke(runner, "Valid After Hostile Paths", home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == valid_id
    assert "Traceback" not in result.output


@pytest.mark.parametrize("database_kind", ["corrupt", "incomplete"])
def test_codex_authoritative_database_failure_is_structured(
    runner: CliRunner, tmp_path: Path, database_kind: str
) -> None:
    home = tmp_path / database_kind
    home.mkdir()
    session_id = "eeee7890-7890-4890-8890-789078907890"
    _write_codex_session(home, session_id, "/valid", 1_720_000_350.0)
    database = home / "state_9.sqlite"
    if database_kind == "corrupt":
        database.write_bytes(b"not a sqlite database")
    else:
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE threads (id TEXT)")
            connection.commit()
        finally:
            connection.close()
    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 1
    assert payload["error"] in {"invalid_database", "unreadable_database"}
    assert "detail" in payload
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("include_payload_id", "payload_id"),
    [
        pytest.param(False, None, id="missing"),
        pytest.param(True, None, id="null"),
        pytest.param(True, "", id="empty"),
        pytest.param(True, 17, id="non-string"),
        pytest.param(
            True,
            "ffff9999-9999-4999-8999-999999999999",
            id="different",
        ),
    ],
)
def test_codex_fallback_uses_filename_id(
    runner: CliRunner,
    tmp_path: Path,
    include_payload_id: bool,
    payload_id: object,
) -> None:
    home = tmp_path / "codex"
    session_id = "eeee9999-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "project")
    payload: dict[str, object] = {"cwd": cwd, "git": {}}
    if include_payload_id:
        payload["id"] = payload_id
    _write_codex_session(
        home,
        session_id,
        cwd,
        1_720_000_400.0,
        lines=[
            {"type": "session_meta", "payload": payload},
            {"type": "event_msg", "payload": {}},
        ],
    )

    result = _invoke(runner, session_id, home, agent="codex")
    wrong_id = "None" if payload_id is None else str(payload_id)

    assert result.exit_code == 0
    assert _json(result)["session_id"] == session_id
    if wrong_id:
        assert _invoke(runner, wrong_id, home, agent="codex").exit_code == 1


def test_codex_fallback_skips_malformed_rollout(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    valid_id = "eeeeaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _write_codex_session(home, valid_id, "/valid", 1_720_000_500.0)
    _write_codex_session(
        home,
        "ffffbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "/bad",
        1_720_000_501.0,
        lines=[
            42,
            {"type": "response_item", "payload": None},
            {"type": "response_item", "payload": []},
            {"type": "response_item", "payload": {"role": [], "content": []}},
            {
                "type": "response_item",
                "payload": {"role": "user", "content": [{"text": 17}]},
            },
            {
                "type": "session_meta",
                "payload": {"cwd": [], "timestamp": {}, "git": []},
            },
        ],
    )
    malformed_id = "ffffbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    valid_result = _invoke(runner, valid_id, home, agent="codex")
    malformed_result = _invoke(runner, malformed_id, home, agent="codex")

    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == valid_id
    assert malformed_result.exit_code == 1
    assert _json(malformed_result)["error"] == "not_found"


def test_codex_database_skips_malformed_rollout(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    valid_id = "eeeecccc-cccc-4ccc-8ccc-cccccccccccc"
    malformed_id = "ffffdddd-dddd-4ddd-8ddd-dddddddddddd"
    valid_rollout = _write_codex_session(
        home, valid_id, "/valid", 1_720_000_510.0
    )
    malformed_rollout = _write_codex_session(
        home,
        malformed_id,
        "/malformed",
        1_720_000_511.0,
        lines=[{"type": "response_item", "payload": None}],
    )
    _insert_codex_thread(
        database,
        valid_id,
        valid_rollout,
        "/valid",
        "Valid Database Thread",
        1_720_000_510,
    )
    _insert_codex_thread(
        database,
        malformed_id,
        malformed_rollout,
        "/malformed",
        "Malformed Database Thread",
        1_720_000_511,
    )

    valid_result = _invoke(runner, valid_id, home, agent="codex")
    malformed_result = _invoke(
        runner, "Malformed Database Thread", home, agent="codex"
    )

    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == valid_id
    assert malformed_result.exit_code == 1
    assert _json(malformed_result)["error"] == "not_found"


@pytest.mark.parametrize(
    "use_database",
    [False, True],
    ids=["fallback", "sqlite"],
)
def test_codex_conversation_without_session_meta_is_not_resolvable(
    runner: CliRunner, tmp_path: Path, use_database: bool
) -> None:
    home = tmp_path / "codex"
    if use_database:
        home.mkdir()
        database = home / "state_1.sqlite"
        _create_threads_database(database)
    invalid_id = "ffffeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    valid_id = "eeeeffff-ffff-4fff-8fff-ffffffffffff"
    invalid_rollout = _write_codex_session(
        home,
        invalid_id,
        "/missing-metadata",
        1_720_000_520.0,
        lines=[{"type": "event_msg", "payload": {}}],
    )
    valid_rollout = _write_codex_session(
        home, valid_id, "/valid", 1_720_000_521.0
    )
    if use_database:
        _insert_codex_thread(
            database,
            invalid_id,
            invalid_rollout,
            "/missing-metadata",
            "Missing Session Metadata",
            1_720_000_520,
        )
        _insert_codex_thread(
            database,
            valid_id,
            valid_rollout,
            "/valid",
            "Valid Metadata Thread",
            1_720_000_521,
        )

    invalid_result = _invoke(runner, invalid_id, home, agent="codex")
    valid_result = _invoke(runner, valid_id, home, agent="codex")

    assert invalid_result.exit_code == 1
    assert _json(invalid_result)["error"] == "not_found"
    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == valid_id


@pytest.mark.parametrize(
    "use_database",
    [False, True],
    ids=["fallback", "sqlite"],
)
def test_codex_metadata_only_rollout_is_resolvable(
    runner: CliRunner, tmp_path: Path, use_database: bool
) -> None:
    home = tmp_path / "codex"
    if use_database:
        home.mkdir()
        database = home / "state_1.sqlite"
        _create_threads_database(database)
    session_id = "eeee1212-1212-4212-8212-121212121212"
    cwd = str(tmp_path / "metadata-only")
    rollout = _write_codex_session(
        home,
        session_id,
        cwd,
        1_720_000_530.0,
        lines=[
            {
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": cwd},
            }
        ],
    )
    if use_database:
        _insert_codex_thread(
            database,
            session_id,
            rollout,
            cwd,
            "Metadata Only Thread",
            1_720_000_530,
        )

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert payload["session_file"] == str(rollout.resolve())


def test_codex_fallback_skips_bad_json_syntax_between_valid_records(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    session_id = "eeeeabab-abab-4bab-8bab-abababababab"
    cwd = str(tmp_path / "surviving-project")
    rollout = _write_codex_session(
        home,
        session_id,
        cwd,
        1_720_000_550.0,
    )
    metadata = json.dumps(
        {"type": "session_meta", "payload": {"id": session_id, "cwd": cwd}}
    )
    rollout.write_text(
        "{bad json before}\n"
        f"{metadata}\n"
        '["bad json between"\n'
        f"{json.dumps({'type': 'event_msg', 'payload': {}})}\n"
        "bad-json-after\n",
        encoding="utf-8",
    )

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert "Traceback" not in result.output


def test_stale_codex_fallback_file_does_not_block_valid_match(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    session_id = "eeeecccc-cccc-4ccc-8ccc-cccccccccccc"
    valid = _write_codex_session(home, session_id, "/valid", 1_720_000_600.0)
    stale = valid.parent / (
        "rollout-2026-07-14T10-00-00-ffffdddd-dddd-4ddd-8ddd-dddddddddddd.jsonl"
    )
    stale.symlink_to(tmp_path / "rollout-that-no-longer-exists.jsonl")
    result = _invoke(runner, session_id, home, agent="codex")
    assert result.exit_code == 0, result.output
    assert _json(result)["session_id"] == session_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, False), (1, True), (None, False), ("0", False), ("1", True)],
)
def test_codex_archived_variants_through_sqlite_and_cli(
    runner: CliRunner,
    tmp_path: Path,
    value: object,
    expected: bool,
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    session_id = "abab0000-0000-4000-8000-000000000000"
    rollout = _write_codex_session(
        home,
        session_id,
        str(tmp_path / "project"),
        1_720_000_700.0,
    )
    _insert_codex_thread(
        database,
        session_id,
        rollout,
        str(tmp_path / "project"),
        "Archived Variant",
        1_720_000_700,
        archived=value,
    )

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["archived"] is expected
    assert isinstance(payload["archived"], bool)


def test_codex_fallback_wrong_typed_record_type_is_isolated(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    _write_codex_session(
        home,
        "face3000-0000-4000-8000-000000000003",
        "/hostile",
        1_720_001_001.0,
        lines=[{"type": [], "sessionId": None}],
    )
    valid_id = "face4000-0000-4000-8000-000000000004"
    _write_codex_session(home, valid_id, "/valid", 1_720_001_002.0)

    result = _invoke(runner, valid_id, home, agent="codex")

    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id
    assert "Traceback" not in result.output


def test_codex_sqlite_required_columns_only_resolve_without_cwd(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    valid_id = "face7000-0000-4000-8000-000000000007"
    valid_rollout = _write_codex_session(
        home, valid_id, "/metadata-cwd", 1_720_001_020.0
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE threads (id TEXT, rollout_path TEXT)"
        )
        connection.executemany(
            "INSERT INTO threads (id, rollout_path) VALUES (?, ?)",
            [
                (None, str(valid_rollout)),
                ("hostile-path", "rollout-with-nul\x00.jsonl"),
                (valid_id, str(valid_rollout)),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    result = _invoke(runner, valid_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == valid_id
    assert payload["directory"] is None
    assert payload["name"] is None
    assert payload["archived"] is False
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "cwd",
    [None, 17, sqlite3.Binary(b"not-text")],
    ids=["null", "integer", "blob"],
)
def test_codex_sqlite_wrong_typed_cwd_is_null_and_row_isolated(
    runner: CliRunner, tmp_path: Path, cwd: object
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    valid_id = "face8000-0000-4000-8000-000000000008"
    valid_rollout = _write_codex_session(
        home, valid_id, "/metadata-cwd", 1_720_001_021.0
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE threads (id TEXT, rollout_path TEXT, cwd)"
        )
        connection.executemany(
            "INSERT INTO threads (id, rollout_path, cwd) VALUES (?, ?, ?)",
            [
                (None, str(valid_rollout), "/hostile"),
                (valid_id, str(valid_rollout), cwd),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    result = _invoke(runner, valid_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == valid_id
    assert payload["directory"] is None
    assert "Traceback" not in result.output


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_codex_rollout_deleted_after_discovery_is_isolated(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex"
    valid_id = "dddd0000-0000-4000-8000-000000000001"
    deleted_id = "eeee0000-0000-4000-8000-000000000002"
    blocker_id = "ffff0000-0000-4000-8000-000000000003"
    valid = _write_codex_session(home, valid_id, "/valid", 1_720_001_030.0)
    deleted = _write_codex_session(
        home, deleted_id, "/deleted", 1_720_001_031.0
    )
    blocker = valid.parent / (
        f"rollout-2026-07-14T10-00-00-{blocker_id}.jsonl"
    )
    os.mkfifo(blocker)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "claude_code_tools.aichat",
            "resolve",
            valid_id,
            "--agent",
            "codex",
            "--home",
            str(home),
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    writer = -1
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            try:
                writer = os.open(blocker, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:
                if process.poll() is not None:
                    pytest.fail("resolver exited before scanning the FIFO")
                time.sleep(0.01)
        if writer < 0:
            pytest.fail("resolver did not reach the discovered FIFO")

        deleted.unlink()
        blocker.unlink()
        blocker.write_text("not json\n", encoding="utf-8")
        os.write(writer, b"not json\n")
        os.close(writer)
        writer = -1
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if writer >= 0:
            os.close(writer)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0, stderr or stdout
    payload = json.loads(stdout)
    assert payload["session_id"] == valid_id
    assert "Traceback" not in stdout
    assert "Traceback" not in stderr


def test_codex_database_row_pointing_at_wrong_rollout_is_rejected(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A stale row for ID A pointing at rollout B never resolves A.

    The rollout's filename encodes its canonical ID; a database row
    claiming a different ID is stale or corrupt and must be
    rejected instead of resolving to the wrong transcript. The
    rollout itself still resolves from disk under its own ID,
    without inheriting the stale row's metadata.
    """
    home = tmp_path / "codex"
    stale_id = "aaaa9999-0000-4000-8000-000000000001"
    real_id = "bbbb9999-0000-4000-8000-000000000002"
    rollout = _write_codex_session(home, real_id, "/real", 1_720_002_100.0)
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    _insert_codex_thread(
        database, stale_id, rollout, "/stale", "Stale Row", 1_720_002_100
    )

    stale_result = _invoke(runner, stale_id, home, agent="codex")
    real_result = _invoke(runner, real_id, home, agent="codex")

    assert stale_result.exit_code == 1
    assert _json(stale_result)["error"] == "not_found"
    real_payload = _json(real_result)
    assert real_result.exit_code == 0
    assert real_payload["session_id"] == real_id
    assert real_payload["session_file"] == str(rollout.resolve())
    assert real_payload["name"] is None
    assert real_payload["directory"] == "/real"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_codex_indexed_rollouts_are_not_reopened_by_disk_discovery(
    tmp_path: Path,
) -> None:
    """Disk discovery skips database-indexed IDs by filename alone.

    The duplicate rollout carrying an already-indexed ID is a FIFO:
    opening it for reading would block forever, so resolution only
    completes when the fallback never reads rollouts the database
    already supplied.
    """
    home = tmp_path / "codex"
    indexed_id = "dddd1234-0000-4000-8000-000000000001"
    valid = _write_codex_session(home, indexed_id, "/valid", 1_720_002_000.0)
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    _insert_codex_thread(
        database, indexed_id, valid, "/valid", "Indexed", 1_720_002_000
    )
    duplicate_dir = home / "sessions" / "2026" / "07" / "15"
    duplicate_dir.mkdir(parents=True)
    duplicate = duplicate_dir / (
        f"rollout-2026-07-15T09-00-00-{indexed_id}.jsonl"
    )
    os.mkfifo(duplicate)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "claude_code_tools.aichat",
            "resolve",
            indexed_id,
            "--agent",
            "codex",
            "--home",
            str(home),
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0, stderr or stdout
    payload = json.loads(stdout)
    assert payload["session_id"] == indexed_id
    assert payload["session_file"] == str(valid.resolve())
    assert "Traceback" not in stderr
