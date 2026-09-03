"""Tests for the standalone Visual Brief release targets."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
VOXTYPE_PYPROJECT = ROOT / "packages/voxtype/pyproject.toml"
VISUAL_BRIEF_PYPROJECT = ROOT / "packages/visual-brief/pyproject.toml"
PUBLISHING_DOC = ROOT / "docs-site/src/content/docs/development/publishing.md"
MAKE_COMMANDS_DOC = ROOT / "docs-site/src/content/docs/development/make-commands.md"


def test_visual_brief_has_voxtype_release_target_parity() -> None:
    """Visual Brief should expose the same release conveniences as Voxtype."""
    text = MAKEFILE.read_text()
    targets = set(re.findall(r"^([a-z0-9-]+):", text, flags=re.MULTILINE))

    expected = {
        "visual-brief-version",
        "visual-brief-all",
        "visual-brief-all-patch",
        "visual-brief-all-minor",
        "visual-brief-all-major",
    }

    assert expected <= targets
    assert re.search(
        r"^visual-brief-all:\s+visual-brief-release\s+visual-brief-publish$",
        text,
        flags=re.MULTILINE,
    )
    for part in ("patch", "minor", "major"):
        recipe = rf"visual-brief-all-{part}:\n\t@\$\(MAKE\) "
        recipe += rf"visual-brief-release BUMP={part}"
        assert re.search(rf"^{recipe}$", text, flags=re.MULTILINE)


def test_visual_brief_release_conveniences_appear_in_help() -> None:
    """The discoverable Make help should explain both release workflows."""
    result = subprocess.run(
        ["make", "help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "make visual-brief-all [BUMP=...]" in result.stdout
    assert "make visual-brief-all-patch / -minor / -major" in result.stdout


def test_standalone_test_targets_use_package_environments() -> None:
    """Fresh workspaces should not fall through to an external pytest."""
    text = MAKEFILE.read_text()

    for package in ("voxtype", "visual-brief"):
        assert f"uv run --package {package} pytest" in text

    for path in (VOXTYPE_PYPROJECT, VISUAL_BRIEF_PYPROJECT):
        project = tomllib.loads(path.read_text())
        dev_dependencies = project["dependency-groups"]["dev"]
        assert any(dependency.startswith("pytest") for dependency in dev_dependencies)


def test_starlight_documents_standalone_python_releases() -> None:
    """Maintainers should find both standalone package workflows in Starlight."""
    publishing = PUBLISHING_DOC.read_text()
    commands = MAKE_COMMANDS_DOC.read_text()

    for package in ("voxtype", "visual-brief"):
        assert f"make {package}-all-patch" in publishing
        assert f"make {package}-publish" in publishing
        assert f"make {package}-all" in publishing
        assert f"make {package}-all-patch" in commands
        assert f"make {package}-publish" in commands
        assert f"make {package}-all" in commands
