"""Helper transcripts stay out of human-facing name resolution."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
from claude_code_tools.session_utils import mark_session_as_helper
from tests.resolve_session_helpers import _write_claude_session


def _resolve(runner: CliRunner, home: Path, query: str) -> Result:
    """Invoke the Claude resolver against an isolated home."""
    return runner.invoke(
        main,
        ["resolve", query, "--agent", "claude", "--home", str(home), "--json"],
    )


def test_name_resolution_ignores_newer_helper_with_same_name(
    tmp_path: Path,
) -> None:
    """A helper fork cannot make its named source ambiguous."""
    home = tmp_path / "claude"
    cwd = str(tmp_path / "project")
    source_id = "11111111-1111-4111-8111-111111111111"
    helper_id = "22222222-2222-4222-8222-222222222222"
    _write_claude_session(home, source_id, cwd, "Named Session", 10.0)
    helper = _write_claude_session(
        home,
        helper_id,
        cwd,
        "Named Session",
        20.0,
    )
    assert mark_session_as_helper(helper)

    result = _resolve(CliRunner(), home, "Named Session")

    assert result.exit_code == 0
    assert json.loads(result.output)["session_id"] == source_id


def test_helper_remains_resolvable_by_exact_id(tmp_path: Path) -> None:
    """Exact IDs retain a diagnostic escape hatch for helper transcripts."""
    home = tmp_path / "claude"
    cwd = str(tmp_path / "project")
    helper_id = "22222222-2222-4222-8222-222222222222"
    helper = _write_claude_session(
        home,
        helper_id,
        cwd,
        "Named Session",
        20.0,
    )
    assert mark_session_as_helper(helper)

    result = _resolve(CliRunner(), home, helper_id)

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["session_id"] == helper_id
    assert payload["matched_by"] == "id"
    assert payload["name"] is None
