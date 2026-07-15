"""Tests for the non-interactive ``aichat resolve`` command."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
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
    session_id: object,
    rollout_path: object,
    cwd: object,
    title: object,
    updated_at: int,
    archived: object = False,
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
                str(rollout_path) if isinstance(rollout_path, Path) else rollout_path,
                cwd,
                title,
                archived,
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


def _run_in_tty(arguments: list[str]) -> tuple[int, str]:
    """Run the CLI with a pseudo-terminal attached to stdout."""
    master, slave = os.openpty()
    command = [sys.executable, "-m", "claude_code_tools.aichat", *arguments]
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
    os.close(master)
    return process.wait(timeout=5), b"".join(chunks).decode(errors="replace")


def _run_cli(
    arguments: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the real module entry point with an isolated environment."""
    return subprocess.run(
        [sys.executable, "-m", "claude_code_tools.aichat", *arguments],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=20,
    )


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


@pytest.mark.parametrize(
    ("query", "winning_id", "matched_by"),
    [
        (
            "face0000-0000-4000-8000-000000000001",
            "face0000-0000-4000-8000-000000000001",
            "id",
        ),
        ("cafe", "22222222-2222-4222-8222-222222222222", "name"),
        (
            "beef",
            "beef0000-0000-4000-8000-000000000003",
            "partial-id",
        ),
    ],
)
def test_claude_first_nonempty_tier_wins_cross_tier_collisions(
    runner: CliRunner,
    tmp_path: Path,
    query: str,
    winning_id: str,
    matched_by: str,
) -> None:
    home = tmp_path / "claude"
    sessions = (
        (
            "face0000-0000-4000-8000-000000000001",
            "Ordinary Session",
        ),
        (
            "11111111-1111-4111-8111-111111111111",
            "face0000-0000-4000-8000-000000000001",
        ),
        ("cafe0000-0000-4000-8000-000000000002", "Other Cafe"),
        ("22222222-2222-4222-8222-222222222222", "cafe"),
        ("beef0000-0000-4000-8000-000000000003", "Prefix Winner"),
        ("33333333-3333-4333-8333-333333333333", "Roast Beef Notes"),
    )
    for index, (session_id, title) in enumerate(sessions):
        _write_claude_session(
            home,
            session_id,
            str(tmp_path / f"project-{index}"),
            title,
            1_720_000_000.0 + index,
        )

    result = _invoke(runner, query, home)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == winning_id
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


def test_claude_ambiguity_is_newest_first_and_capped(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude"
    session_ids: list[str] = []
    mtimes: dict[str, float] = {}
    for index in range(30):
        session_id = f"{index:08x}-0000-4000-8000-{index:012x}"
        mtime = 1_720_000_000.0 + index
        session_ids.append(session_id)
        mtimes[session_id] = mtime
        _write_claude_session(
            home,
            session_id,
            str(tmp_path / f"project-{index}"),
            "Crowded Name",
            mtime,
        )

    result = _invoke(runner, "Crowded Name", home)
    payload = _json(result)
    candidates = payload["candidates"]

    assert result.exit_code == 2
    assert payload["error"] == "ambiguous"
    assert payload["query"] == "Crowded Name"
    assert payload["agent"] == "claude"
    assert payload["match_count"] == 30
    assert isinstance(candidates, list)
    assert len(candidates) == 25
    expected_ids = list(reversed(session_ids))[:25]
    assert [candidate["session_id"] for candidate in candidates] == expected_ids
    for candidate in candidates:
        assert set(candidate) == {
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
        parsed = datetime.fromisoformat(candidate["modified"])
        assert parsed.timestamp() == pytest.approx(
            mtimes[candidate["session_id"]]
        )


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
    ("agent", "option"),
    [("claude", "--claude-home"), ("codex", "--codex-home")],
)
def test_top_level_home_options_apply_to_resolve(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    agent: str,
    option: str,
) -> None:
    fake_home = claude_home if agent == "claude" else codex_home
    result = runner.invoke(
        main,
        [
            option,
            str(fake_home.path),
            "resolve",
            fake_home.ids[2],
            "--agent",
            agent,
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert _json(result)["home"] == str(fake_home.path.resolve())


def test_local_home_overrides_top_level_home(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    other_home = tmp_path / "other-claude"
    other_id = "ffff0000-0000-4000-8000-000000000000"
    _write_claude_session(
        other_home,
        other_id,
        str(tmp_path / "other-project"),
        "Other Session",
        1_720_000_000.0,
    )
    result = runner.invoke(
        main,
        [
            "--claude-home",
            str(claude_home.path),
            "resolve",
            other_id,
            "--home",
            str(other_home),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert _json(result)["home"] == str(other_home.resolve())


def test_resolve_skips_auto_index_without_changing_other_commands(
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    claude_home = tmp_path / "selected-claude"
    codex_home = tmp_path / "selected-codex"
    codex_home.mkdir()
    wrong_claude_home = tmp_path / "environment-claude"
    wrong_codex_home = tmp_path / "environment-codex"
    wrong_codex_home.mkdir()
    session_id = "aaaa9999-9999-4999-8999-999999999999"
    selected_session = _write_claude_session(
        claude_home,
        session_id,
        str(tmp_path / "selected-project"),
        "Selected Session",
        1_720_000_000.0,
    )
    wrong_session = _write_claude_session(
        wrong_claude_home,
        "bbbb9999-9999-4999-8999-999999999999",
        str(tmp_path / "wrong-project"),
        "Wrong Session",
        1_720_000_001.0,
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(user_home),
            "CLAUDE_CONFIG_DIR": str(wrong_claude_home),
            "CODEX_HOME": str(wrong_codex_home),
        }
    )
    index_path = user_home / ".cctools" / "search-index"

    resolve_result = _run_cli(
        ["resolve", session_id, "--home", str(claude_home), "--json"],
        env,
    )

    assert resolve_result.returncode == 0
    assert not index_path.exists()

    search_result = _run_cli(
        [
            "search",
            "--claude-home",
            str(claude_home),
            "--codex-home",
            str(codex_home),
            "--json",
            "--help",
        ],
        env,
    )

    assert search_result.returncode == 0
    assert "Indexed " not in search_result.stdout + search_result.stderr
    state = json.loads((index_path / "index_state.json").read_text())
    assert str(selected_session) in state
    assert str(wrong_session) not in state


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
    modified = datetime.fromisoformat(payload["modified"])
    assert modified.timestamp() == pytest.approx(
        codex_home.files[1].stat().st_mtime
    )
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
    exit_code, tty_output = _run_in_tty(
        [
            "resolve",
            claude_home.ids[2],
            "--home",
            str(claude_home.path),
        ]
    )
    assert exit_code == 0
    assert "Session" in tty_output
    assert not tty_output.lstrip().startswith("{")
    exit_code, tty_json = _run_in_tty(
        [
            "resolve",
            claude_home.ids[2],
            "--home",
            str(claude_home.path),
            "--json",
        ]
    )
    assert exit_code == 0
    assert json.loads(tty_json)["session_id"] == claude_home.ids[2]


def test_pretty_not_found_escapes_hostile_query(
    runner: CliRunner, claude_home: FakeHome
) -> None:
    """Render a markup-like not-found query as literal text."""
    result = _invoke(
        runner,
        "[/]",
        claude_home.path,
        extra=["--pretty"],
    )

    assert result.exit_code == 1
    assert "No session found" in result.output
    assert "[/]" in result.output
    assert "Traceback" not in result.output


def test_pretty_success_escapes_hostile_codex_metadata(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Render database strings containing malformed tags as literal text."""
    home = tmp_path / "[" / "home]"
    home.mkdir(parents=True)
    rollout_id = "eeee5555-5555-4555-8555-555555555555"
    rollout = _write_codex_session(
        home,
        rollout_id,
        "[/rollout-directory]",
        1_720_000_000.0,
    )
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    session_id = "hostile[/id]"
    _insert_codex_thread(
        database,
        session_id,
        rollout,
        "[/directory]",
        "Hostile [/name]",
        1_720_000_000,
    )

    result = _invoke(
        runner,
        session_id,
        home,
        agent="codex",
        extra=["--pretty"],
    )

    assert result.exit_code == 0
    assert session_id in result.output
    assert "Hostile [/name]" in result.output
    assert "[/directory]" in result.output
    assert "Home" in result.output
    assert "Session file" in result.output
    assert "Traceback" not in result.output


def test_pretty_error_escapes_hostile_detail(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Render an expected error detail containing malformed tags literally."""
    bad_home = tmp_path / "[" / "bad-home]"
    bad_home.parent.mkdir()
    bad_home.write_text("not a directory", encoding="utf-8")

    result = _invoke(
        runner,
        "anything",
        bad_home,
        extra=["--pretty"],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "[/bad-home]" in result.output
    assert "Traceback" not in result.output


def test_json_and_pretty_are_mutually_exclusive(
    runner: CliRunner, claude_home: FakeHome
) -> None:
    result = _invoke(
        runner,
        claude_home.ids[2],
        claude_home.path,
        extra=["--json", "--pretty"],
    )
    payload = _json(result)

    assert result.exit_code == 1
    assert payload == {
        "error": "invalid_format",
        "detail": "Choose only one of --json or --pretty.",
    }
    assert "Traceback" not in result.output


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


@pytest.mark.parametrize("bad_id", [None, 17, [], {"id": "bad"}])
def test_claude_non_string_transcript_id_is_exact_id_only(
    runner: CliRunner,
    claude_home: FakeHome,
    tmp_path: Path,
    bad_id: object,
) -> None:
    filename_id = "dede5555-5555-4555-8555-555555555555"
    cwd = str(tmp_path / "project-with-bad-id")
    _write_raw_claude_session(
        claude_home.path,
        filename_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": bad_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": bad_id,
                "customTitle": "Malformed Identifier",
            },
        ],
    )

    exact = _invoke(runner, filename_id, claude_home.path)
    partial = _invoke(runner, "dede", claude_home.path)
    named = _invoke(runner, "Malformed Identifier", claude_home.path)
    valid = _invoke(runner, claude_home.ids[2], claude_home.path)

    assert exact.exit_code == 0
    assert _json(exact)["matched_by"] == "id"
    assert partial.exit_code == 1
    assert named.exit_code == 1
    assert valid.exit_code == 0
    assert _json(valid)["session_id"] == claude_home.ids[2]


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


def test_claude_malformed_jsonl_before_between_and_after_valid_records(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fefe6666-6666-4666-8666-666666666666"
    cwd = str(tmp_path / "malformed-json-project")
    session_file = _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [],
    )
    valid_user = json.dumps(
        {"type": "user", "sessionId": session_id, "cwd": cwd}
    )
    valid_title = json.dumps(
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": "Malformed JSON Survivor",
        }
    )
    session_file.write_text(
        "{not valid json}\n"
        f"{valid_user}\n"
        '["unterminated"\n'
        f"{valid_title}\n"
        "not-json-after-valid-records\n",
        encoding="utf-8",
    )

    named = _invoke(runner, "Malformed JSON Survivor", claude_home.path)
    valid = _invoke(runner, claude_home.ids[2], claude_home.path)

    assert named.exit_code == 0
    assert _json(named)["session_id"] == session_id
    assert valid.exit_code == 0
    assert _json(valid)["session_id"] == claude_home.ids[2]
    assert "Traceback" not in named.output + valid.output


def test_claude_malformed_nested_metadata_after_valid_records(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fefe7777-7777-4777-8777-777777777777"
    cwd = str(tmp_path / "nested-metadata-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "progress",
                "cwd": {"wrong": "shape"},
                "trim_metadata": [],
                "continue_metadata": "invalid",
            },
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "file-history-snapshot",
                "metadata": {"git": []},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Nested Metadata Survivor",
            },
            None,
            [],
            {"timestamp": ["wrong", "shape"]},
        ],
    )

    result = _invoke(runner, "Nested Metadata Survivor", claude_home.path)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert "Traceback" not in result.output


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


def test_codex_empty_database_ignores_unindexed_rollouts(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    _create_threads_database(home / "state_1.sqlite")
    session_id = "eeee7788-7788-4788-8788-778877887788"
    _write_codex_session(
        home,
        session_id,
        str(tmp_path / "fallback-project"),
        1_720_000_310.0,
    )

    result = _invoke(runner, session_id, home, agent="codex")
    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"


def test_codex_database_ignores_unindexed_rollout(
    runner: CliRunner, tmp_path: Path
) -> None:
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
    assert omitted_result.exit_code == 1
    assert _json(omitted_result)["error"] == "not_found"


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


def test_claude_agent_filename_sidechain_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    filename_id = "agent-fafa8888-8888-4888-8888-888888888888"
    cwd = str(tmp_path / "filename-sidechain")
    _write_raw_claude_session(
        claude_home.path,
        filename_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": "fafa8888-8888-4888-8888-888888888888",
                "cwd": cwd,
            },
            {
                "type": "custom-title",
                "customTitle": "Filename Sidechain",
            },
        ],
    )

    exact = _invoke(runner, filename_id, claude_home.path)
    named = _invoke(runner, "Filename Sidechain", claude_home.path)

    assert exact.exit_code == 0
    assert _json(exact)["matched_by"] == "id"
    assert named.exit_code == 1


def test_claude_late_sidechain_marker_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fafa9999-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "late-sidechain")
    lines: list[object] = [
        {"type": "user", "sessionId": session_id, "cwd": cwd},
        *({"type": "progress", "index": index} for index in range(12)),
        {"type": "progress", "isSidechain": True},
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": "Late Sidechain",
        },
    ]
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        lines,
    )

    exact = _invoke(runner, session_id, claude_home.path)
    partial = _invoke(runner, "fafa", claude_home.path)
    named = _invoke(runner, "Late Sidechain", claude_home.path)

    assert exact.exit_code == 0
    assert _json(exact)["matched_by"] == "id"
    assert partial.exit_code == 1
    assert named.exit_code == 1


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


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_unreadable_home_is_a_structured_error(
    runner: CliRunner, tmp_path: Path, agent: str
) -> None:
    home = tmp_path / f"unreadable-{agent}"
    home.mkdir()
    (home / "sentinel").write_text("unreadable", encoding="utf-8")
    home.chmod(0)
    try:
        try:
            next(home.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("directory permissions cannot be enforced")

        result = _invoke(runner, "anything", home, agent=agent)
        payload = _json(result)

        assert result.exit_code == 1
        assert set(payload) == {"error", "detail"}
        assert payload["error"] == "unreadable_home"
        assert isinstance(payload["detail"], str)
        assert payload["detail"]
        assert "Traceback" not in result.output
    finally:
        home.chmod(0o700)


def test_claude_wrong_typed_record_type_does_not_block_valid_match(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude"
    hostile_id = "face1000-0000-4000-8000-000000000001"
    _write_raw_claude_session(
        home,
        hostile_id,
        str(tmp_path / "hostile"),
        [{"type": [], "sessionId": None}],
    )
    valid_id = "face2000-0000-4000-8000-000000000002"
    _write_claude_session(
        home,
        valid_id,
        str(tmp_path / "valid"),
        "Valid After Wrong Type",
        1_720_001_000.0,
    )

    result = _invoke(runner, "Valid After Wrong Type", home)

    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id
    assert "Traceback" not in result.output


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


@pytest.mark.parametrize(
    "message",
    [
        pytest.param([], id="non-dict-message"),
        pytest.param({"content": 17}, id="non-string-or-list-content"),
        pytest.param(
            {"content": [{"type": "text", "text": 17}]},
            id="non-string-text-block",
        ),
    ],
)
def test_claude_hostile_message_metadata_is_isolated(
    runner: CliRunner, tmp_path: Path, message: object
) -> None:
    home = tmp_path / "claude"
    hostile_id = "face5000-0000-4000-8000-000000000005"
    hostile_cwd = str(tmp_path / "hostile")
    _write_raw_claude_session(
        home,
        hostile_id,
        hostile_cwd,
        [
            {
                "type": "user",
                "sessionId": hostile_id,
                "cwd": hostile_cwd,
                "message": message,
            }
        ],
    )
    valid_id = "face6000-0000-4000-8000-000000000006"
    _write_claude_session(
        home,
        valid_id,
        str(tmp_path / "valid"),
        "Valid After Hostile Message",
        1_720_001_010.0,
    )

    result = _invoke(runner, "Valid After Hostile Message", home)

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


@pytest.mark.parametrize("contents", [b"", b"not-json\n[truncated\n"])
def test_claude_invalid_transcript_does_not_resolve_by_exact_filename(
    runner: CliRunner, tmp_path: Path, contents: bytes
) -> None:
    home = tmp_path / "claude"
    session_id = "face9000-0000-4000-8000-000000000009"
    project = home / "projects" / "-invalid"
    project.mkdir(parents=True)
    transcript = project / f"{session_id}.jsonl"
    transcript.write_bytes(contents)

    result = _invoke(runner, session_id, home)

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"
    assert "Traceback" not in result.output


def test_unreadable_claude_transcript_does_not_resolve_by_filename(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude"
    session_id = "facea000-0000-4000-8000-00000000000a"
    transcript = _write_raw_claude_session(
        home,
        session_id,
        str(tmp_path / "unreadable"),
        [{"type": "user", "sessionId": session_id}],
    )
    transcript.chmod(0)
    try:
        try:
            transcript.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("file permissions cannot be enforced")

        result = _invoke(runner, session_id, home)

        assert result.exit_code == 1
        assert _json(result)["error"] == "not_found"
        assert "Traceback" not in result.output
    finally:
        transcript.chmod(0o600)


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
