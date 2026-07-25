"""Regressions for session resolution used by direct ``aichat`` actions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.session_utils import encode_claude_project_path
from tests.resolve_session_helpers import (
    FakeHome,
    _write_claude_session,
    _write_codex_session,
    claude_home,
    codex_home,
    runner,
)

__all__ = ["claude_home", "codex_home", "runner"]


def _root_args(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> list[str]:
    """Return root options that isolate both agent homes."""
    return [
        "--claude-home",
        str(claude_home.path),
        "--codex-home",
        str(codex_home.path),
    ]


def _share_claude_title_with_codex(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> str:
    """Give a Codex thread the third Claude session's exact title."""
    title = "Unique Deployment Review"
    with sqlite3.connect(codex_home.path / "state_5.sqlite") as connection:
        connection.execute(
            "UPDATE threads SET title = ? WHERE id = ?",
            (title, codex_home.ids[2]),
        )
        connection.commit()
    assert claude_home.files[2].is_file()
    return title


def _write_session_without_cwd(tmp_path: Path) -> Path:
    """Create an explicit Claude transcript that has no cwd metadata."""
    session_id = "eeee4444-4444-4444-8444-444444444444"
    session_file = tmp_path / "explicit-sessions" / f"{session_id}.jsonl"
    session_file.parent.mkdir()
    session_file.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "message": {"content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return session_file


def test_copy_agent_constrains_cross_agent_name_collision(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """``copy --agent`` selects that home when an exact name is shared."""
    query = _share_claude_title_with_codex(claude_home, codex_home)
    destination = tmp_path / "copied-session.jsonl"

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "copy",
            query,
            "--agent",
            "codex",
            "--dest",
            str(destination),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert destination.read_bytes() == codex_home.files[2].read_bytes()


def test_export_agent_constrains_cross_agent_name_collision(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """``export --agent`` resolves a shared name within the forced home."""
    query = _share_claude_title_with_codex(claude_home, codex_home)
    destination = tmp_path / "exported-session.txt"
    marker = "selected constrained Codex transcript"
    with codex_home.files[2].open("a", encoding="utf-8") as session_stream:
        session_stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": marker}
                        ],
                    },
                }
            )
            + "\n"
        )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "export",
            "--agent",
            "codex",
            query,
            "--output",
            str(destination),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert destination.is_file()
    assert marker in destination.read_text(encoding="utf-8")
    assert "Detected CODEX session" in result.output
    assert "Exporting with CODEX" in result.output
    assert "user specified" not in result.output


def test_export_agent_after_session_is_consumed_as_constraint(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A post-SESSION ``--agent=`` is consumed instead of forwarded."""
    query = _share_claude_title_with_codex(claude_home, codex_home)
    destination = tmp_path / "post-session-export.txt"
    marker = "post-session agent selected Codex"
    with codex_home.files[2].open("a", encoding="utf-8") as session_stream:
        session_stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": marker}
                        ],
                    },
                }
            )
            + "\n"
        )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "export",
            query,
            "--agent=codex",
            "--output",
            str(destination),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert marker in destination.read_text(encoding="utf-8")
    assert "Detected CODEX session" in result.output


def test_smart_trim_dry_run_without_session_has_cli_error(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """The option-only smart-trim path does not raise ``NameError``."""
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "smart-trim",
            "--dry-run",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Direct smart-trim options require a session ID" in result.output
    assert "NameError" not in result.output


def test_smart_trim_session_dry_run_reaches_delegate(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session dry-run path reaches the underlying CLI without NameError."""
    import sys

    called = False

    def capture_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(sys, "argv", sys.argv.copy())
    monkeypatch.setattr("claude_code_tools.smart_trim.main", capture_main)
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "smart-trim",
            claude_home.ids[2],
            "--dry-run",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert called
    assert "NameError" not in result.output


def test_direct_trim_agent_rejects_other_agent_session(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct trim applies ``--agent`` before invoking its delegate."""
    called = False

    def capture_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "claude_code_tools.trim_session.main",
        capture_main,
    )
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "trim",
            claude_home.ids[2],
            "--simple-ui",
            "--agent",
            "codex",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Session not found" in result.output
    assert not called


def test_direct_trim_passes_resolved_path_to_delegate(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct trim delegates only after constrained shared resolution."""
    import sys

    captured: list[str] = []

    def capture_main() -> None:
        captured.extend(sys.argv)

    monkeypatch.setattr(sys, "argv", sys.argv.copy())
    monkeypatch.setattr(
        "claude_code_tools.trim_session.main",
        capture_main,
    )
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "trim",
            claude_home.ids[2],
            "--simple-ui",
            "--agent",
            "claude",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert str(claude_home.files[2].resolve()) in captured
    assert captured[captured.index("--agent") + 1] == "claude"


@pytest.mark.parametrize("claude_home_value", ["", "   "])
def test_direct_trim_rejects_empty_command_local_claude_home(
    claude_home_value: str,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty home cannot fall back through the filename fast path."""
    called = False

    def capture_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "claude_code_tools.trim_session.main",
        capture_main,
    )
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "trim",
            claude_home.ids[2],
            "--simple-ui",
            "--agent",
            "claude",
            "--claude-home",
            claude_home_value,
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Claude home must not be empty" in result.output
    assert not called


@pytest.mark.parametrize("command", ["trim", "smart-trim", "resume"])
def test_interactive_session_action_resolves_name_before_ui(
    command: str,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive actions pass a name's resolved absolute path to the UI."""
    import sys

    captured: dict[str, Any] = {}

    def capture_ui(
        sessions: list[dict[str, Any]],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        captured.update(sessions[0])

    monkeypatch.setattr(
        "claude_code_tools.session_menu_cli.run_node_menu_ui",
        capture_ui,
    )
    monkeypatch.setattr(sys, "argv", sys.argv.copy())
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            "Unique Deployment Review",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["file_path"] == str(claude_home.files[2].resolve())
    assert captured["agent"] == "claude"
    assert captured["claude_home"] == str(claude_home.path)


@pytest.mark.parametrize("command", ["resume", "trim", "smart-trim"])
def test_interactive_action_uses_command_local_claude_home(
    command: str,
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command-local home resolves before a same-named default session."""
    import sys

    custom_home = tmp_path / "custom-claude"
    custom_id = "eeee4444-4444-4444-8444-444444444444"
    custom_file = _write_claude_session(
        custom_home,
        custom_id,
        str(tmp_path / "custom-worktree"),
        "Unique Deployment Review",
        1_730_000_000.0,
    )
    captured: dict[str, Any] = {}

    def capture_ui(
        sessions: list[dict[str, Any]],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        captured.update(sessions[0])

    monkeypatch.setattr(
        "claude_code_tools.session_menu_cli.run_node_menu_ui",
        capture_ui,
    )
    monkeypatch.setattr(sys, "argv", sys.argv.copy())
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            "--agent",
            "claude",
            "--claude-home",
            str(custom_home),
            "Unique Deployment Review",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["file_path"] == str(custom_file.resolve())
    assert captured["file_path"] != str(claude_home.files[2].resolve())
    assert captured["claude_home"] == str(custom_home)


@pytest.mark.parametrize("command", ["trim", "smart-trim", "resume"])
def test_interactive_session_action_rejects_ambiguous_query_before_ui(
    command: str,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive actions report all candidates without launching the UI."""
    menu_called = False

    def capture_menu() -> None:
        nonlocal menu_called
        menu_called = True

    monkeypatch.setattr(
        "claude_code_tools.session_menu_cli.main",
        capture_menu,
    )
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            "aaaa",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "ambiguous" in result.output.lower()
    assert claude_home.ids[0] in result.output
    assert claude_home.ids[1] in result.output
    assert not menu_called


@pytest.mark.parametrize("command", ["info", "lineage"])
def test_converted_read_only_command_runs(
    command: str,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Converted read-only commands retain every runtime import."""
    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            str(claude_home.files[2]),
            "--agent",
            "claude",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "NameError" not in result.output


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_info_rejects_malformed_explicit_path_inside_agent_home(
    agent: str,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """An agent home's location cannot make malformed JSONL resumable."""
    home = claude_home.path if agent == "claude" else codex_home.path
    malformed = home / "sessions" / "bogus.jsonl"
    malformed.parent.mkdir(exist_ok=True)
    malformed.write_text('{"cwd":"/wrong"}\n', encoding="utf-8")

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "info",
            str(malformed),
            "--agent",
            agent,
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "Could not detect agent for session file" in result.output


def test_info_accepts_valid_codex_rollout_as_explicit_path(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A valid Codex rollout remains usable through direct-path lookup."""
    session_id = "eeee4444-4444-4444-8444-444444444444"
    rollout = _write_codex_session(
        codex_home.path,
        session_id,
        str(tmp_path / "valid-codex-worktree"),
        1_720_000_000.0,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "info",
            str(rollout),
            "--agent",
            "codex",
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert f'"session_id": "{session_id}"' in result.output


def test_info_counts_lines_in_transcript_with_undecodable_bytes(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Info reports a valid session despite an undecodable earlier line."""
    session_id = "eeee4444-4444-4444-8444-444444444444"
    session_file = tmp_path / f"{session_id}.jsonl"
    record = json.dumps(
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": str(tmp_path),
            "message": {"role": "user", "content": "hello"},
        }
    ).encode()
    session_file.write_bytes(b"\xff\n" + record + b"\n")

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "info",
            str(session_file),
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert '"line_count": 2' in result.output


def test_export_agent_help_describes_search_constraint(
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """``export --agent`` help describes lookup scope, not an override."""
    result = runner.invoke(
        main,
        [*_root_args(claude_home, codex_home), "export", "--help"],
    )

    assert result.exit_code == 0, result.output
    assert "Restrict the search to this agent's home" in result.output
    assert "force export" not in result.output.lower()


def test_rollover_direct_action_falls_back_to_session_parent(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared direct-action UI gets a stable cwd without metadata."""
    session_file = _write_session_without_cwd(tmp_path)
    captured: dict[str, Any] = {}

    def capture_ui(
        sessions: list[dict[str, Any]],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        captured.update(sessions[0])

    monkeypatch.setattr(
        "claude_code_tools.node_menu_ui.run_node_menu_ui",
        capture_ui,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "rollover",
            str(session_file),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["cwd"] == str(session_file.parent)


def test_smart_trim_falls_back_to_session_parent(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smart trim passes the explicit file's parent when cwd is absent."""
    session_file = _write_session_without_cwd(tmp_path)
    captured: dict[str, str] = {}

    def capture_trim(
        session_id: str,
        project_path: str,
        claude_home: str | None = None,
        custom_instructions: str | None = None,
    ) -> None:
        captured["session_id"] = session_id
        captured["project_path"] = project_path

    monkeypatch.setattr(
        "claude_code_tools.find_claude_session."
        "handle_smart_trim_resume_claude",
        capture_trim,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "smart-trim",
            str(session_file),
            "--instructions",
            "keep the conclusion",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["project_path"] == str(session_file.parent)


def test_query_falls_back_to_session_parent(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive query receives the explicit file's parent as cwd."""
    session_file = _write_session_without_cwd(tmp_path)
    captured: dict[str, Any] = {}

    def capture_ui(
        sessions: list[dict[str, Any]],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        captured.update(sessions[0])

    monkeypatch.setattr(
        "claude_code_tools.node_menu_ui.run_node_menu_ui",
        capture_ui,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "query",
            str(session_file),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["cwd"] == str(session_file.parent)


def test_clone_falls_back_to_session_parent(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clone receives the explicit file's parent when cwd is absent."""
    session_file = _write_session_without_cwd(tmp_path)
    captured: dict[str, str] = {}

    def capture_clone(
        session_id: str,
        project_path: str,
        shell_mode: bool = False,
        claude_home: str | None = None,
        source_path: Path | None = None,
    ) -> None:
        captured["session_id"] = session_id
        captured["project_path"] = project_path
        captured["source_path"] = str(source_path)

    monkeypatch.setattr(
        "claude_code_tools.find_claude_session.clone_session",
        capture_clone,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "clone",
            str(session_file),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["project_path"] == str(session_file.parent)
    assert captured["source_path"] == str(session_file)


def test_clone_uses_exact_resolved_claude_transcript(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude clone copies the named path when metadata collides."""
    import uuid

    session_id = "11111111-1111-4111-8111-111111111111"
    cwd = str(tmp_path / "shared-cwd")
    encoded_cwd = encode_claude_project_path(cwd)
    in_home = (
        claude_home.path
        / "projects"
        / encoded_cwd
        / f"{session_id}.jsonl"
    )
    in_home.parent.mkdir()
    in_home.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"role": "user", "content": "HOME"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    external = tmp_path / f"{session_id}.jsonl"
    external.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"role": "user", "content": "OUTSIDE"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    clone_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    monkeypatch.setattr(uuid, "uuid4", lambda: clone_id)
    monkeypatch.setattr(
        "claude_code_tools.find_claude_session.resume_session",
        lambda *_args, **_kwargs: None,
    )

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "clone",
            str(external),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    clone = (
        claude_home.path
        / "projects"
        / encoded_cwd
        / f"{clone_id}.jsonl"
    )
    assert clone.is_file()
    assert "OUTSIDE" in clone.read_text(encoding="utf-8")
    assert "HOME" not in clone.read_text(encoding="utf-8")


def test_clone_reports_destination_creation_failure(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude clone reports an unwritable home without a traceback."""
    from claude_code_tools.find_claude_session import get_session_file_path

    source = _write_session_without_cwd(tmp_path)
    destination_dir = Path(
        get_session_file_path(
            source.stem,
            str(source.parent),
            str(claude_home.path),
        )
    ).parent
    original_mkdir = Path.mkdir

    def reject_destination(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == destination_dir:
            raise PermissionError("configured Claude home is unwritable")
        original_mkdir(
            path,
            mode=mode,
            parents=parents,
            exist_ok=exist_ok,
        )

    monkeypatch.setattr(Path, "mkdir", reject_destination)

    result = runner.invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "clone",
            str(source),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "Error cloning session" in result.output
    assert "configured Claude home is unwritable" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "command_args",
    [
        ["smart-trim", "--agent", "claude", "Shared Plan"],
        ["smart-trim", "Shared Plan", "--agent", "claude"],
        ["resume", "--agent", "claude", "Shared Plan"],
        ["resume", "Shared Plan", "--agent", "claude"],
    ],
)
def test_interactive_agent_option_constrains_requested_session(
    command_args: list[str],
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive lookup retains both the session and agent constraint."""
    captured: dict[str, object] = {}

    def capture_ui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "claude_code_tools.aichat._find_and_run_session_ui",
        capture_ui,
    )
    result = runner.invoke(
        main,
        [*_root_args(claude_home, codex_home), *command_args],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["session_id"] == "Shared Plan"
    assert captured["agent_constraint"] == "claude"


@pytest.mark.parametrize(
    "command_args",
    [
        ["rollover", "--agent", "claude", "Shared Plan"],
        ["rollover", "Shared Plan", "--agent", "claude"],
    ],
)
def test_rollover_agent_constraint_keeps_interactive_mode(
    command_args: list[str],
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding only an agent constraint does not execute rollover directly."""
    captured: dict[str, object] = {}

    def capture_ui(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "claude_code_tools.aichat._find_and_run_session_ui",
        capture_ui,
    )
    result = runner.invoke(
        main,
        [*_root_args(claude_home, codex_home), *command_args],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured["session_id"] == "Shared Plan"
    assert captured["agent_constraint"] == "claude"
    assert captured["direct_action"] == "continue"
