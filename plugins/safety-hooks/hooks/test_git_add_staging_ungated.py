#!/usr/bin/env python3
"""
Unit tests for staging decisions in git_add_block_hook.py

Staging specific paths is not gated: `git add <path>` is allowed even when the
paths are already-tracked modified files. Bulk staging stays blocked outright.
"""
import os
import subprocess
import sys
import tempfile
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_add_block_hook import check_git_add_command


def _run(*args: str, cwd: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class TestStagingIsUngated(unittest.TestCase):
    """A repository with one modified file and one untracked file."""

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="git-add-hook-")
        self.addCleanup(
            subprocess.run, ["rm", "-rf", self.repo], check=False)

        _run("git", "init", "-q", "-b", "main", ".", cwd=self.repo)
        _run("git", "config", "user.email", "test@example.com", cwd=self.repo)
        _run("git", "config", "user.name", "Test", cwd=self.repo)
        os.makedirs(os.path.join(self.repo, "sub"), exist_ok=True)
        for name in ("tracked.txt", "sub/tracked.txt"):
            with open(os.path.join(self.repo, name), "w") as handle:
                handle.write("original\n")
        _run("git", "add", "tracked.txt", "sub/tracked.txt", cwd=self.repo)
        _run("git", "commit", "-q", "-m", "initial", cwd=self.repo)

        for name in ("tracked.txt", "sub/tracked.txt"):
            with open(os.path.join(self.repo, name), "w") as handle:
                handle.write("modified\n")
        with open(os.path.join(self.repo, "sub/new.txt"), "w") as handle:
            handle.write("new\n")

        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

    def assertAllowed(self, command: str) -> None:
        decision, reason = check_git_add_command(command)
        self.assertEqual((decision, reason), (False, None), command)

    def assertBlocked(self, command: str) -> None:
        decision, _ = check_git_add_command(command)
        self.assertIs(decision, True, command)

    def test_modified_file_is_allowed(self) -> None:
        self.assertAllowed("git add tracked.txt")

    def test_several_modified_files_are_allowed(self) -> None:
        self.assertAllowed("git add tracked.txt sub/tracked.txt")

    def test_directory_with_modified_files_is_allowed(self) -> None:
        self.assertAllowed("git add sub/")

    def test_update_flag_is_allowed(self) -> None:
        self.assertAllowed("git add -u")

    def test_add_in_another_repo_is_allowed(self) -> None:
        self.assertAllowed(f"git -C {self.repo} add tracked.txt")

    def test_bulk_staging_is_still_blocked(self) -> None:
        for command in ("git add -A", "git add .", "git add --all",
                        "git add *"):
            self.assertBlocked(command)

    def test_pathspec_file_still_asks(self) -> None:
        decision, _ = check_git_add_command(
            "git add --pathspec-from-file=list.txt")
        self.assertEqual(decision, "ask")


if __name__ == "__main__":
    unittest.main()
