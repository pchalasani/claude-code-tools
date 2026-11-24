"""Check whether an index-to-worktree restore would overwrite file edits."""

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import List, Optional


def _git(repo_dir: str, *args: str) -> bytes:
    """Run read-only Git plumbing without optional locks or filesystem hooks."""
    return subprocess.check_output(
        ["git", "--no-optional-locks", "--literal-pathspecs",
         "-c", "core.fsmonitor=false", *args],
        cwd=repo_dir, stderr=subprocess.DEVNULL, timeout=10,
    )


def _safe_file(repo_dir: str, path: str) -> bool:
    """Require one ordinary index entry and a missing or identical file."""
    if (not re.fullmatch(r"[\w ./@+,=:%-]+", path)
            or path.startswith((":", "=")) or path.endswith(("/", "/."))
            or ".." in Path(path).parts):
        return False
    relative = os.path.relpath(path, repo_dir) if os.path.isabs(path) else path
    relative = os.path.normpath(relative)
    if relative == "." or ".." in Path(relative).parts:
        return False

    # --debug exposes intent-to-add as well as skip-worktree and assume-valid.
    # Accept only an ordinary stage-zero entry with no flags. The exact path
    # check excludes directories, even if only a single tracked child matches.
    entry = _git(repo_dir, "ls-files", "--stage", "--debug", "-z", "--", relative)
    header, _, details = entry.partition(b"\0")
    fields, _, name = header.partition(b"\t")
    if (name != os.fsencode(relative) or b"\0" in details
            or not details.endswith(b"\tflags: 0\n")):
        return False
    mode, oid, stage = fields.split()
    if mode not in (b"100644", b"100755") or stage != b"0":
        return False

    # Git can replace a non-directory ancestor while recreating a missing file.
    target = Path(repo_dir)
    parts = Path(relative).parts
    permissions = 0
    for index, part in enumerate(parts):
        target = target / part
        try:
            permissions = target.lstat().st_mode
        except FileNotFoundError:
            return True
        if index < len(parts) - 1 and not stat.S_ISDIR(permissions):
            return False

    if (not stat.S_ISREG(permissions)
            or bool(permissions & stat.S_IXUSR) != (mode == b"100755")):
        return False
    # A raw content comparison also catches edits hidden by stat caching.
    # Avoid running clean filters in the repository from a permission hook.
    return _git(repo_dir, "hash-object", "--no-filters", "--", relative).strip() == oid


def safe_index_paths(repo_dir: Optional[str], paths: List[str]) -> bool:
    """Return whether each named file can safely be restored from its index."""
    if not repo_dir or not paths:
        return False
    try:
        return (_git(repo_dir, "rev-parse", "--is-inside-work-tree") == b"true\n"
                and all(_safe_file(repo_dir, path) for path in paths))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
