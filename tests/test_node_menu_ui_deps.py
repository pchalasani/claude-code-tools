"""Tests for the Node UI dependency preflight check.

A source checkout used via an editable install keeps its own
``node_ui/node_modules``. When that directory is missing, spawning Node
produces a raw ``ERR_MODULE_NOT_FOUND`` stack trace, so the bridge checks
first and reports something actionable instead.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from claude_code_tools import node_menu_ui


def _make_node_ui(root: Path, packages: tuple[str, ...]) -> Path:
    """Create a node_ui directory populated with the given packages.

    Args:
        root: Directory in which to create ``node_ui``.
        packages: Package names to create under ``node_modules``.

    Returns:
        Path to the created ``node_ui`` directory.
    """
    node_ui = root / "node_ui"
    node_ui.mkdir()
    (node_ui / "menu.js").write_text("// stub\n", encoding="utf-8")
    if packages:
        node_modules = node_ui / "node_modules"
        node_modules.mkdir()
        for name in packages:
            (node_modules / name).mkdir(parents=True)
    return node_ui


def test_missing_packages_when_node_modules_absent(tmp_path: Path) -> None:
    """Every requirement is reported when node_modules does not exist."""
    node_ui = _make_node_ui(tmp_path, ())

    missing = node_menu_ui._missing_node_ui_packages(node_ui)

    assert missing == list(node_menu_ui.NODE_UI_REQUIRED_PACKAGES)


def test_missing_packages_reports_only_absent_ones(tmp_path: Path) -> None:
    """A partially installed node_modules reports just the gaps."""
    present = tuple(node_menu_ui.NODE_UI_REQUIRED_PACKAGES[:-1])
    node_ui = _make_node_ui(tmp_path, present)

    missing = node_menu_ui._missing_node_ui_packages(node_ui)

    assert missing == [node_menu_ui.NODE_UI_REQUIRED_PACKAGES[-1]]


def test_no_missing_packages_when_fully_installed(tmp_path: Path) -> None:
    """A complete node_modules reports nothing missing."""
    node_ui = _make_node_ui(tmp_path, node_menu_ui.NODE_UI_REQUIRED_PACKAGES)

    assert node_menu_ui._missing_node_ui_packages(node_ui) == []


def test_run_node_fails_fast_without_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Node is never spawned when its dependencies are missing."""
    node_ui = _make_node_ui(tmp_path, ())
    monkeypatch.setattr(node_menu_ui, "_node_ui_dir", lambda: node_ui)

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("node should not be spawned")

    monkeypatch.setattr(node_menu_ui.subprocess, "run", _fail)

    code = node_menu_ui._run_node(tmp_path / "in.json", tmp_path / "out.json")

    assert code == 1
    message = capsys.readouterr().err
    assert "meow" in message
    assert str(node_ui / "node_modules") in message
    assert f"npm ci --prefix {shlex.quote(str(node_ui))} --omit=dev" in message


def test_setup_message_quotes_paths_with_spaces(tmp_path: Path) -> None:
    """The suggested npm command survives a checkout path containing spaces."""
    spaced = tmp_path / "my checkout"
    spaced.mkdir()
    node_ui = _make_node_ui(spaced, ())

    message = node_menu_ui._node_ui_setup_message(["meow"], node_ui)

    assert f"--prefix {shlex.quote(str(node_ui))} " in message
    assert f"--prefix {node_ui} " not in message


def test_windows_paths_use_double_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd.exe treats POSIX single quotes literally, so use its own quoting."""
    plain = _make_node_ui(tmp_path, ())
    monkeypatch.setattr(node_menu_ui.os, "name", "nt")
    # cmd.exe splits on metacharacters, not just spaces, so quote regardless.
    assert f'--prefix "{plain}" ' in node_menu_ui._node_ui_setup_message(["meow"], plain)
    monkeypatch.undo()

    spaced = tmp_path / "my checkout"
    spaced.mkdir()
    node_ui = _make_node_ui(spaced, ())
    monkeypatch.setattr(node_menu_ui.os, "name", "nt")

    message = node_menu_ui._node_ui_setup_message(["meow"], node_ui)

    assert f'--prefix "{node_ui}" ' in message
    assert "'" not in message.split("npm ci")[1].splitlines()[0]


def test_run_node_reports_missing_node_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An absent 'node' binary produces a message, not a traceback."""
    node_ui = _make_node_ui(tmp_path, node_menu_ui.NODE_UI_REQUIRED_PACKAGES)
    monkeypatch.setattr(node_menu_ui, "_node_ui_dir", lambda: node_ui)

    def _missing_binary(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("node")

    monkeypatch.setattr(node_menu_ui.subprocess, "run", _missing_binary)

    code = node_menu_ui._run_node(tmp_path / "in.json", tmp_path / "out.json")

    assert code == 1
    assert "'node' was not found on PATH" in capsys.readouterr().err


def test_repository_requirements_match_package_manifest() -> None:
    """The checked requirements are real node_ui runtime dependencies."""
    import json

    manifest_path = Path(__file__).resolve().parent.parent / "node_ui" / "package.json"
    dependencies = json.loads(manifest_path.read_text(encoding="utf-8"))["dependencies"]

    for name in node_menu_ui.NODE_UI_REQUIRED_PACKAGES:
        assert name in dependencies


def test_missing_node_runtime_is_reported_before_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without Node installed, telling the user to run npm would be useless."""
    node_ui = _make_node_ui(tmp_path, ())
    monkeypatch.setattr(node_menu_ui, "_node_ui_dir", lambda: node_ui)
    monkeypatch.setattr(node_menu_ui.shutil, "which", lambda _name: None)

    code = node_menu_ui._run_node(tmp_path / "in.json", tmp_path / "out.json")

    assert code == 1
    err = capsys.readouterr().err
    assert "'node' was not found on PATH" in err
    assert "npm ci" not in err
