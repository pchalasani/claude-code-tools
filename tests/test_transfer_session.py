"""Exercise the public transfer CLI and destination protocol on real worktrees."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.transfer_remote import handle_request, project_state
from claude_code_tools.transfer_session import remote_bootstrap

SID = "f247a9f0-6196-4c19-a77a-e99c14474e8c"


def git(directory: Path, *args: str) -> str:
    """Run isolated fixture Git commands without user hooks or signing."""
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    """Create matching clean worktrees and one saved Claude conversation."""
    source = tmp_path / "source project"
    target = tmp_path / "target project"
    source.mkdir()
    git(source, "init")
    (source / "README.md").write_text("fixture\n")
    git(source, "add", "README.md")
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
    home = tmp_path / "source profile"
    transcript = home / "projects" / "encoded" / f"{SID}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": SID,
                "cwd": str(source),
                "message": {"role": "user", "content": "Remember the fixture"},
            }
        )
        + "\n"
    )
    return {
        "source": source,
        "target": target,
        "home": home,
        "destination": tmp_path / "destination profile",
        "transcript": transcript,
    }


def arguments(workspace: dict[str, Path]) -> list[str]:
    """Return the public command for a local fixture transfer."""
    return [
        "transfer",
        "--apply",
        SID,
        "--agent",
        "claude",
        "--source-home",
        str(workspace["home"]),
        "--to",
        "local",
        "--destination-home",
        str(workspace["destination"]),
        "--destination-project",
        str(workspace["target"]),
        "--json",
    ]


def test_dry_run_then_copy_and_repeat(workspace: dict[str, Path]) -> None:
    """Dry-run is read-only; copying is verified and does not remove source."""
    runner = CliRunner()
    before = workspace["transcript"].read_bytes()
    result = runner.invoke(
        main, [arg for arg in arguments(workspace) if arg != "--apply"]
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["plan"]
    assert not workspace["destination"].exists()
    assert plan["artifacts"]
    assert "CLAUDE_CONFIG_DIR=" in plan["resume_command"]
    result = runner.invoke(main, arguments(workspace))
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["ok"] is True
    assert report["destination"]["verified_files"] == 2
    copied = next(workspace["destination"].glob("projects/*/*.jsonl"))
    assert json.loads(copied.read_text())["cwd"] == str(workspace["target"])
    assert workspace["transcript"].read_bytes() == before
    assert copied.stat().st_mode & 0o777 == 0o600
    repeated = runner.invoke(main, arguments(workspace))
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["destination"]["imported_files"] == 0


def test_dirty_or_wrong_revision_refused(workspace: dict[str, Path]) -> None:
    """A copied conversation cannot silently resume against different code."""
    (workspace["target"] / "untracked.txt").write_text("unsynced")
    result = CliRunner().invoke(main, arguments(workspace))
    assert result.exit_code == 1
    assert "clean" in json.loads(result.output)["error"]
    assert not workspace["destination"].exists()
    (workspace["target"] / "untracked.txt").unlink()
    git(
        workspace["target"],
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "new",
    )
    result = CliRunner().invoke(main, arguments(workspace))
    assert result.exit_code == 1
    assert "revision" in json.loads(result.output)["error"]


def test_destination_conflict_preserved(workspace: dict[str, Path]) -> None:
    """Existing history is never overwritten by another source copy."""
    runner = CliRunner()
    assert runner.invoke(main, arguments(workspace)).exit_code == 0
    copied = next(workspace["destination"].glob("projects/*/*.jsonl"))
    copied.write_text("existing conversation")
    result = runner.invoke(main, arguments(workspace))
    assert result.exit_code == 1
    assert copied.read_text() == "existing conversation"


def test_remote_bootstrap_without_installed_package(
    workspace: dict[str, Path], tmp_path: Path
) -> None:
    """The SSH helper runs in a clean Python process with only stdlib available."""
    request = {
        "operation": "probe",
        "destination_home": str(workspace["destination"]),
        "destination_project": str(workspace["target"]),
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", remote_bootstrap()],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert not workspace["destination"].exists()


def request_with_files(workspace: dict[str, Path]) -> dict[str, Any]:
    """Build a destination import with independent integrity metadata."""
    data = b"artifact"
    metadata = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {
        "operation": "import",
        "destination_home": str(workspace["destination"]),
        "destination_project": str(workspace["target"]),
        "manifest": {
            "agent": "claude",
            "project_state": project_state(workspace["source"]),
            "artifacts": {"projects/a/one": metadata, "projects/a/two": metadata},
        },
        "data": {
            "projects/a/one": base64.b64encode(data).decode(),
            "projects/a/two": base64.b64encode(b"corrupt").decode(),
        },
    }


def test_corrupt_bundle_rolls_back_created_files(workspace: dict[str, Path]) -> None:
    """A late integrity failure removes files created earlier in the import."""
    with pytest.raises(ValueError, match="integrity"):
        handle_request(request_with_files(workspace))
    assert not list(workspace["destination"].rglob("one"))
    assert not (workspace["destination"] / ".aichat-transfer.lock").exists()


def test_symlink_and_traversal_refused(workspace: dict[str, Path]) -> None:
    """Artifact paths stay under the explicit account home."""
    request = request_with_files(workspace)
    home = workspace["destination"]
    home.mkdir()
    (home / "projects").symlink_to(workspace["target"], target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        handle_request(request)
    (home / "projects").unlink()
    request["manifest"]["artifacts"] = {"../escape": {}}
    with pytest.raises(ValueError, match="Unsafe"):
        handle_request(request)


def test_command_is_registered() -> None:
    """The shipped entry point exposes transfer rather than falling into menu."""
    result = CliRunner().invoke(main, ["transfer", "--help"])
    assert result.exit_code == 0
    assert "--destination-project" in result.output


def test_archived_codex_session_resolves(
    workspace: dict[str, Path], tmp_path: Path
) -> None:
    """Archived SQLite-indexed threads remain eligible for public transfer."""
    import sqlite3

    from tests.test_transfer_codex import profile, thread

    home = tmp_path / "codex"
    destination = tmp_path / "codex-destination"
    profile(home)
    profile(destination)
    thread(home, SID)
    original = home / "sessions" / "2026" / f"{SID}.jsonl"
    archived = home / "archived_sessions" / original.name
    archived.parent.mkdir()
    original.rename(archived)
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute(
            "UPDATE threads SET cwd=?,rollout_path=?,sandbox_policy=?,archived=1",
            (
                str(workspace["source"]),
                str(archived),
                json.dumps({"type": "workspace-write", "writable_roots": []}),
            ),
        )
    args = [
        "transfer",
        SID,
        "--agent",
        "codex",
        "--map",
        "/old/project",
        str(workspace["target"]),
        "--source-home",
        str(home),
        "--to",
        "local",
        "--destination-home",
        str(destination),
        "--destination-project",
        str(workspace["target"]),
        "--dry-run",
        "--json",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["plan"]["files"] == [
        f"archived_sessions/{SID}.jsonl",
        f"transfer-support/{SID}/path-map.json",
    ]


def test_interrupt_after_real_database_commit_preserves_rollouts(
    workspace: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late interrupt cannot delete rollouts referenced by committed rows."""
    import sqlite3

    from claude_code_tools.transfer_session import prepare_transfer
    from tests.test_transfer_codex import profile, thread

    home = tmp_path / "codex"
    destination = tmp_path / "codex-destination"
    profile(home)
    profile(destination)
    thread(home, SID)
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute(
            "UPDATE threads SET cwd=?,sandbox_policy=?",
            (
                str(workspace["source"]),
                json.dumps({"type": "workspace-write", "writable_roots": []}),
            ),
        )
    staging = tmp_path / "staging"
    manifest = prepare_transfer(
        "codex",
        home,
        SID,
        destination,
        workspace["target"],
        staging,
        path_mappings={"/old/project": str(workspace["target"])},
    )
    request = {
        "operation": "import",
        "destination_home": str(destination),
        "destination_project": str(workspace["target"]),
        "manifest": manifest,
        "data": {
            relative: base64.b64encode(
                (staging / "files" / relative).read_bytes()
            ).decode()
            for relative in manifest["files"]
        },
    }
    original_connect = sqlite3.connect

    class InterruptedCommit(sqlite3.Connection):
        """Commit real SQLite data, then simulate deferred signal delivery."""

        def commit(self) -> None:
            super().commit()
            raise KeyboardInterrupt

    def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if database == ":memory:":
            kwargs["factory"] = InterruptedCommit
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(ValueError, match="commit outcome may be uncertain"):
        handle_request(request)
    with original_connect(destination / "state_5.sqlite") as db:
        assert db.execute("SELECT count(*) FROM threads WHERE id=?", (SID,)).fetchone()[
            0
        ]
    for relative in manifest["files"]:
        assert (destination / relative).read_bytes() == (
            staging / "files" / relative
        ).read_bytes()
    assert (destination / ".aichat-transfer.lock").is_dir()


def test_apply_and_dry_run_are_mutually_exclusive(workspace: dict[str, Path]) -> None:
    """Ambiguous execution intent never writes destination data."""
    result = CliRunner().invoke(main, arguments(workspace) + ["--dry-run"])
    assert result.exit_code == 2
    assert "either --apply or --dry-run" in result.output
    assert not workspace["destination"].exists()


def test_remote_source_bootstrap_exports_without_installed_package(
    workspace: dict[str, Path],
) -> None:
    """The shipped stdlib helper can read a remote source and return its bundle."""
    request = {
        "operation": "export",
        "agent": "claude",
        "session": SID,
        "source_home": str(workspace["home"]),
        "destination_home": str(workspace["destination"]),
        "destination_project": str(workspace["target"]),
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", remote_bootstrap()],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["ran"] is True and report["ok"] is True
    assert report["manifest"]["session_id"] == SID
    assert report["data"]
    assert not workspace["destination"].exists()


def test_public_report_omits_private_record_contents() -> None:
    """CLI inspection must not print message/goal/token material from payload rows."""
    from claude_code_tools.transfer_session import public_report

    report = {
        "ok": True,
        "plan": {
            "databases": [
                {
                    "tables": [
                        {
                            "name": "goals",
                            "rows": [
                                {"objective": "PRIVATE_CONVERSATION_TOKEN"},
                            ],
                        }
                    ]
                }
            ],
            "metadata_updates": [{"rows": [{"text": "PRIVATE_CONVERSATION_TOKEN"}]}],
        },
    }
    rendered = public_report(report)
    assert "PRIVATE_CONVERSATION_TOKEN" not in json.dumps(rendered)
    assert rendered["plan"]["databases"][0]["tables"][0]["row_count"] == 1
    assert rendered["plan"]["metadata_updates"][0]["row_count"] == 1
    assert report["plan"]["databases"][0]["tables"][0]["rows"]


def test_default_report_displays_missing_source_artifacts(
    workspace: dict[str, Path],
) -> None:
    """A normal dry run exposes source gaps without requiring JSON output."""
    transcript = workspace["transcript"]
    record = json.loads(transcript.read_text())
    record["slug"] = "missing-fixture-plan"
    transcript.write_text(json.dumps(record) + "\n")
    args = [arg for arg in arguments(workspace) if arg not in ("--apply", "--json")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "Transfer plan verified." in result.output
    missing = workspace["home"] / "plans/missing-fixture-plan.md"
    assert f"Missing at source (not copied): {missing}" in result.output
    assert not workspace["destination"].exists()


@pytest.mark.parametrize("apply", [False, True])
def test_codex_missing_artifact_in_normal_report(
    workspace: dict[str, Path], tmp_path: Path, apply: bool
) -> None:
    """Codex string-shaped source gaps render on both inspection and copy."""
    import sqlite3

    from tests.test_transfer_codex import profile, thread

    home = tmp_path / "codex"
    destination = tmp_path / "codex-destination"
    profile(home)
    profile(destination)
    thread(home, SID)
    missing = home / "attachments/missing.txt"
    rollout = home / "sessions/2026" / f"{SID}.jsonl"
    with rollout.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "content": [{"type": "input_text", "text": str(missing)}],
                    },
                }
            )
            + "\n"
        )
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET cwd=?", (str(workspace["source"]),))
    args = [
        "transfer",
        SID,
        "--agent",
        "codex",
        "--source-home",
        str(home),
        "--to",
        "local",
        "--destination-home",
        str(destination),
        "--destination-project",
        str(workspace["target"]),
        "--map",
        "/old/project",
        str(workspace["target"]),
    ]
    if apply:
        args.append("--apply")
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert f"Missing at source (not copied): {missing}" in result.output
