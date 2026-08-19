"""Session-name coverage for legacy ``aichat`` command wrappers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from tests.resolve_session_helpers import FakeHome, claude_home, codex_home

__all__ = ["claude_home", "codex_home"]


def _root_args(claude: FakeHome, codex: FakeHome) -> list[str]:
    """Return root options that isolate both agent homes."""
    return [
        "--claude-home",
        str(claude.path),
        "--codex-home",
        str(codex.path),
    ]


@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("find-original", ["--verbose"]),
        ("find-derived", ["--tree"]),
        ("menu", []),
    ],
)
def test_required_legacy_session_uses_delegate_usage_error(
    command: str,
    options: list[str],
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Omitted SESSION reports ordinary CLI usage, not an internal error."""
    result = CliRunner().invoke(
        main,
        [*_root_args(claude_home, codex_home), command, *options],
    )

    assert result.exit_code == 2
    assert "usage:" in result.output.casefold()
    assert "required" in result.output.casefold()
    assert "MissingPositionalError" not in result.output


@pytest.mark.parametrize(
    ("query", "expected_agent", "expected_index"),
    [
        ("Unique Deployment Review", "claude", 2),
        ("Unique Codex Migration", "codex", 2),
    ],
)
@pytest.mark.parametrize("shortcut", [False, True])
def test_menu_and_shortcut_resolve_unique_names(
    query: str,
    expected_agent: str,
    expected_index: int,
    shortcut: bool,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both menu entry forms resolve names before launching the UI."""
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
    arguments = [*_root_args(claude_home, codex_home)]
    if not shortcut:
        arguments.append("menu")
    arguments.append(query)

    result = CliRunner().invoke(main, arguments, catch_exceptions=False)

    expected_home = claude_home if expected_agent == "claude" else codex_home
    assert result.exit_code == 0, result.output
    assert captured["agent"] == expected_agent
    assert captured["file_path"] == str(expected_home.files[expected_index])


@pytest.mark.parametrize(
    ("query", "expected_agent"),
    [
        ("Unique Deployment Review", "claude"),
        ("Unique Codex Migration", "codex"),
    ],
)
def test_delete_force_resolves_unique_names_for_both_agents(
    query: str,
    expected_agent: str,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Delete resolves either agent's name before unlinking exactly one file."""
    expected_home = claude_home if expected_agent == "claude" else codex_home
    selected = expected_home.files[2]

    result = CliRunner().invoke(
        main,
        [*_root_args(claude_home, codex_home), "delete", query, "--force"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert not selected.exists()
    assert all(path.exists() for path in expected_home.files[:2])
    assert "Session deleted" in result.output


@pytest.mark.parametrize(
    ("command", "query", "delegate", "home_option", "expected_file"),
    [
        (
            "export-claude",
            "Unique Deployment Review",
            "claude_code_tools.export_claude_session.main",
            "--claude-home",
            "claude",
        ),
        (
            "export-codex",
            "Unique Codex Migration",
            "claude_code_tools.export_codex_session.main",
            "--codex-home",
            "codex",
        ),
    ],
)
def test_agent_exports_resolve_names_and_preserve_option_order(
    command: str,
    query: str,
    delegate: str,
    home_option: str,
    expected_file: str,
    tmp_path: Path,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent exports replace only SESSION when options precede it."""
    captured: list[str] = []

    def capture_main() -> None:
        captured.extend(sys.argv[1:])

    monkeypatch.setattr(delegate, capture_main)
    output = tmp_path / f"{command}.md"
    selected_home = claude_home if expected_file == "claude" else codex_home
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            "--output",
            str(output),
            home_option,
            str(selected_home.path),
            query,
            "--verbose",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured[0:2] == ["--output", str(output)]
    assert str(selected_home.files[2]) in captured
    assert "--verbose" in captured


@pytest.mark.parametrize(
    ("command", "query", "delegate", "extra"),
    [
        (
            "find-original",
            "Unique Deployment Review",
            "claude_code_tools.find_original_session.main",
            ["--verbose"],
        ),
        (
            "find-derived",
            "Unique Codex Migration",
            "claude_code_tools.find_trimmed_sessions.main",
            ["--tree", "--stats"],
        ),
    ],
)
def test_lineage_wrappers_resolve_names_and_preserve_flags(
    command: str,
    query: str,
    delegate: str,
    extra: list[str],
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage passthrough wrappers substitute only the session argument."""
    captured: list[str] = []

    def capture_main() -> None:
        captured.extend(sys.argv[1:])

    monkeypatch.setattr(delegate, capture_main)
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            command,
            *extra,
            query,
        ],
        catch_exceptions=False,
    )

    expected = (
        claude_home.files[2] if command == "find-original" else (codex_home.files[2])
    )
    assert result.exit_code == 0, result.output
    assert str(expected) in captured
    if command == "find-derived":
        assert captured[-4:] == [
            "--claude-home",
            str(claude_home.path),
            "--codex-home",
            str(codex_home.path),
        ]
    assert all(flag in captured for flag in extra)


def test_trim_in_place_json_resolves_unique_claude_name(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """The hook-safe JSON path accepts a Claude session name without UI."""
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "trim-in-place",
            "Unique Deployment Review",
            "--dry-run",
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("\n") == 1
    assert "session_file" in result.output
    assert str(claude_home.files[2]) in result.output


def test_trim_in_place_json_ambiguity_never_prompts(
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ambiguous name remains a single JSON error even on a TTY."""
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        lambda _stream: True,
    )
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "trim-in-place",
            "Shared Plan",
            "--dry-run",
            "--json",
        ],
        input="1\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert result.output.count("\n") == 1
    assert "Ambiguous session" in result.output
    assert "Which session" not in result.output
