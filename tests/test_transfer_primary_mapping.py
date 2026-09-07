"""Both native adapters keep operational cwd aligned with the verified project."""

from pathlib import Path

import pytest

from claude_code_tools import transfer_claude, transfer_codex
from tests.test_transfer_claude import SID, fixture_home
from tests.test_transfer_codex import profile, thread


def export_fixture(agent: str, tmp_path: Path, mappings: dict[str, str]) -> dict:
    """Export a native-format isolated fixture through its public adapter."""
    if agent == "claude":
        home, _ = fixture_home(tmp_path)
        adapter = transfer_claude
    else:
        home = tmp_path / "codex"
        profile(home)
        thread(home, SID)
        adapter = transfer_codex
    return adapter.export_session(
        home,
        SID,
        Path("/destination/account"),
        Path("/new/project"),
        tmp_path / "staging",
        path_mappings=mappings,
    )


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    "source", ["/old/project", "/old/project/", "/old/project/../project"]
)
def test_rejects_conflicting_normalized_primary_map(
    tmp_path: Path, agent: str, source: str
) -> None:
    with pytest.raises(ValueError, match="Primary project mapping conflicts"):
        export_fixture(agent, tmp_path, {source: "/other/project"})


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_accepts_matching_primary_and_descendant_maps(
    tmp_path: Path, agent: str
) -> None:
    mappings = {
        "/old/project": "/new/unused/../project",
        "/old/project/subtree": "/separate/subtree",
    }
    result = export_fixture(agent, tmp_path, mappings)
    assert result["ok"]
    exported = result["path_mappings"]
    if isinstance(exported, list):
        exported = {item["source"]: item["destination"] for item in exported}
    assert exported["/old/project/subtree"] == "/separate/subtree"
    assert exported["/old/project"] == "/new/project"
