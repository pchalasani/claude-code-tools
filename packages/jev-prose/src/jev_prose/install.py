"""Install the bundled writing-loop skill for either coding agent."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from .backend import DetectorError


def install_skill(target: str, force: bool = False) -> list[str]:
    """Copy the skill globally, preflighting conflicts before writing.

    Args:
        target: claude, codex, or both.
        force: Explicitly replace a different existing skill with this one.

    Returns:
        Paths to the installed SKILL.md files.
    """
    roots = {
        "claude": Path(os.environ.get(
            "CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")
        )).expanduser(),
        "codex": Path(os.environ.get(
            "CODEX_HOME", str(Path.home() / ".codex")
        )).expanduser(),
    }
    targets = list(roots) if target == "both" else [target]
    content = files("jev_prose").joinpath("SKILL.md").read_text(encoding="utf-8")
    paths = [roots[name] / "skills/jev-prose/SKILL.md" for name in targets]
    for path in paths:
        if path.exists() and path.read_text(encoding="utf-8") != content and not force:
            raise DetectorError(f"Existing different skill at {path}; use --force.")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [str(path) for path in paths]
