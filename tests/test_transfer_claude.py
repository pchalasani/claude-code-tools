"""Real filesystem coverage for Claude transfer staging."""

import json
from pathlib import Path

import pytest

from claude_code_tools.transfer_claude import export_session

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def fixture_home(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal account with an actual transcript and companions."""
    home = tmp_path / "claude"
    project = home / "projects" / "-old-project"
    project.mkdir(parents=True)
    records = [
        {"type": "custom-title", "customTitle": "Research", "sessionId": SID},
        {"type": "user", "cwd": "/old/project", "sessionId": SID,
         "message": {"content": "Read /old/project/code.py"}},
        {"type": "file-history-snapshot", "snapshot": {
            "trackedFileBackups": {"/old/project/code.py": {"version": 1}}}},
    ]
    transcript = project / f"{SID}.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return home, transcript


def export(home: Path, tmp_path: Path) -> dict:
    """Stage to a destination that need not exist on this machine."""
    return export_session(
        home, SID, Path("/remote/.claude-work"), Path("/new/project"),
        tmp_path / "bundle",
    )


def test_complete_payload_and_metadata(tmp_path: Path) -> None:
    """Transfer companions and memory but retain prose and the original home."""
    home, transcript = fixture_home(tmp_path)
    original = transcript.read_bytes()
    artifacts = {
        f"projects/-old-project/{SID}/tool-results/result.txt": "tool text",
        f"file-history/{SID}/backup@v1": "old contents",
        f"tasks/{SID}/1.json": '{"subject":"continue"}',
        f"tasks/{SID}/.lock": "lock",
        f"todos/{SID}-agent-{SID}.json": "[]",
        "projects/-old-project/memory/MEMORY.md": "/old/project memory",
        "settings.json": "secret configuration",
    }
    for name, content in artifacts.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    result = export(home, tmp_path)
    assert result["ok"] is True
    assert len(result["files"]) == 6
    assert result["shared_files"] == ["projects/-new-project/memory/MEMORY.md"]
    staged = tmp_path / "bundle/files/projects/-new-project" / transcript.name
    records = [json.loads(line) for line in staged.read_text().splitlines()]
    assert records[1]["cwd"] == "/new/project"
    assert records[1]["message"]["content"] == "Read /old/project/code.py"
    assert "/new/project/code.py" in records[2]["snapshot"]["trackedFileBackups"]
    assert transcript.read_bytes() == original
    assert not any(".lock" in name or "settings" in name for name in result["files"])


def test_rejects_ambiguous_cwd(tmp_path: Path) -> None:
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as handle:
        handle.write(json.dumps({"cwd": "/unrelated"}) + "\n")
    with pytest.raises(ValueError, match="exactly one project"):
        export(home, tmp_path)


@pytest.mark.parametrize("artifact", ["memory", f"{SID}/tool-results"])
def test_rejects_symlink_companions(tmp_path: Path, artifact: str) -> None:
    home, transcript = fixture_home(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    link = transcript.parent / artifact
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        export(home, tmp_path)


def test_rejects_unknown_sidecar(tmp_path: Path) -> None:
    home, transcript = fixture_home(tmp_path)
    artifact = transcript.with_suffix("") / "unknown-runtime"
    artifact.parent.mkdir()
    artifact.write_text("unknown")
    with pytest.raises(ValueError, match="Unsupported Claude session sidecar"):
        export(home, tmp_path)


def test_rejects_partial_json(tmp_path: Path) -> None:
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as handle:
        handle.write('{"partial":')
    with pytest.raises(ValueError, match="Invalid JSON"):
        export(home, tmp_path)


def test_copies_subagent_metadata_and_reports_runtime(tmp_path: Path) -> None:
    home, transcript = fixture_home(tmp_path)
    subagent = transcript.with_suffix("") / "subagents/agent-123.jsonl"
    subagent.parent.mkdir(parents=True)
    subagent.write_text(json.dumps({"cwd": "/old/project/sub", "type": "user"}))
    (home / "tasks" / f"session-{SID[:8]}").mkdir(parents=True)
    result = export(home, tmp_path)
    staged = tmp_path / "bundle/files/projects/-new-project" / SID / (
        "subagents/agent-123.jsonl"
    )
    assert json.loads(staged.read_text())["cwd"] == "/new/project/sub"
    assert any("Session-linked tasks" in warning for warning in result["warnings"])
