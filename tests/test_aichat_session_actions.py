"""Regressions for session resolution used by direct ``aichat`` actions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from tests.resolve_session_helpers import (
    FakeHome,
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
    ) -> None:
        captured["session_id"] = session_id
        captured["project_path"] = project_path

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
