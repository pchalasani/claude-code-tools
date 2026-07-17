"""Tests for the non-interactive ``aichat resolve`` command.

Shared fixtures and helpers live in
:mod:`tests.resolve_session_helpers`. Codex database/fallback tests
and hostile-transcript hardening tests live in the sibling modules
``test_resolve_session_codex.py`` and
``test_resolve_session_hardening.py`` (split for the repo's
1000-line file limit).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from tests.resolve_session_helpers import (
    CLAUDE_IDS,
    CODEX_IDS,
    FakeHome,
    _create_threads_database,
    _insert_codex_thread,
    _invoke,
    _json,
    _run_cli,
    _run_in_tty,
    _write_claude_session,
    _write_codex_session,
    _write_raw_claude_session,
    claude_home,
    codex_home,
    runner,
)

__all__ = ["claude_home", "codex_home", "runner"]


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
    """Render database strings containing malformed tags as literal text.

    The queried id itself must stay free of path separators and glob
    metacharacters — the contract rejects such queries outright — so
    the hostile markup lives in the name, directory, and home path.
    """
    home = tmp_path / "[" / "home]"
    home.mkdir(parents=True)
    # The row id must agree with the id encoded in the rollout's
    # filename: mismatched rows are rejected as stale/corrupt.
    session_id = "hostile-markup-id"
    rollout = _write_codex_session(
        home,
        session_id,
        "[/rollout-directory]",
        1_720_000_000.0,
    )
    database = home / "state_1.sqlite"
    _create_threads_database(database)
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
