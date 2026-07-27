"""Guards on the committed front-end bundle.

The bundle is built with Node and committed so installing the tool needs no
Node at all. That trade only works if a stale or unshippable bundle fails the
suite loudly rather than quietly reaching the human's browser.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

needs_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is required to enumerate the version-controlled sources",
)

from visual_brief.render.assets import (
    SCRIPT_NAME,
    STYLE_NAME,
    bundle_script,
    bundle_style,
)

PACKAGE_ROOT = Path(__file__).parents[1]
STAMP_TOOL = PACKAGE_ROOT / "tools" / "frontend_stamp.py"
STATIC_DIR = PACKAGE_ROOT / "src" / "visual_brief" / "static"


def _load_stamp_tool() -> ModuleType:
    """Import the build-stamp tool from the package's tools directory."""
    spec = importlib.util.spec_from_file_location(
        "visual_brief_frontend_stamp",
        STAMP_TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_bundle_matches_the_front_end_sources() -> None:
    """Fail loudly when the committed bundle is stale or hand-edited."""
    stamp_tool = _load_stamp_tool()

    try:
        stamp_tool.check_stamp()
    except stamp_tool.StaleBundleError as error:
        pytest.fail(str(error))


def test_bundle_is_two_files_with_stable_names() -> None:
    """Keep exactly one script and one stylesheet, both unhashed."""
    emitted = sorted(
        path.name for path in STATIC_DIR.iterdir() if path.is_file()
    )

    assert emitted == [STYLE_NAME, SCRIPT_NAME]


def test_the_stamp_survives_a_bare_vite_build() -> None:
    """Keep the staleness gate outside the directory the build empties."""
    stamp_tool = _load_stamp_tool()

    assert stamp_tool.STAMP_PATH.is_file()
    assert STATIC_DIR not in stamp_tool.STAMP_PATH.parents


def _write_frontend(root: Path) -> None:
    """Lay out the smallest front-end tree the stamp tool can fingerprint.

    Args:
        root: Directory to build the tree in.
    """
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "main.tsx").write_text("export const main = 1;\n")
    (root / "package.json").write_text('{"name": "sample"}\n')
    (root / ".gitignore").write_text(".DS_Store\nnode_modules/\n")


@needs_git
def test_an_ignored_dropping_cannot_declare_the_bundle_stale(
    tmp_path: Path,
) -> None:
    """Keep `.DS_Store` out of the fingerprint; git never shows it either."""
    stamp_tool = _load_stamp_tool()
    frontend = tmp_path / "frontend"
    _write_frontend(frontend)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    before = stamp_tool.source_fingerprint(frontend)

    (frontend / ".DS_Store").write_bytes(b"finder junk")
    (frontend / "src" / ".DS_Store").write_bytes(b"more finder junk")

    assert stamp_tool.source_fingerprint(frontend) == before


@needs_git
def test_editing_a_unit_test_does_not_declare_the_bundle_stale(
    tmp_path: Path,
) -> None:
    """Fingerprint what the build reads, not what Vitest reads."""
    stamp_tool = _load_stamp_tool()
    frontend = tmp_path / "frontend"
    _write_frontend(frontend)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    before = stamp_tool.source_fingerprint(frontend)

    (frontend / "src" / "main.test.ts").write_text("it('works', () => {});\n")
    (frontend / "test" / "sample.ts").write_text("export const sample = 1;\n")
    assert stamp_tool.source_fingerprint(frontend) == before

    (frontend / "src" / "main.tsx").write_text("export const main = 2;\n")
    assert stamp_tool.source_fingerprint(frontend) != before


@needs_git
def test_the_fingerprint_is_the_same_with_and_without_git(
    tmp_path: Path,
) -> None:
    """Keep an unpacked sdist agreeing with the checkout it was built from."""
    stamp_tool = _load_stamp_tool()
    tracked = tmp_path / "repo" / "frontend"
    _write_frontend(tracked)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path / "repo", check=True)
    (tracked / ".DS_Store").write_bytes(b"finder junk")
    (tracked / "src" / "main.test.ts").write_text("it('works', () => {});\n")

    plain = tmp_path / "plain"
    shutil.copytree(tmp_path / "repo", plain)
    shutil.rmtree(plain / ".git")

    assert stamp_tool.source_fingerprint(tracked) == stamp_tool.source_fingerprint(
        plain / "frontend"
    )


@needs_git
def test_writing_a_stamp_names_the_sources_git_has_never_seen(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warn before a file no other checkout has is baked into the stamp."""
    stamp_tool = _load_stamp_tool()
    frontend = tmp_path / "frontend"
    _write_frontend(frontend)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    static = tmp_path / "static"
    static.mkdir()
    for name in stamp_tool.BUNDLE_NAMES:
        (static / name).write_text("built\n")
    (frontend / "src" / "scratch.ts").write_text("export const scratch = 1;\n")

    stamp_tool.write_stamp(frontend, static, tmp_path / "stamp.json")

    warning = capsys.readouterr().err
    assert "src/scratch.ts" in warning
    assert "main.tsx" not in warning


def test_bundle_can_be_inlined_into_a_single_page() -> None:
    """Refuse anything that would leave the page or end its elements."""
    for name, text in ((SCRIPT_NAME, bundle_script()), (STYLE_NAME, bundle_style())):
        assert text.strip(), name
        assert re.search(r"https?://", text) is None, name
        assert re.search(r"</(script|style)", text, re.IGNORECASE) is None, name
        assert "<!--" not in text, name


def _reduced_motion_block(style: str) -> str:
    """Return the shipped rules that apply when motion is unwelcome.

    Args:
        style: The bundled stylesheet.

    Returns:
        The body of the reduced-motion media block.

    Raises:
        AssertionError: If the stylesheet ships no such block.
    """
    match = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{", style)
    assert match is not None, "the bundle ships no reduced-motion rules"
    depth = 1
    start = match.end()
    for index in range(start, len(style)):
        if style[index] == "{":
            depth += 1
        elif style[index] == "}":
            depth -= 1
            if depth == 0:
                return style[start:index]
    raise AssertionError("the reduced-motion block is never closed")


def test_the_agent_is_working_sign_degrades_to_a_static_label() -> None:
    """Keep the words readable, and still, where motion is unwelcome.

    The shimmer paints through transparent text, so switching the animation
    off is not enough on its own: the fill has to come back or the human is
    left with a blank line where the reassurance should be. Chrome's motion
    preference cannot be emulated through the browser driver this suite uses,
    so the guarantee is checked in the stylesheet that ships.
    """
    style = bundle_style()

    assert ".working-text{" in style.replace(" ", "")
    assert "agent-working" in style
    reduced = _reduced_motion_block(style)
    assert ".working-text" in reduced
    assert "animation:none" in reduced.replace(" ", "")
    assert "-webkit-text-fill-color:currentcolor" in reduced.replace(" ", "").lower()


def test_bundle_has_no_dynamic_import_or_module_syntax() -> None:
    """Keep the script a single inlinable classic script."""
    script = bundle_script()

    assert "import(" not in script
    assert not re.search(r"\bfrom\s*[\"']", script)
    assert "sourceMappingURL" not in script
