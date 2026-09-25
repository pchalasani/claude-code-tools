"""Verify environment differences remain visible in normal transfer output."""

import json
from pathlib import Path

from click.testing import CliRunner

from claude_code_tools.aichat import main
from tests.test_transfer_session import arguments
from tests.test_transfer_session import workspace as make_workspace


def test_default_report_names_new_skill_denies(tmp_path: Path) -> None:
    """A destination permission deny is displayed without requiring JSON output."""
    workspace = make_workspace.__wrapped__(tmp_path)
    destination = workspace["destination"]
    destination.mkdir()
    (destination / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Skill(fixture-skill)"]}})
    )
    args = [arg for arg in arguments(workspace) if arg not in ("--apply", "--json")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "Environment newly_denied_skills: Skill(fixture-skill)" in result.output
    assert not (destination / "projects").exists()
