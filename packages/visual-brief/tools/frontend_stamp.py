"""Detect a committed front-end bundle that no longer matches its sources.

The built bundle is committed so ``uv tool install visual-brief`` needs no
Node. That only stays honest if a stale bundle is impossible to miss, so the
build records a fingerprint of every front-end source next to the artifacts and
the test target refuses to run when the two disagree.

``write`` fingerprints whatever is on disk, so it declares fresh whatever it
is pointed at: it must only ever run immediately after a build. ``make
visual-brief-frontend`` is the supported entry point and does exactly that,
which is why nothing else should invoke ``write`` by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PACKAGE_ROOT / "frontend"
STATIC_DIR = PACKAGE_ROOT / "src" / "visual_brief" / "static"
# The stamp lives outside the Vite output directory, which the build empties:
# a bare `npm run build` must not be able to delete it.
STAMP_PATH = PACKAGE_ROOT / "tools" / "bundle-stamp.json"
BUNDLE_NAMES = ("visual-brief.js", "visual-brief.css")
SKIPPED_DIRECTORIES = frozenset({"node_modules", "dist", "coverage"})
# Editor and platform droppings that git ignores. They are invisible to
# `git status`, so fingerprinting them would report a stale bundle that no
# rebuild can clear and that no other checkout can reproduce.
SKIPPED_FILES = frozenset({".DS_Store", "Thumbs.db"})
# Vitest sources cannot reach the emitted bundle: the build entry is
# ``src/main.tsx`` and nothing it imports is a test. Fingerprinting them would
# make editing a unit test refuse to run the suite until Node rebuilt an
# identical bundle.
TEST_SUFFIXES = (".test.ts", ".test.tsx")
TEST_DIRECTORIES = frozenset({"test", "__tests__"})
REBUILD_HINT = "run `make visual-brief-frontend` and commit the result"


class StaleBundleError(RuntimeError):
    """Raised when the committed bundle does not match the sources."""


def _hash_file(path: Path) -> str:
    """Return the SHA-256 of one file.

    Args:
        path: File to read.

    Returns:
        The hex digest of the file's bytes.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _builds_the_bundle(relative: Path) -> bool:
    """Report whether one front-end path can change the emitted bundle.

    Dot-prefixed directories are tool caches, but dot-prefixed *files* are
    kept: an ``.npmrc`` or a ``.browserslistrc`` changes what the build emits.

    ``index.html`` is kept too, even though this build is a library build
    whose entry is ``src/main.tsx`` and never reads it. Deciding what Vite
    takes from an HTML entry is not worth being wrong about, and including
    the dev harness only ever fails safe: a rebuild re-emits the same bytes.

    Args:
        relative: Path of the file, relative to the front-end root.

    Returns:
        True when the file belongs in the fingerprint.
    """
    directories = relative.parts[:-1]
    if any(part.startswith(".") for part in directories):
        return False
    if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
        return False
    if any(part in TEST_DIRECTORIES for part in directories):
        return False
    if relative.name in SKIPPED_FILES:
        return False
    return not relative.name.endswith(TEST_SUFFIXES)


def _git_paths(frontend_dir: Path, *selectors: str) -> list[Path] | None:
    """Ask git for the front-end paths matching one set of selectors.

    Args:
        frontend_dir: Root of the front-end project.
        selectors: ``git ls-files`` selectors, such as ``--cached``.

    Returns:
        The matching paths, or None when git cannot answer.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", *selectors, "--", "."],
            cwd=frontend_dir,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    names = completed.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [frontend_dir / name for name in names if name]


def _tracked_files(frontend_dir: Path) -> list[Path] | None:
    """Ask git which files under the front end belong to the project.

    Tracked files plus files git would offer to add: a source written but not
    yet staged still has to make the bundle stale, while anything ``.gitignore``
    covers is not this project's input.

    Args:
        frontend_dir: Root of the front-end project.

    Returns:
        The project's paths, or None when git cannot answer.
    """
    return _git_paths(frontend_dir, "--cached", "--others", "--exclude-standard")


def untracked_sources(frontend_dir: Path = FRONTEND_DIR) -> list[Path]:
    """List fingerprinted sources that git does not track yet.

    They count towards the fingerprint on this machine and exist on no other,
    so a stamp written over one reads as stale in every fresh clone.

    Args:
        frontend_dir: Root of the front-end project.

    Returns:
        Sorted untracked files that belong to the fingerprint.
    """
    found = _git_paths(frontend_dir, "--others", "--exclude-standard")
    if found is None:
        return []
    return sorted(
        path
        for path in found
        if _builds_the_bundle(path.relative_to(frontend_dir)) and path.is_file()
    )


def iter_source_files(frontend_dir: Path = FRONTEND_DIR) -> list[Path]:
    """List the front-end sources that determine the built bundle.

    Only version-controlled files are fingerprinted, so an ignored dropping
    such as ``.DS_Store`` cannot declare a byte-perfect bundle stale on one
    machine and not on another. A checkout without git — an unpacked sdist —
    falls back to walking the tree with the same exclusions.

    Args:
        frontend_dir: Root of the front-end project.

    Returns:
        Sorted files, excluding dependencies, build output and test-only code.
    """
    candidates = _tracked_files(frontend_dir)
    if candidates is None:
        candidates = list(frontend_dir.rglob("*"))
    found = {
        path
        for path in candidates
        if _builds_the_bundle(path.relative_to(frontend_dir)) and path.is_file()
    }
    return sorted(found)


def source_fingerprint(frontend_dir: Path = FRONTEND_DIR) -> str:
    """Fingerprint every front-end source file.

    Args:
        frontend_dir: Root of the front-end project.

    Returns:
        A hex digest covering each source path and its contents.

    Raises:
        StaleBundleError: If the front-end sources are missing.
    """
    if not frontend_dir.is_dir():
        raise StaleBundleError(f"missing front-end sources at {frontend_dir}")
    digest = hashlib.sha256()
    for path in iter_source_files(frontend_dir):
        relative = path.relative_to(frontend_dir).as_posix()
        digest.update(f"{relative}\0{_hash_file(path)}\n".encode("utf-8"))
    return digest.hexdigest()


def output_fingerprints(static_dir: Path = STATIC_DIR) -> dict[str, str]:
    """Fingerprint the committed bundle artifacts.

    Args:
        static_dir: Directory holding the built bundle.

    Returns:
        A mapping of artifact name to hex digest.

    Raises:
        StaleBundleError: If an artifact is missing.
    """
    fingerprints: dict[str, str] = {}
    for name in BUNDLE_NAMES:
        path = static_dir / name
        if not path.is_file():
            raise StaleBundleError(f"missing built bundle {path}; {REBUILD_HINT}")
        fingerprints[name] = _hash_file(path)
    return fingerprints


def write_stamp(
    frontend_dir: Path = FRONTEND_DIR,
    static_dir: Path = STATIC_DIR,
    stamp_path: Path = STAMP_PATH,
) -> dict[str, Any]:
    """Record the fingerprints of the sources and the built bundle.

    Both fingerprints are read from disk, so this must run immediately after a
    build; ``make visual-brief-frontend`` is the supported way to do that. A
    source git has never seen is fingerprinted too, and says so, because a
    stamp written over one cannot be reproduced anywhere else.

    Args:
        frontend_dir: Root of the front-end project.
        static_dir: Directory holding the built bundle.
        stamp_path: File the fingerprints are recorded in.

    Returns:
        The stamp that was written.
    """
    strangers = untracked_sources(frontend_dir)
    if strangers:
        names = ", ".join(
            path.relative_to(frontend_dir).as_posix() for path in strangers
        )
        print(
            f"warning: fingerprinting untracked front-end sources: {names}; "
            "git add them or every other checkout will read this stamp as "
            "stale",
            file=sys.stderr,
        )
    stamp: dict[str, Any] = {
        "sources": source_fingerprint(frontend_dir),
        "outputs": output_fingerprints(static_dir),
    }
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n")
    return stamp


def read_stamp(stamp_path: Path = STAMP_PATH) -> dict[str, Any]:
    """Read the committed stamp.

    Args:
        stamp_path: File the fingerprints were recorded in.

    Returns:
        The recorded stamp.

    Raises:
        StaleBundleError: If the stamp is missing or unreadable.
    """
    try:
        loaded: Any = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StaleBundleError(
            f"cannot read the bundle stamp at {stamp_path}; {REBUILD_HINT}"
        ) from error
    if not isinstance(loaded, dict):
        raise StaleBundleError(
            f"malformed bundle stamp at {stamp_path}; {REBUILD_HINT}"
        )
    return loaded


def check_stamp(
    frontend_dir: Path = FRONTEND_DIR,
    static_dir: Path = STATIC_DIR,
    stamp_path: Path = STAMP_PATH,
) -> None:
    """Fail when the committed bundle is stale.

    Args:
        frontend_dir: Root of the front-end project.
        static_dir: Directory holding the built bundle.
        stamp_path: File the fingerprints were recorded in.

    Raises:
        StaleBundleError: If sources or artifacts differ from the stamp.
    """
    stamp = read_stamp(stamp_path)
    expected_sources = source_fingerprint(frontend_dir)
    if stamp.get("sources") != expected_sources:
        raise StaleBundleError(
            "the committed visual-brief bundle is stale: front-end sources "
            f"changed since it was built; {REBUILD_HINT}"
        )
    expected_outputs = output_fingerprints(static_dir)
    if stamp.get("outputs") != expected_outputs:
        raise StaleBundleError(
            "the committed visual-brief bundle was edited by hand: its "
            f"artifacts do not match the recorded build; {REBUILD_HINT}"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the stamp tool.

    Args:
        argv: Command line arguments, defaulting to ``sys.argv``.

    Returns:
        A process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("write", "check"))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "write":
            stamp = write_stamp()
            print(f"bundle stamp written: sources {stamp['sources'][:12]}")
        else:
            check_stamp()
            print("bundle stamp matches the front-end sources")
    except StaleBundleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
