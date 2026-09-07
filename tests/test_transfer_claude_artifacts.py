"""Regression coverage for session artifacts discovered during real migrations."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from claude_code_tools.transfer_claude import export_session
from tests.test_transfer_claude import SID, fixture_home


def test_goal_and_tool_jsonl_preserved(tmp_path: Path) -> None:
    """Native goal attachments and arbitrary persisted output retain their content."""
    home, transcript = fixture_home(tmp_path)
    goal = {
        "type": "attachment",
        "attachment": {
            "type": "goal_status",
            "met": False,
            "condition": "Read /old/project/report",
            "sentinel": True,
        },
    }
    with transcript.open("a") as stream:
        stream.write(json.dumps(goal) + "\n")
    output = transcript.with_suffix("") / "tool-results" / "output.jsonl"
    output.parent.mkdir(parents=True)
    content = b'{"cwd":"/unrelated/data","value": 1}\nnot JSON at all\n'
    output.write_bytes(content)
    export_session(
        home, SID, Path("/remote/profile"), Path("/new/project"), tmp_path / "bundle"
    )
    root = tmp_path / "bundle/files/projects/-new-project"
    assert json.loads((root / transcript.name).read_text().splitlines()[-1]) == goal
    assert (root / SID / "tool-results/output.jsonl").read_bytes() == content


def test_scratch_symlink_missing_and_mapping(tmp_path: Path, monkeypatch) -> None:
    """Copy session scratch, materialize a log link, and report missing references."""
    home, transcript = fixture_home(tmp_path)
    with tempfile.TemporaryDirectory(prefix="claude-transfer-", dir="/tmp") as name:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: name)
        scratch = (
            Path(name) / f"claude-{os.getuid()}" / "-old-project" / SID / "scratchpad"
        )
        scratch.mkdir(parents=True)
        (scratch / "plan.txt").write_text("scratch goal")
        log = transcript.with_suffix("") / "subagents/agent-child.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps({"cwd": "/old/project", "type": "user"}))
        (scratch / "child.output").symlink_to(log)
        missing = scratch / "gone.txt"
        with transcript.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "message": {
                            "content": f"Read {scratch}/plan.txt then {missing}"
                        },
                        "slug": "missing-plan",
                    }
                )
                + "\n"
            )
        result = export_session(
            home,
            SID,
            Path("/remote/profile"),
            Path("/new/project"),
            tmp_path / "bundle",
        )
        assert str(missing) in {item["path"] for item in result["missing_at_source"]}
        assert str(home / "plans/missing-plan.md") in {
            item["path"] for item in result["missing_at_source"]
        }
        mapping = result["path_mappings"]
        for original in (scratch / "plan.txt", scratch / "child.output"):
            relative = Path(mapping[str(original)]).relative_to("/remote/profile")
            copied = tmp_path / "bundle/files" / relative
            assert copied.is_file() and not copied.is_symlink()
            assert copied.read_bytes() == original.read_bytes()


def test_multiple_explicit_projects(tmp_path: Path) -> None:
    """Explicit mappings support transcript cwd transitions into another worktree."""
    home, transcript = fixture_home(tmp_path)
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {"cwd": "/other/tree", "message": {"content": "Historical /other/tree"}}
            )
            + "\n"
        )
    export_session(
        home,
        SID,
        Path("/remote/profile"),
        Path("/new/project"),
        tmp_path / "bundle",
        path_mappings={"/other/tree": "/new/tree"},
    )
    record = json.loads(
        (tmp_path / "bundle/files/projects/-new-project" / transcript.name)
        .read_text()
        .splitlines()[-1]
    )
    assert record["cwd"] == "/new/tree"
    assert record["message"]["content"] == "Historical /other/tree"


def test_unreferenced_native_scratch(tmp_path: Path, monkeypatch) -> None:
    """Exact native session scratch survives even when used via shell variables."""
    import os

    from claude_code_tools import transfer_claude_artifacts

    home, _ = fixture_home(tmp_path)
    monkeypatch.setattr(
        transfer_claude_artifacts.tempfile, "gettempdir", lambda: str(tmp_path)
    )
    scratch = tmp_path / f"claude-{os.getuid()}" / "-old-project" / SID / "scratchpad"
    scratch.mkdir(parents=True)
    (scratch / "goal.txt").write_text("Native scratch without literal reference")
    result = export_session(
        home,
        SID,
        Path("/remote/profile"),
        Path("/new/project"),
        tmp_path / "bundle",
        path_mappings=[{"source": "/other", "destination": "/remote/other"}],
    )
    mapped = Path(result["path_mappings"][str(scratch / "goal.txt")])
    copied = tmp_path / "bundle/files" / mapped.relative_to("/remote/profile")
    assert copied.read_text() == "Native scratch without literal reference"


def test_prior_transfer_scratch_survives_return(tmp_path: Path) -> None:
    """Previously relocated scratch and original historical aliases survive a return."""
    home, _ = fixture_home(tmp_path)
    support = home / "transfer-support" / SID
    artifact = support / "scratch/hash/goal.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("goal from the first machine")
    (support / "path-map.json").write_text(
        json.dumps(
            {
                "path_mappings": {"/tmp/old-original/goal.txt": str(artifact)},
                "missing_at_source": [
                    {"path": "/tmp/old-original/gone", "reason": "missing_at_source"}
                ],
            }
        )
    )
    result = export_session(
        home, SID, Path("/return/profile"), Path("/return/project"), tmp_path / "bundle"
    )
    target = Path("/return/profile") / artifact.relative_to(home)
    assert result["path_mappings"]["/tmp/old-original/goal.txt"] == str(target)
    assert (tmp_path / "bundle/files" / artifact.relative_to(home)).read_text() == (
        "goal from the first machine"
    )
    assert not any(name.endswith("/path-map.json") for name in result["files"])
    assert any(
        item["path"] == "/tmp/old-original/gone" for item in result["missing_at_source"]
    )


def test_unicode_line_separators_inside_native_jsonl(tmp_path: Path) -> None:
    """JSONL records use LF, not Unicode separators inside conversation strings."""
    home, transcript = fixture_home(tmp_path)
    text = "First\u2028second\u2029third\u0085fourth"
    record = {"type": "user", "cwd": "/old/project", "message": {"content": text}}
    with transcript.open("a") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    subagent = transcript.with_suffix("") / "subagents/agent-child.jsonl"
    subagent.parent.mkdir(parents=True)
    subagent.write_text(json.dumps(record, ensure_ascii=False) + "\n")
    export_session(
        home, SID, Path("/remote/profile"), Path("/new/project"), tmp_path / "bundle"
    )
    root = tmp_path / "bundle/files/projects/-new-project"
    for path in (root / transcript.name, root / SID / "subagents/agent-child.jsonl"):
        records = [json.loads(line) for line in path.read_text().split("\n") if line]
        assert records[-1]["message"]["content"] == text
        assert records[-1]["cwd"] == "/new/project"


def test_scratch_link_cannot_copy_another_sessions_subagent(
    tmp_path: Path, monkeypatch
) -> None:
    """A .jsonl/subagents shape alone does not authorize another session's log."""
    home, transcript = fixture_home(tmp_path)
    unrelated = transcript.parent / "another-session/subagents/agent-private.jsonl"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unrelated conversation must not be copied")
    with tempfile.TemporaryDirectory(prefix="claude-transfer-", dir="/tmp") as name:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: name)
        scratch = (
            Path(name) / f"claude-{os.getuid()}" / "-old-project" / SID / "scratchpad"
        )
        scratch.mkdir(parents=True)
        link = scratch / "task.output"
        link.symlink_to(unrelated)
        with transcript.open("a") as stream:
            stream.write(json.dumps({"message": {"content": f"Read {link}"}}) + "\n")
        result = export_session(
            home,
            SID,
            Path("/remote/profile"),
            Path("/new/project"),
            tmp_path / "bundle",
        )
        assert str(link) not in result["path_mappings"]
        assert {"path": str(link), "reason": "unsupported_symlink_target"} in result[
            "missing_at_source"
        ]
        for path in (tmp_path / "bundle/files").rglob("*"):
            if path.is_file():
                assert (
                    b"unrelated conversation must not be copied"
                    not in path.read_bytes()
                )


def test_regular_other_session_scratch_is_not_copied(
    tmp_path: Path, monkeypatch
) -> None:
    """A literal path into another session's scratch bucket does not authorize it."""
    home, transcript = fixture_home(tmp_path)
    with tempfile.TemporaryDirectory(prefix="claude-transfer-", dir="/tmp") as name:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: name)
        bucket = Path(name) / f"claude-{os.getuid()}" / "-old-project"
        selected = bucket / SID / "scratchpad/selected.txt"
        selected.parent.mkdir(parents=True)
        selected.write_text("owned scratch")
        unrelated = bucket / "another-session" / "scratchpad/private.txt"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("unrelated private scratch")
        linked_directory = selected.parent / "linked"
        linked_directory.symlink_to(unrelated.parent, target_is_directory=True)
        traversed = linked_directory / unrelated.name
        with transcript.open("a") as stream:
            stream.write(
                json.dumps(
                    {"message": {"content": f"Read {unrelated} and {traversed}"}}
                )
                + "\n"
            )
        result = export_session(
            home,
            SID,
            Path("/remote/profile"),
            Path("/new/project"),
            tmp_path / "bundle",
        )
        assert str(selected.resolve()) in result["path_mappings"]
        assert str(unrelated) not in result["path_mappings"]
        assert str(traversed) not in result["path_mappings"]
        for path in (tmp_path / "bundle/files").rglob("*"):
            if path.is_file():
                assert b"unrelated private scratch" not in path.read_bytes()


@pytest.mark.parametrize("linked_component", ["bucket", "project"])
def test_native_scratch_rejects_symlinked_ancestor(
    tmp_path: Path, monkeypatch, linked_component: str
) -> None:
    """A computed native path cannot designate unrelated files through an ancestor."""
    home, _ = fixture_home(tmp_path)
    temporary_base = tmp_path / "temporary"
    temporary_base.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temporary_base))
    bucket = temporary_base / f"claude-{os.getuid()}"
    external = tmp_path / "unrelated"
    if linked_component == "bucket":
        secret = external / "-old-project" / SID / "scratchpad/secret.txt"
        secret.parent.mkdir(parents=True)
        bucket.symlink_to(external, target_is_directory=True)
    else:
        secret = external / SID / "scratchpad/secret.txt"
        secret.parent.mkdir(parents=True)
        bucket.mkdir()
        (bucket / "-old-project").symlink_to(external, target_is_directory=True)
    secret.write_text("unrelated data must not enter selected session export")
    with pytest.raises(ValueError, match="scratch root uses a symlinked ancestor"):
        export_session(
            home,
            SID,
            Path("/remote/profile"),
            Path("/new/project"),
            tmp_path / "bundle",
        )
    for path in (tmp_path / "bundle/files").rglob("*"):
        if path.is_file():
            assert secret.read_bytes() not in path.read_bytes()
