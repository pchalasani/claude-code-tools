"""Validate every operational worktree used by transferred session metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_tools.transfer_remote import handle_request
from claude_code_tools.transfer_session import prepare_transfer
from claude_code_tools.transfer_source import export_request
from tests.test_transfer_codex import insert, profile, thread
from tests.test_transfer_session import SID, git


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("exporter", ["local", "ssh_handler"])
@pytest.mark.parametrize("problem", ["dirty", "revision", "missing", "none"])
def test_secondary_operational_worktree_checked(
    tmp_path: Path, agent: str, exporter: str, problem: str
) -> None:
    """Matching primary repos cannot mask a bad subagent/descendant worktree."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    git(source, "init")
    (source / "README").write_text("fixture")
    git(source, "add", "README")
    git(
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.test",
        "commit",
        "-m",
        "fixture",
    )
    git(tmp_path, "clone", str(source), str(target))
    secondary, target_secondary = tmp_path / "secondary", tmp_path / "target-secondary"
    git(source, "worktree", "add", "--detach", str(secondary), "HEAD")
    git(target, "worktree", "add", "--detach", str(target_secondary), "HEAD")
    home, destination = tmp_path / "profile", tmp_path / "destination"
    if agent == "codex":
        profile(home)
        profile(destination)
        for sid, cwd in ((SID, source), ("child", secondary)):
            thread(home, sid, mode="legacy")
            import sqlite3

            with sqlite3.connect(home / "state_5.sqlite") as db:
                db.execute(
                    "UPDATE threads SET cwd=?,sandbox_policy=? WHERE id=?",
                    (str(cwd), '{"type":"read-only"}', sid),
                )
            (home / "sessions/2026" / f"{sid}.jsonl").write_text(
                json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}})
                + "\n"
            )
        insert(
            home,
            "state_5.sqlite",
            "thread_spawn_edges",
            {
                "parent_thread_id": SID,
                "child_thread_id": "child",
                "status": "completed",
            },
        )
    else:
        parent = home / "projects/encoded"
        parent.mkdir(parents=True)
        (parent / f"{SID}.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": SID,
                    "cwd": str(source),
                    "message": {"content": "fixture"},
                }
            )
            + "\n"
        )
        sidecar = parent / SID / "subagents"
        sidecar.mkdir(parents=True)
        (sidecar / "agent-example.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "child",
                    "cwd": str(secondary),
                    "message": {"content": "subagent fixture"},
                }
            )
            + "\n"
        )
    mappings = {str(secondary): str(target_secondary), "/artifact-only": "/unavailable"}
    if exporter == "local":
        manifest = prepare_transfer(
            agent,
            home,
            SID,
            destination,
            target,
            tmp_path / "stage",
            path_mappings=mappings,
        )
    else:
        response = export_request(
            {
                "agent": agent,
                "source_home": str(home),
                "session": SID,
                "destination_home": str(destination),
                "destination_project": str(target),
                "path_mappings": mappings,
            }
        )
        assert response["ran"] and response["ok"]
        manifest = response["manifest"]
    assert {row["source"]["path"] for row in manifest["operational_projects"]} == {
        str(source),
        str(secondary),
    }
    if problem == "dirty":
        (target_secondary / "untracked").write_text("new local work")
    elif problem == "revision":
        (target_secondary / "README").write_text("different revision")
        git(target_secondary, "add", "README")
        git(
            target_secondary,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.test",
            "commit",
            "-m",
            "different",
        )
    elif problem == "missing":
        git(target, "worktree", "remove", str(target_secondary))
    request = {
        "operation": "validate",
        "destination_home": str(destination),
        "destination_project": str(target),
        "manifest": manifest,
    }
    if problem == "none":
        assert handle_request(request)["ok"]
    else:
        with pytest.raises(
            ValueError, match="Operational worktree|Project does not exist"
        ):
            handle_request(request)
    assert not (destination / ".aichat-transfer.lock").exists()
