"""Tests for the id-substring and filename-substring resolver tiers.

Covers the extended ``aichat resolve`` tier ladder:

1. exact id, 2. exact name, 3. id prefix, 4. id substring,
5. session-file name substring, 6. name substring.

Kept separate from ``test_resolve_session.py`` to respect the
repo's file-length limit. Reuses that module's fixture helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
from tests.resolve_session_helpers import (
    _create_threads_database,
    _insert_codex_thread,
    _write_claude_session,
    _write_codex_session,
    _write_raw_claude_session,
)


@pytest.fixture
def runner() -> CliRunner:
    """Return an isolated in-process Click runner."""
    return CliRunner()


def _invoke(
    runner: CliRunner,
    query: str,
    home: Path,
    agent: str = "claude",
) -> Result:
    """Invoke ``aichat resolve`` against one explicit home."""
    return runner.invoke(
        main,
        ["resolve", query, "--agent", agent, "--home", str(home)],
        catch_exceptions=False,
    )


def _json(result: Result) -> dict[str, object]:
    """Decode a command's single JSON object."""
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _codex_home_with_db(tmp_path: Path) -> Path:
    """Create an empty Codex home holding an empty threads database."""
    home = tmp_path / "codex"
    home.mkdir()
    _create_threads_database(home / "state_1.sqlite")
    return home


def _write_legacy_rollout(home: Path, session_id: str) -> Path:
    """Write one legacy 2025-format Codex rollout file."""
    day_dir = home / "sessions" / "2025" / "08" / "27"
    day_dir.mkdir(parents=True, exist_ok=True)
    rollout = day_dir / (
        f"rollout-2025-08-27T19-44-13-{session_id}.jsonl"
    )
    lines = [
        {
            "id": session_id,
            "timestamp": "2025-08-27T19:44:13.169Z",
            "instructions": None,
            "git": {"branch": "legacy-branch"},
        },
        {"record_type": "state"},
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Legacy question"}],
        },
    ]
    rollout.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    return rollout


def test_id_prefix_beats_id_substring(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A prefix match wins outright over a mid-id substring match."""
    home = tmp_path / "claude"
    prefix_id = "feed0000-1111-4111-8111-111111111111"
    substring_id = "0000feed-2222-4222-8222-222222222222"
    for index, session_id in enumerate((prefix_id, substring_id)):
        _write_claude_session(
            home,
            session_id,
            str(tmp_path / f"proj-{index}"),
            f"Tier Session {index}",
            1_720_000_000.0 + index,
        )

    result = _invoke(runner, "feed", home)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == prefix_id
    assert payload["matched_by"] == "partial-id"


def test_id_substring_resolves_mid_fragment(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A mid-id fragment spanning a dash resolves via id-substring."""
    home = tmp_path / "claude"
    session_id = "fead0000-6c6c-7120-8000-000000000001"
    _write_claude_session(
        home,
        session_id,
        str(tmp_path / "proj-mid"),
        "Mid Fragment",
        1_720_000_000.0,
    )
    _write_claude_session(
        home,
        "bead0000-1111-4111-8111-111111111111",
        str(tmp_path / "proj-other"),
        "Other Session",
        1_720_000_001.0,
    )

    result = _invoke(runner, "6c6c-7120", home)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "id-substring"


def test_id_substring_resolves_suffix_fragment(
    runner: CliRunner, tmp_path: Path
) -> None:
    session_id = "fead0000-1111-4111-8111-733355557333"
    home = tmp_path / "claude"
    _write_claude_session(
        home,
        session_id,
        str(tmp_path / "proj-suffix"),
        "Suffix Fragment",
        1_720_000_000.0,
    )

    result = _invoke(runner, "733355557333", home)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "id-substring"


@pytest.mark.parametrize("query", ["6c6", "6c/6c", "6c\\6c"])
def test_id_substring_gates_reject_short_and_separator_queries(
    runner: CliRunner, tmp_path: Path, query: str
) -> None:
    home = tmp_path / "claude"
    _write_claude_session(
        home,
        "fead0000-6c6c-7120-8000-000000000001",
        str(tmp_path / "proj"),
        "Gated Session",
        1_720_000_000.0,
    )

    result = _invoke(runner, query, home)

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"


def test_filename_substring_beats_name_substring(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A filename-fragment match shadows name substrings (tier 5 < 6).

    Regression for a real-world failure: sessions whose free-text
    names quote a rollout timestamp (e.g. review agents whose first
    message embeds "2026-03-25T14-50") drowned the single structural
    filename match under dozens of name-substring candidates.
    """
    home = _codex_home_with_db(tmp_path)
    database = home / "state_1.sqlite"
    named_id = "cafe0000-1111-4111-8111-111111111111"
    other_id = "cafe0000-2222-4222-8222-222222222222"
    named = _write_codex_session(
        home, named_id, "/named", 1_720_000_000.0
    )
    other = _write_codex_session(
        home, other_id, "/other", 1_720_000_001.0
    )
    # The query appears in ONE session's FILENAME (other_id's rollout,
    # via its embedded id) and in the OTHER session's NAME; the
    # structural filename match must win alone.
    query = "14T10-00-00-cafe0000-2222"
    _insert_codex_thread(
        database, named_id, named, "/named",
        f"notes quoting {query} planning", 1_720_000_000,
    )
    _insert_codex_thread(
        database, other_id, other, "/other", "Other Thread", 1_720_000_001
    )

    result = _invoke(runner, query, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == other_id
    assert payload["matched_by"] == "filename"


def test_filename_substring_timestamp_fragment(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A codex rollout timestamp fragment resolves via the filename tier."""
    home = tmp_path / "codex"
    session_id = "cafe0000-3333-4333-8333-333333333333"
    day_dir = home / "sessions" / "2026" / "03" / "25"
    day_dir.mkdir(parents=True)
    rollout = day_dir / (
        f"rollout-2026-03-25T14-50-00-{session_id}.jsonl"
    )
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": "/timestamped"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = _invoke(runner, "2026-03-25T14-50", home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "filename"
    assert payload["session_file"] == str(rollout.resolve())


@pytest.mark.parametrize(
    "query", ["2026-03-25t14-50", "ROLLOUT-2026-03-25T14-50"]
)
def test_filename_substring_matching_is_case_insensitive(
    runner: CliRunner, tmp_path: Path, query: str
) -> None:
    """Filename fragments match regardless of query casing.

    The rollout filename contains an uppercase ``T`` timestamp
    separator and a lowercase ``rollout-`` prefix; queries with the
    opposite casing must still resolve via the filename tier.
    """
    home = tmp_path / "codex"
    session_id = "cafe0000-8888-4888-8888-888888888888"
    day_dir = home / "sessions" / "2026" / "03" / "25"
    day_dir.mkdir(parents=True)
    rollout = day_dir / (
        f"rollout-2026-03-25T14-50-00-{session_id}.jsonl"
    )
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": "/case-insensitive"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = _invoke(runner, query, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "filename"
    assert payload["session_file"] == str(rollout.resolve())


def test_filename_substring_rollout_prefix_fragment(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    session_id = "cafe0000-4444-4444-8444-444444444444"
    _write_codex_session(home, session_id, "/rollout", 1_720_000_000.0)

    result = _invoke(
        runner, "rollout-2026-07-14T10-00", home, agent="codex"
    )
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "filename"


def test_filename_substring_ambiguity_lists_all_candidates(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "codex"
    ids = (
        "cafe0000-5555-4555-8555-555555555555",
        "cafe0000-6666-4666-8666-666666666666",
    )
    for index, session_id in enumerate(ids):
        _write_codex_session(
            home, session_id, f"/proj-{index}", 1_720_000_000.0 + index
        )

    result = _invoke(runner, "2026-07-14T10-00", home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 2
    assert payload["error"] == "ambiguous"
    assert payload["match_count"] == 2
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    assert {item["session_id"] for item in candidates} == set(ids)
    assert {item["matched_by"] for item in candidates} == {"filename"}


@pytest.mark.parametrize("query", ["07/14", "2026\\07"])
def test_filename_tier_rejects_path_separator_queries(
    runner: CliRunner, tmp_path: Path, query: str
) -> None:
    home = tmp_path / "codex"
    _write_codex_session(
        home,
        "cafe0000-7777-4777-8777-777777777777",
        "/gated",
        1_720_000_000.0,
    )

    result = _invoke(runner, query, home, agent="codex")

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"


@pytest.mark.parametrize(
    "hostile_name",
    ["a/b", "a\\b", "*", "??", "has*star", "what?", "[a-z]"],
)
def test_separator_and_glob_queries_never_match_matching_names(
    runner: CliRunner, tmp_path: Path, hostile_name: str
) -> None:
    """Rejected queries match NOTHING, even an exactly-equal name.

    A claude session whose custom title literally equals the query
    must still resolve to not_found: path separators and glob
    metacharacters are rejected before any tier runs.
    """
    home = tmp_path / "claude"
    _write_claude_session(
        home,
        "abcd0000-1111-4111-8111-111111111111",
        str(tmp_path / "proj-hostile"),
        hostile_name,
        1_720_000_000.0,
    )

    exact_result = _invoke(runner, hostile_name, home)

    assert exact_result.exit_code == 1
    assert _json(exact_result)["error"] == "not_found"


@pytest.mark.parametrize("hostile_name", ["a/b", "has*star", "what?"])
def test_separator_and_glob_queries_never_match_codex_titles(
    runner: CliRunner, tmp_path: Path, hostile_name: str
) -> None:
    """Codex thread titles equal to a rejected query never match."""
    home = _codex_home_with_db(tmp_path)
    database = home / "state_1.sqlite"
    session_id = "abcd0000-2222-4222-8222-222222222222"
    rollout = _write_codex_session(
        home, session_id, "/hostile-title", 1_720_000_000.0
    )
    _insert_codex_thread(
        database,
        session_id,
        rollout,
        "/hostile-title",
        hostile_name,
        1_720_000_000,
    )

    result = _invoke(runner, hostile_name, home, agent="codex")

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"


def test_sidechain_sessions_excluded_from_new_tiers(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Ineligible sessions stay exact-id-only under tiers 4 and 6."""
    home = tmp_path / "claude"
    session_id = "faded000-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "sidechain-project")
    _write_raw_claude_session(
        home,
        session_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "isSidechain": True,
                "message": {"content": "hello"},
            }
        ],
    )

    mid_result = _invoke(runner, "9999-4999", home)
    filename_result = _invoke(runner, "faded000-9999", home)
    exact_result = _invoke(runner, session_id, home)

    assert mid_result.exit_code == 1
    assert filename_result.exit_code == 1
    assert exact_result.exit_code == 0
    assert _json(exact_result)["matched_by"] == "id"


def test_matchkind_json_output_is_stable(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Existing kinds keep their values; new kinds join the same shape."""
    home = tmp_path / "claude"
    session_id = "beef0000-1111-4111-8111-222233334444"
    _write_claude_session(
        home,
        session_id,
        str(tmp_path / "proj-stable"),
        "Stability Check",
        1_720_000_000.0,
    )
    expected_keys = {
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
    cases = (
        (session_id, "id"),
        ("Stability Check", "name"),
        ("beef", "partial-id"),
        ("1111-4111", "id-substring"),
        ("beef0000-1111-4111-8111-222233334444.jsonl", "filename"),
    )
    for query, matched_by in cases:
        payload = _json(_invoke(runner, query, home))
        assert set(payload) == expected_keys, query
        assert payload["matched_by"] == matched_by, query
        assert payload["session_id"] == session_id, query


def test_legacy_codex_rollout_resolves_by_id(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Legacy 2025-format rollouts are enumerated from disk."""
    home = tmp_path / "codex"
    session_id = "1d18f22c-9a88-4ea9-ae77-4094c9f87aaa"
    rollout = _write_legacy_rollout(home, session_id)

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "id"
    assert payload["session_file"] == str(rollout.resolve())


def test_legacy_rollout_indexed_in_state_db_resolves_by_title(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A legacy rollout indexed in SQLite keeps its database metadata.

    The database enumeration must accept legacy 2025-format rollouts
    exactly like the disk fallback does; otherwise the fallback's
    nameless record replaces the row and the thread's authoritative
    title, cwd, and archived state are lost, breaking exact-name and
    name-substring resolution.
    """
    home = tmp_path / "codex"
    home.mkdir()
    database = home / "state_1.sqlite"
    _create_threads_database(database)
    session_id = "2d18f22c-9a88-4ea9-ae77-4094c9f87bbb"
    rollout = _write_legacy_rollout(home, session_id)
    _insert_codex_thread(
        database,
        session_id,
        rollout,
        "/legacy-db-cwd",
        "Legacy Indexed Title",
        1_720_000_000,
        archived=True,
    )

    exact = _invoke(runner, "Legacy Indexed Title", home, agent="codex")
    substring = _invoke(runner, "legacy indexed", home, agent="codex")

    for result in (exact, substring):
        payload = _json(result)
        assert result.exit_code == 0
        assert payload["session_id"] == session_id
        assert payload["matched_by"] == "name"
        assert payload["name"] == "Legacy Indexed Title"
        assert payload["directory"] == "/legacy-db-cwd"
        assert payload["archived"] is True
        assert payload["session_file"] == str(rollout.resolve())


def _append_index(home: Path, *entries: object) -> None:
    """Append raw entries to the home's session_index.jsonl."""
    index = home / "session_index.jsonl"
    with open(index, "a", encoding="utf-8") as handle:
        for entry in entries:
            if isinstance(entry, str):
                handle.write(entry + "\n")
            else:
                handle.write(json.dumps(entry) + "\n")


def test_explicit_thread_name_resolves_from_session_index(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A session_index.jsonl thread_name resolves as an exact name.

    Regression: `aichat port typ2latex` failed to find a codex
    session whose explicit thread name lived only in
    session_index.jsonl (the state DB title is just the auto-captured
    first user message).
    """
    home = _codex_home_with_db(tmp_path)
    database = home / "state_1.sqlite"
    session_id = "beef1111-1111-4111-8111-111111111111"
    rollout = _write_codex_session(home, session_id, "/paper", 1_720_000_000.0)
    _insert_codex_thread(
        database, session_id, rollout, "/paper",
        "look into open-source tools to convert typ to latex",
        1_720_000_000,
    )
    _append_index(
        home,
        {"id": session_id, "thread_name": "typ2latex",
         "updated_at": "2026-07-14T15:30:36Z"},
    )

    result = _invoke(runner, "typ2latex", home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "name"
    assert payload["name"] == "typ2latex"


def test_session_index_overrides_database_title_and_later_entry_wins(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Explicit thread names beat DB titles; last index entry wins."""
    home = _codex_home_with_db(tmp_path)
    database = home / "state_1.sqlite"
    session_id = "beef2222-2222-4222-8222-222222222222"
    rollout = _write_codex_session(home, session_id, "/x", 1_720_000_000.0)
    _insert_codex_thread(
        database, session_id, rollout, "/x", "Old DB Title", 1_720_000_000
    )
    _append_index(
        home,
        {"id": session_id, "thread_name": "first-name", "updated_at": "z"},
        {"id": session_id, "thread_name": "renamed-later", "updated_at": "z"},
    )

    renamed = _invoke(runner, "renamed-later", home, agent="codex")
    assert _json(renamed)["session_id"] == session_id
    assert _json(renamed)["name"] == "renamed-later"

    old_title = _invoke(runner, "Old DB Title", home, agent="codex")
    assert _json(old_title)["error"] == "not_found"


def test_session_index_tolerates_garbage_and_missing_file(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Malformed index lines are skipped; valid entries still apply."""
    home = _codex_home_with_db(tmp_path)
    database = home / "state_1.sqlite"
    session_id = "beef3333-3333-4333-8333-333333333333"
    rollout = _write_codex_session(home, session_id, "/y", 1_720_000_000.0)
    _insert_codex_thread(
        database, session_id, rollout, "/y", "Some Title", 1_720_000_000
    )
    _append_index(
        home,
        "not json at all {",
        "[1, 2, 3]",
        {"id": None, "thread_name": "no-id"},
        {"id": session_id, "thread_name": "   "},
        {"id": "", "thread_name": "empty-id"},
        {"id": session_id, "thread_name": "good-name"},
    )

    result = _invoke(runner, "good-name", home, agent="codex")
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["name"] == "good-name"


def test_session_index_applies_without_state_database(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Index names apply to disk-only enumeration (no sqlite DB)."""
    home = tmp_path / "codex"
    session_id = "beef4444-4444-4444-8444-444444444444"
    _write_codex_session(home, session_id, "/z", 1_720_000_000.0)
    _append_index(
        home,
        {"id": session_id, "thread_name": "diskless-name", "updated_at": "z"},
    )

    result = _invoke(runner, "diskless-name", home, agent="codex")
    payload = _json(result)
    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["matched_by"] == "name"
    assert payload["name"] == "diskless-name"
