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
        {
            "type": "user",
            "cwd": "/old/project",
            "sessionId": SID,
            "message": {"content": "Read /old/project/code.py"},
        },
        {
            "type": "file-history-snapshot",
            "snapshot": {
                "trackedFileBackups": {"/old/project/code.py": {"version": 1}}
            },
        },
    ]
    transcript = project / f"{SID}.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return home, transcript


def export(home: Path, tmp_path: Path) -> dict:
    """Stage to a destination that need not exist on this machine."""
    return export_session(
        home,
        SID,
        Path("/remote/.claude-work"),
        Path("/new/project"),
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
    staged = (
        tmp_path
        / "bundle/files/projects/-new-project"
        / SID
        / ("subagents/agent-123.jsonl")
    )
    assert json.loads(staged.read_text())["cwd"] == "/new/project/sub"
    assert any("Session-linked tasks" in warning for warning in result["warnings"])


def test_includes_referenced_plan_only(tmp_path: Path) -> None:
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as handle:
        handle.write(json.dumps({"slug": "dancing-quiet-fox"}) + "\n")
    plans = home / "plans"
    plans.mkdir()
    (plans / "dancing-quiet-fox.md").write_text("Continue research")
    (plans / "unrelated.md").write_text("Other session")
    result = export(home, tmp_path)
    assert "plans/dancing-quiet-fox.md" in result["files"]
    assert "plans/unrelated.md" not in result["files"]


def test_detects_changed_and_added_sources(tmp_path: Path) -> None:
    """Use a real racing writer; export must refuse the unstable snapshot."""
    import threading
    import time

    home, transcript = fixture_home(tmp_path)
    results = transcript.with_suffix("") / "tool-results"
    results.mkdir(parents=True)
    # Large enough to hold the copy open while the companion writer runs.
    (results / "large.txt").write_bytes(b"x" * (32 * 1024 * 1024))
    stop = threading.Event()
    started = threading.Event()

    def mutate() -> None:
        index = 0
        while not stop.is_set():
            (results / "changing.txt").write_text(str(index))
            started.set()
            index += 1
            time.sleep(0.0001)

    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(ValueError, match="Source changed"):
            export(home, tmp_path)
    finally:
        stop.set()
        thread.join(5)
        assert not thread.is_alive()


def test_inventory_detects_new_file(tmp_path: Path) -> None:
    from claude_code_tools.transfer_claude import _fingerprint

    root = tmp_path / "sidecar"
    root.mkdir()
    before = _fingerprint([root])
    (root / "new.txt").write_text("new output")
    assert _fingerprint([root]) != before


@pytest.mark.parametrize(
    "cwd", ["/other/repo", "/old/project-extra", "/old/project/../other", "relative"]
)
def test_rejects_unmappable_sidecar_cwd(tmp_path: Path, cwd: str) -> None:
    """External subagent projects cannot silently retain source-machine cwd."""
    home, transcript = fixture_home(tmp_path)
    sidecar = transcript.with_suffix("") / "subagents" / "agent-child.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n")
    with pytest.raises(ValueError, match="cwd needs an explicit mapping"):
        export(home, tmp_path)


def test_maps_sidecar_project_subdirectory(tmp_path: Path) -> None:
    """A subagent inside the selected project retains its relative directory."""
    home, transcript = fixture_home(tmp_path)
    sidecar = transcript.with_suffix("") / "subagents" / "agent-child.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps(
            {
                "type": "user",
                "cwd": "/old/project/subdir",
                "message": {"content": "Historical /old/project/subdir"},
            }
        )
        + "\n"
    )
    export(home, tmp_path)
    copied = (
        tmp_path
        / "bundle/files/projects/-new-project"
        / SID
        / "subagents"
        / sidecar.name
    )
    record = json.loads(copied.read_text())
    assert record["cwd"] == "/new/project/subdir"
    assert record["message"]["content"] == "Historical /old/project/subdir"


@pytest.mark.parametrize(
    "key", ["/old/project/../outside.py", "/other/repo/file.py", "../outside.py"]
)
def test_rejects_unmappable_file_history(tmp_path: Path, key: str) -> None:
    """File-history keys cannot escape the selected project after normalization."""
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "file-history-snapshot",
                    "snapshot": {"trackedFileBackups": {key: {"version": 1}}},
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="file-history path needs an explicit mapping"):
        export(home, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("realParentDir", "/old/project/../outside"),
        ("realParentDir", "/other/repo"),
        ("backupFileName", "../outside"),
        ("backupFileName", "/other/backup"),
    ],
)
def test_rejects_unmappable_backup_metadata(
    tmp_path: Path, field: str, value: str
) -> None:
    """The paths inside backup values receive the same containment checks."""
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "file-history-snapshot",
                    "snapshot": {"trackedFileBackups": {"file.py": {field: value}}},
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="(realParentDir|backupFileName)"):
        export(home, tmp_path)


def test_preserves_relative_backup_keys_and_maps_real_parent(tmp_path: Path) -> None:
    """Native relative keys stay relative and real parent directories relocate."""
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "file-history-snapshot",
                    "snapshot": {
                        "trackedFileBackups": {
                            "src/../file.py": {
                                "realParentDir": "/old/project/src/..",
                                "backupFileName": "backup@v1",
                                "version": 1,
                            },
                        }
                    },
                }
            )
            + "\n"
        )
    export(home, tmp_path)
    copied = tmp_path / "bundle/files/projects/-new-project" / transcript.name
    record = json.loads(copied.read_text().splitlines()[-1])
    assert record["snapshot"]["trackedFileBackups"] == {
        "file.py": {
            "realParentDir": "/new/project",
            "backupFileName": "backup@v1",
            "version": 1,
        },
    }


def test_rejects_normalized_backup_key_collision(tmp_path: Path) -> None:
    """Normalization cannot silently discard one of two backup records."""
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "file-history-snapshot",
                    "snapshot": {
                        "trackedFileBackups": {
                            "src/../file.py": {"version": 1},
                            "file.py": {"version": 2},
                        }
                    },
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="file-history collision"):
        export(home, tmp_path)
