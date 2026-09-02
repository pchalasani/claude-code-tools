"""Packaging contracts for the Visual Brief plugin skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SKILL = ROOT / "packages" / "visual-brief" / "SKILL.md"
PLUGIN = ROOT / "plugins" / "visual-brief"
PLUGIN_SKILL = PLUGIN / "skills" / "visual-brief" / "SKILL.md"


def read_json(path: Path) -> dict[str, Any]:
    """Read and type-check one JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_plugin_skill_is_the_canonical_visual_brief_skill() -> None:
    """Both agents must discover an exact copy of the maintained skill."""
    assert PLUGIN_SKILL.read_text(encoding="utf-8") == CANONICAL_SKILL.read_text(
        encoding="utf-8"
    )

    codex_manifest = read_json(PLUGIN / ".codex-plugin" / "plugin.json")
    assert codex_manifest["skills"] == "./skills/"
