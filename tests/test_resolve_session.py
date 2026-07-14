"""Tests for the non-interactive ``aichat resolve`` command."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
from claude_code_tools.resolve_session import _normalize_archived
from claude_code_tools.session_utils import encode_claude_project_path

CLAUDE_IDS = (
    "aaaa1111-1111-4111-8111-111111111111",
    "aaaa2222-2222-4222-8222-222222222222",
    "bbbb3333-3333-4333-8333-333333333333",
)
CODEX_IDS = (
    "cccc1111-1111-4111-8111-111111111111",
    "cccc2222-2222-4222-8222-222222222222",
    "dddd3333-3333-4333-8333-333333333333",
)


@dataclass(frozen=True)
class FakeHome:
    """Paths and metadata for one fake agent home."""

    path: Path
    ids: tuple[str, str, str]
    files: tuple[Path, Path, Path]
    directories: tuple[str, str, str]


def _write_claude_session(
    home: Path,
    session_id: str,
    cwd: str,
    title: str,
    mtime: float,
) -> Path:
    """Create one realistic minimal Claude transcript."""
    project = home / "projects" / encode_claude_project_path(cwd)
    project.mkdir(parents=True, exist_ok=True)
    session_file = project / f"{session_id}.jsonl"
    lines = [
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "message": {"content": "hello"},
        },
        {
            "type": "ai-title",
            "sessionId": session_id,
            "aiTitle": f"Auto {title}",
        },
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": title,
        },
    ]
    session_file.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


def _write_raw_claude_session(
    home: Path,
    session_id: str,
    project_cwd: str,
    lines: list[object],
    mtime: float = 1_720_000_000.0,
) -> Path:
    """Create a Claude transcript from exact JSON values."""
    project = home / "projects" / encode_claude_project_path(project_cwd)
    project.mkdir(parents=True, exist_ok=True)
    session_file = project / f"{session_id}.jsonl"
    session_file.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


@pytest.fixture
def claude_home(tmp_path: Path) -> FakeHome:
    """Create three Claude sessions with name and prefix collisions."""
    home = tmp_path / "claude"
    directories = (
        str(tmp_path / "work" / "alpha"),
        str(tmp_path / "work" / "beta"),
        str(tmp_path / "work" / "gamma"),
    )
    titles = ("Shared Plan", "Shared Plan", "Unique Deployment Review")
    files = tuple(
        _write_claude_session(
            home,
            session_id,
            cwd,
            title,
            1_700_000_000.0 + index,
        )
        for index, (session_id, cwd, title) in enumerate(
            zip(CLAUDE_IDS, directories, titles, strict=True)
        )
    )
    return FakeHome(home, CLAUDE_IDS, files, directories)


def _create_threads_database(database: Path) -> None:
    """Create the Codex threads schema used by the resolver."""
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT,
                rollout_path TEXT,
                cwd TEXT,
                title TEXT,
                archived INTEGER,
                git_branch TEXT,
                updated_at INTEGER
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_codex_thread(
    database: Path,
    session_id: str,
    rollout_path: Path,
    cwd: str,
    title: str,
    updated_at: int,
    archived: bool = False,
) -> None:
    """Insert one row into a fake Codex threads database."""
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, cwd, title, archived,
                git_branch, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                str(rollout_path),
                cwd,
                title,
                int(archived),
                "feat/session-finder",
                updated_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _write_codex_session(
    home: Path,
    session_id: str,
    cwd: str,
    mtime: float,
    lines: list[object] | None = None,
) -> Path:
    """Create one realistic minimal Codex rollout file."""
    session_dir = home / "sessions" / "2026" / "07" / "14"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / (
        f"rollout-2026-07-14T10-00-00-{session_id}.jsonl"
    )
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "cwd": cwd,
            "git": {"branch": "feat/session-finder"},
        },
    }
    entries = [metadata] if lines is None else lines
    session_file.write_text(
        "".join(f"{json.dumps(entry)}\n" for entry in entries),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


@pytest.fixture
def codex_home(tmp_path: Path) -> FakeHome:
    """Create three Codex threads with name and prefix collisions."""
    home = tmp_path / "codex"
    home.mkdir()
    directories = (
        str(tmp_path / "code" / "alpha"),
        str(tmp_path / "code" / "beta"),
        str(tmp_path / "code" / "gamma"),
    )
    titles = ("Shared Codex", "Shared Codex", "Unique Codex Migration")
    files = tuple(
        _write_codex_session(
            home,
            session_id,
            cwd,
            1_710_000_000.0 + index,
        )
        for index, (session_id, cwd) in enumerate(
            zip(CODEX_IDS, directories, strict=True)
        )
    )
    old_database = home / "state_4.sqlite"
    _create_threads_database(old_database)
    database = home / "state_5.sqlite"
    _create_threads_database(database)
    for index, (session_id, session_file, cwd, title) in enumerate(
        zip(CODEX_IDS, files, directories, titles, strict=True)
    ):
        _insert_codex_thread(
            database,
            session_id,
            session_file,
            cwd,
            title,
            1_710_000_000 + index,
            archived=index == 1,
        )
    return FakeHome(home, CODEX_IDS, files, directories)


@pytest.fixture
def runner() -> CliRunner:
    """Return an isolated in-process Click runner."""
    return CliRunner()


def _invoke(
    runner: CliRunner,
    query: str | None,
    home: Path | None = None,
    agent: str | None = None,
    extra: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    """Invoke ``aichat resolve`` with concise test arguments."""
    args = ["resolve"]
    if query is not None:
        args.append(query)
    if agent is not None:
        args.extend(["--agent", agent])
    if home is not None:
        args.extend(["--home", str(home)])
    if extra:
        args.extend(extra)
    return runner.invoke(main, args, env=env, catch_exceptions=False)


def _json(result: Result) -> dict[str, object]:
    """Decode a command's single JSON object."""
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    ("query", "expected_id", "matched_by"),
    [
        (CLAUDE_IDS[0], CLAUDE_IDS[0], "id"),
        ("bbbb", CLAUDE_IDS[2], "partial-id"),
        ("unique deployment review", CLAUDE_IDS[2], "name"),
        ("deployment", CLAUDE_IDS[2], "name"),
    ],
)
def test_claude_success_tiers(
    runner: CliRunner,
    claude_home: FakeHome,
    query: str,
    expected_id: str,
    matched_by: str,
) -> None:
    result = _invoke(runner, query, claude_home.path)
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == expected_id
    assert payload["matched_by"] == matched_by


@pytest.mark.parametrize("query", ["shared plan", "aaaa"])
def test_claude_ambiguous_tiers(
    runner: CliRunner, claude_home: FakeHome, query: str
) -> None:
    result = _invoke(runner, query, claude_home.path)
    payload = _json(result)
    assert result.exit_code == 2
    assert payload["error"] == "ambiguous"
    assert payload["match_count"] == 2
    assert len(payload["candidates"]) == 2  # type: ignore[arg-type]


def test_claude_not_found(runner: CliRunner, claude_home: FakeHome) -> None:
    result = _invoke(runner, "missing session", claude_home.path)
    payload = _json(result)
    assert result.exit_code == 1
    assert payload == {
        "error": "not_found",
        "query": "missing session",
        "agent": "claude",
        "home": str(claude_home.path.resolve()),
    }


def test_existing_file_home_returns_structured_error(
    runner: CliRunner, tmp_path: Path
) -> None:
    bad_home = tmp_path / "home-file"
    bad_home.write_text("not a directory", encoding="utf-8")
    result = _invoke(runner, "anything", bad_home)
    payload = _json(result)
    assert result.exit_code == 1
    assert payload["error"] == "invalid_home"
    assert "not a directory" in str(payload["detail"])
    assert "Usage:" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("query", [None, "", " ", " \t "])
def test_empty_query_is_rejected(
    runner: CliRunner,
    claude_home: FakeHome,
    query: str | None,
) -> None:
    result = _invoke(runner, query, claude_home.path)
    payload = _json(result)
    assert result.exit_code == 1
    expected_error = "invalid_input" if query is None else "invalid_query"
    assert payload["error"] == expected_error
    if query is None:
        assert "Missing argument 'QUERY'" in str(payload["detail"])
    else:
        assert payload["detail"] == "Query must not be empty."
    assert "Traceback" not in result.output


def test_invalid_agent_is_structured(runner: CliRunner) -> None:
    result = _invoke(runner, "query", agent="other")
    assert result.exit_code == 1
    assert _json(result)["error"] == "invalid_agent"
    assert "Usage:" not in result.output


def test_default_agent_is_claude(runner: CliRunner, claude_home: FakeHome) -> None:
    payload = _json(_invoke(runner, claude_home.ids[0], claude_home.path))
    assert payload["agent"] == "claude"


def test_explicit_home_overrides_agent_environments(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    tmp_path: Path,
) -> None:
    wrong_home = tmp_path / "wrong-claude"
    wrong_home.mkdir()
    claude_result = _invoke(
        runner,
        claude_home.ids[0],
        claude_home.path,
        env={"CLAUDE_CONFIG_DIR": str(wrong_home)},
    )
    wrong_home = tmp_path / "wrong-codex"
    wrong_home.mkdir()
    codex_result = _invoke(
        runner,
        codex_home.ids[0],
        codex_home.path,
        agent="codex",
        env={"CODEX_HOME": str(wrong_home)},
    )
    assert claude_result.exit_code == codex_result.exit_code == 0
    assert _json(claude_result)["home"] == str(claude_home.path.resolve())
    assert _json(codex_result)["home"] == str(codex_home.path.resolve())


def test_empty_explicit_homes_do_not_use_environment(
    runner: CliRunner, claude_home: FakeHome, codex_home: FakeHome
) -> None:
    cases = (
        ("claude", "CLAUDE_CONFIG_DIR", claude_home),
        ("codex", "CODEX_HOME", codex_home),
    )
    for agent, variable, fake_home in cases:
        result = runner.invoke(
            main,
            ["resolve", fake_home.ids[0], "--agent", agent, "--home", ""],
            env={variable: str(fake_home.path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        assert _json(result)["error"] == "invalid_home"


def test_agent_home_environment_variables(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    claude_result = _invoke(
        runner,
        claude_home.ids[2],
        env={"CLAUDE_CONFIG_DIR": str(claude_home.path)},
    )
    codex_result = _invoke(
        runner,
        codex_home.ids[2],
        agent="codex",
        env={"CODEX_HOME": str(codex_home.path)},
    )
    assert _json(claude_result)["home"] == str(claude_home.path.resolve())
    assert _json(codex_result)["home"] == str(codex_home.path.resolve())


@pytest.mark.parametrize(
    ("query", "expected_code", "expected_match", "expected_id"),
    [
        (CODEX_IDS[0], 0, "id", CODEX_IDS[0]),
        ("dddd", 0, "partial-id", CODEX_IDS[2]),
        ("unique codex migration", 0, "name", CODEX_IDS[2]),
        ("migration", 0, "name", CODEX_IDS[2]),
        ("shared codex", 2, None, None),
        ("cccc", 2, None, None),
        ("absent codex", 1, None, None),
    ],
)
def test_codex_resolution_mirror(
    runner: CliRunner,
    codex_home: FakeHome,
    query: str,
    expected_code: int,
    expected_match: str | None,
    expected_id: str | None,
) -> None:
    result = _invoke(runner, query, codex_home.path, agent="codex")
    payload = _json(result)
    assert result.exit_code == expected_code
    if expected_match is not None:
        assert payload["matched_by"] == expected_match
        assert payload["session_id"] == expected_id
    elif expected_code == 2:
        assert payload["error"] == "ambiguous"
        assert payload["match_count"] == 2
        candidates = payload["candidates"]
        assert isinstance(candidates, list)
        assert {item["session_id"] for item in candidates} == set(CODEX_IDS[:2])
    else:
        assert payload["error"] == "not_found"


def test_json_success_shape_and_types(runner: CliRunner, codex_home: FakeHome) -> None:
    result = _invoke(
        runner,
        codex_home.ids[1],
        codex_home.path,
        agent="codex",
    )
    payload = _json(result)
    assert set(payload) == {
        "agent",
        "session_id",
        "name",
        "directory",
        "home",
        "session_file",
        "matched_by",
        "modified",
        "archived",
    }
    assert isinstance(payload["agent"], str)
    assert isinstance(payload["session_id"], str)
    assert isinstance(payload["name"], str)
    assert isinstance(payload["directory"], str)
    assert isinstance(payload["home"], str)
    assert isinstance(payload["session_file"], str)
    assert isinstance(payload["matched_by"], str)
    assert isinstance(payload["modified"], str)
    assert isinstance(payload["archived"], bool)
    assert payload["name"] == "Shared Codex"
    assert payload["directory"] == codex_home.directories[1]
    assert payload["session_file"] == str(codex_home.files[1].resolve())
    assert payload["archived"] is True


def test_json_and_pretty_flags_force_formats(
    runner: CliRunner, claude_home: FakeHome
) -> None:
    json_result = _invoke(
        runner,
        claude_home.ids[2],
        claude_home.path,
        extra=["--json"],
    )
    pretty_result = _invoke(
        runner,
        claude_home.ids[2],
        claude_home.path,
        extra=["--pretty"],
    )
    assert json_result.exit_code == 0
    assert _json(json_result)["session_id"] == claude_home.ids[2]
    assert pretty_result.exit_code == 0
    assert not pretty_result.output.lstrip().startswith("{")
    assert "Session" in pretty_result.output
    assert "Matched by" in pretty_result.output
    master, slave = os.openpty()
    command = [
        sys.executable, "-m", "claude_code_tools.aichat", "resolve",
        claude_home.ids[2], "--home", str(claude_home.path),
    ]
    process = subprocess.Popen(command, stdout=slave, stderr=slave)
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4_096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    tty_output = b"".join(chunks).decode(errors="replace")
    os.close(master)
    assert process.wait(timeout=5) == 0
    assert "Session" in tty_output
    assert not tty_output.lstrip().startswith("{")


def test_codex_stale_database_row_does_not_block_valid_match(
    runner: CliRunner, codex_home: FakeHome
) -> None:
    database = codex_home.path / "state_5.sqlite"
    missing_rollout = codex_home.path / "sessions" / "missing.jsonl"
    _insert_codex_thread(
        database,
        "eeee4444-4444-4444-8444-444444444444",
        missing_rollout,
        "/missing",
        "Stale Thread",
        1_720_000_100,
    )
    result = _invoke(
        runner,
        codex_home.ids[2],
        codex_home.path,
        agent="codex",
    )
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == codex_home.ids[2]


def test_codex_duplicate_id_rows_resolve_uniquely(
    runner: CliRunner, codex_home: FakeHome
) -> None:
    database = codex_home.path / "state_5.sqlite"
    _insert_codex_thread(
        database,
        codex_home.ids[0],
        codex_home.files[0],
        codex_home.directories[0],
        "Shared Codex",
        1_720_000_200,
    )
    result = _invoke(
        runner,
        codex_home.ids[0],
        codex_home.path,
        agent="codex",
    )
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == codex_home.ids[0]
    assert payload["matched_by"] == "id"


def test_claude_codex_named_home_and_duplicate_id(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude.codex-home"
    session_id = "eeee4444-4444-4444-8444-444444444444"
    old_cwd = str(tmp_path / "old-project")
    new_cwd = str(tmp_path / "new-project")
    _write_claude_session(home, session_id, old_cwd, "Old Copy", 100.0)
    newest = _write_claude_session(
        home, session_id, new_cwd, "Newest Copy", 200.0
    )
    exact = _invoke(runner, session_id, home)
    named = _invoke(runner, "Newest Copy", home)
    payload = _json(exact)
    assert exact.exit_code == named.exit_code == 0
    assert payload["directory"] == new_cwd
    assert payload["session_file"] == str(newest.resolve())
    assert _json(named)["session_id"] == session_id


@pytest.mark.parametrize("bad_cwd", [None, "", 17, {"bad": "cwd"}])
def test_claude_no_cwd_is_exact_id_only(
    runner: CliRunner,
    claude_home: FakeHome,
    tmp_path: Path,
    bad_cwd: object,
) -> None:
    session_id = "eeee5555-5555-4555-8555-555555555555"
    project_cwd = str(tmp_path / "project-without-cwd")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        project_cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": bad_cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "No Cwd Session",
            },
        ],
    )
    exact_result = _invoke(runner, session_id, claude_home.path)
    exact_payload = _json(exact_result)
    partial_result = _invoke(runner, "eeee", claude_home.path)
    name_result = _invoke(runner, "No Cwd Session", claude_home.path)
    valid_result = _invoke(runner, claude_home.ids[2], claude_home.path)
    assert exact_result.exit_code == 0
    assert exact_payload["matched_by"] == "id"
    assert exact_payload["directory"] is None
    assert exact_payload["name"] is None
    assert partial_result.exit_code == 1
    assert _json(partial_result)["error"] == "not_found"
    assert name_result.exit_code == 1
    assert _json(name_result)["error"] == "not_found"
    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == claude_home.ids[2]


def test_claude_non_object_json_before_valid_records_is_resolvable(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "ffff6666-6666-4666-8666-666666666666"
    cwd = str(tmp_path / "corrupt-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            None,
            [],
            42,
            "text",
            {"timestamp": {"malformed": True}},
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Corrupt Named Session",
            },
        ],
    )
    valid_result = _invoke(
        runner,
        claude_home.ids[2],
        claude_home.path,
    )
    exact_result = _invoke(runner, session_id, claude_home.path)
    partial_result = _invoke(runner, "ffff", claude_home.path)
    name_result = _invoke(runner, "Corrupt Named", claude_home.path)
    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == claude_home.ids[2]
    assert exact_result.exit_code == 0
    assert _json(exact_result)["matched_by"] == "id"
    assert partial_result.exit_code == 0
    assert _json(partial_result)["session_id"] == session_id
    assert name_result.exit_code == 0
    assert _json(name_result)["session_id"] == session_id


def test_claude_invalid_utf8_is_isolated(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "ffff7777-7777-4777-8777-777777777777"
    cwd = str(tmp_path / "invalid-utf8")
    session_file = _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [{"type": "user", "sessionId": session_id, "cwd": cwd}],
    )
    session_file.write_bytes(session_file.read_bytes() + b"\xff\n")
    valid_result = _invoke(runner, claude_home.ids[2], claude_home.path)
    partial_result = _invoke(runner, "ffff7777", claude_home.path)

    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == claude_home.ids[2]
    assert "Traceback" not in valid_result.output
    assert partial_result.exit_code == 1


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


@pytest.mark.parametrize("database_kind", ["corrupt", "incomplete"])
def test_codex_database_failure_falls_back_to_rollouts(
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
    assert result.exit_code == 0
    assert _json(result)["session_id"] == session_id


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
        lines=[{"type": "session_meta", "payload": payload}],
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
    result = _invoke(runner, valid_id, home, agent="codex")
    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id


def test_stale_claude_file_does_not_block_valid_match(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    payload_cwd = str(tmp_path / "moved-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        str(tmp_path / "original-project"),
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": payload_cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Moved Session",
            },
        ],
    )

    result = _invoke(runner, session_id, claude_home.path)
    name_result = _invoke(runner, "Moved Session", claude_home.path)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["name"] == "Moved Session"
    assert name_result.exit_code == 0
    assert _json(name_result)["session_id"] == session_id


def test_stale_codex_fallback_file_does_not_block_valid_match(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    session_id = "eeeecccc-cccc-4ccc-8ccc-cccccccccccc"
    valid = _write_codex_session(home, session_id, "/valid", 1_720_000_600.0)
    stale = valid.parent / (
        "rollout-2026-07-14T10-00-00-ffffdddd-dddd-4ddd-8ddd-dddddddddddd.jsonl"
    )
    os.mkfifo(stale)
    entry = json.dumps(
        {"type": "session_meta", "payload": {"id": "stale", "cwd": "/stale"}}
    )
    encoded_entry = f"{entry}\n".encode()
    def feed_fifo_three_times() -> None:
        for attempt in range(3):
            descriptor = os.open(stale, os.O_WRONLY)
            if attempt == 2:
                stale.unlink()
            try:
                os.write(descriptor, encoded_entry)
            except BrokenPipeError:
                pass
            finally:
                os.close(descriptor)

    writer = threading.Thread(target=feed_fifo_three_times)
    writer.start()
    result = _invoke(runner, session_id, home, agent="codex")
    writer.join(timeout=2)
    assert not writer.is_alive()
    assert result.exit_code == 0, result.output
    assert _json(result)["session_id"] == session_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, False), (1, True), (None, False), ("0", False), ("1", True)],
)
def test_codex_archived_value_normalization(value: object, expected: bool) -> None:
    assert _normalize_archived(value) is expected


def test_claude_sidechain_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "eeee8888-8888-4888-8888-888888888888"
    cwd = str(tmp_path / "sidechain-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "isSidechain": True,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Hidden Sidechain",
            },
        ],
    )

    exact_result = _invoke(runner, session_id, claude_home.path)
    partial_result = _invoke(runner, "eeee", claude_home.path)
    name_result = _invoke(runner, "Hidden Sidechain", claude_home.path)
    assert exact_result.exit_code == 0
    assert _json(exact_result)["matched_by"] == "id"
    assert partial_result.exit_code == 1
    assert _json(partial_result)["error"] == "not_found"
    assert name_result.exit_code == 1
    assert _json(name_result)["error"] == "not_found"


def test_claude_ai_title_is_not_a_name(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "dddd9999-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "ai-title-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "ai-title",
                "sessionId": session_id,
                "aiTitle": "Automatic Only Title",
            },
        ],
    )

    exact_result = _invoke(runner, session_id, claude_home.path)
    title_result = _invoke(runner, "Automatic Only", claude_home.path)
    assert exact_result.exit_code == 0
    assert _json(exact_result)["name"] is None
    assert title_result.exit_code == 1
    assert _json(title_result)["error"] == "not_found"
