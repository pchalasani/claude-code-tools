"""Packaging contracts for the shared Visual Brief lifecycle plugin."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "visual-brief"


def read_json(path: Path) -> dict[str, Any]:
    """Read and type-check one JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def marketplace_entry(path: Path) -> dict[str, Any]:
    """Return the unique Visual Brief entry from one marketplace."""
    entries = read_json(path)["plugins"]
    matches = [entry for entry in entries if entry["name"] == "visual-brief"]
    assert len(matches) == 1
    entry = matches[0]
    assert isinstance(entry, dict)
    return entry


def test_both_marketplaces_reference_the_same_plugin_root() -> None:
    """Claude and Codex must expose one policy-free plugin source."""
    claude = marketplace_entry(ROOT / ".claude-plugin" / "marketplace.json")
    codex = marketplace_entry(ROOT / ".agents" / "plugins" / "marketplace.json")

    assert claude["source"] == "./plugins/visual-brief"
    assert codex["source"] == {
        "source": "local",
        "path": "./plugins/visual-brief",
    }


def test_plugin_has_both_manifests_and_hook_definitions() -> None:
    """The common root must be installable by both providers."""
    claude_manifest = read_json(PLUGIN / ".claude-plugin" / "plugin.json")
    codex_manifest = read_json(PLUGIN / ".codex-plugin" / "plugin.json")

    assert claude_manifest["name"] == "visual-brief"
    assert codex_manifest["name"] == "visual-brief"
    hooks = read_json(PLUGIN / "hooks" / "hooks.json")
    serialized_hooks = json.dumps(hooks)
    assert "PostToolUse" in hooks["hooks"]
    assert "Bash" in serialized_hooks
    assert "Edit" in serialized_hooks
    assert "apply_patch" in serialized_hooks
    assert "exec_command" in serialized_hooks
    command = hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert command == '"${CLAUDE_PLUGIN_ROOT}/hooks/reminder-hook"'

    assert "hooks" not in codex_manifest
    for manifest in (claude_manifest, codex_manifest):
        assert manifest["author"] == {"name": "Prasad Chalasani"}
    assert codex_manifest["interface"]["displayName"] == "Visual Brief"
    assert codex_manifest["interface"]["developerName"] == "Prasad Chalasani"
    launcher = PLUGIN / "hooks" / "reminder-hook"
    assert launcher.is_file()
    assert os.access(launcher, os.X_OK)
    assert list((PLUGIN / "hooks").glob("*.py")) == []
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "visual-brief" in readme
    assert "prerequisite" in readme.lower()


def test_visual_brief_wheel_contains_shared_reminder_engine(
    tmp_path: Path,
) -> None:
    """Installed hooks must invoke the exact engine shipped in the wheel."""
    package = ROOT / "packages" / "visual-brief"
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=package,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"wheel build did not run successfully: {completed.stderr}")
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "visual_brief/reminders.py" in names

    hook_text = (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
    codex_text = (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
        encoding="utf-8"
    )
    launcher_text = (PLUGIN / "hooks" / "reminder-hook").read_text(
        encoding="utf-8"
    )
    plugin_text = hook_text + codex_text + launcher_text
    assert "visual_brief.reminders" not in plugin_text
    assert "visual-brief reminder-hook" in plugin_text


def test_hook_launcher_is_inert_without_cli(tmp_path: Path) -> None:
    """A plugin-only install must emit valid empty hook output."""
    launcher = PLUGIN / "hooks" / "reminder-hook"
    completed = subprocess.run(
        [str(launcher)],
        input="{}",
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": str(tmp_path)},
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {}


def test_hook_launcher_executes_available_cli(tmp_path: Path) -> None:
    """The launcher must delegate unchanged to the installed CLI."""
    fake_cli = tmp_path / "visual-brief"
    fake_cli.write_text(
        "#!/bin/sh\nprintf '{\"arguments\":\"%s\"}\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)
    launcher = PLUGIN / "hooks" / "reminder-hook"
    completed = subprocess.run(
        [str(launcher)],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": str(tmp_path)},
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "arguments": "reminder-hook --provider auto"
    }
