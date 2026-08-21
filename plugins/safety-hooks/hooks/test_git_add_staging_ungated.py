#!/usr/bin/env python3
"""
Unit tests for staging decisions in git_add_block_hook.py

Staging specific paths is not gated: `git add <path>` is allowed even when the
paths are already-tracked modified files. Bulk staging stays blocked outright,
including pathspecs that select the whole repository by another name.
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

    def test_root_equivalent_pathspecs_are_blocked(self) -> None:
        """Anything that stages what `git add .` stages is blocked too."""
        for command in (
            "git add ./",
            "git add .//",
            "git add :/",
            "git add :",
            "git add :(top)",
            'git add ""',
            "git add /",
            "git add ../",
            f"git add {self.repo}",
            f"git add {self.repo}/",
            f"git add {os.path.realpath(self.repo)}/",
            f"git -C {self.repo} add ./",
            "git add tracked.txt ./",
            "git add -- ./",
        ):
            with self.subTest(command=command):
                self.assertBlocked(command)

    def test_paths_below_the_root_are_still_allowed(self) -> None:
        for command in (
            "git add sub/",
            "git add ./sub",
            "git add :/sub",
            "git add :(top)sub",
            "git add :!tracked.txt",
            f"git add {self.repo}/sub",
            "git add --chmod=+x tracked.txt",
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_repository_root_magic_variants_are_blocked(self) -> None:
        """`git add ://` stages the whole repository, same as `git add :/`."""
        for command in ("git add ://", "git add :////", "git add :(top)/"):
            with self.subTest(command=command):
                self.assertBlocked(command)

    def test_routed_work_tree_is_the_base_for_absolute_paths(self) -> None:
        """A routed work tree is resolved, not treated as unknown."""
        for command in (
            f"GIT_DIR={self.repo}/.git GIT_WORK_TREE={self.repo} "
            f"git add {self.repo}",
            f"git --git-dir={self.repo}/.git --work-tree={self.repo} "
            f"add {self.repo}",
            f"env GIT_WORK_TREE={self.repo} git add {self.repo}",
        ):
            with self.subTest(command=command):
                self.assertBlocked(command)

        for command in (
            f"GIT_DIR={self.repo}/.git GIT_WORK_TREE={self.repo} "
            f"git add {self.repo}/sub",
            f"git --work-tree={self.repo} add {self.repo}/sub",
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_relative_work_tree_resolves_against_the_final_directory(
        self,
    ) -> None:
        """`git -C` moves what a relative GIT_WORK_TREE resolves against."""
        parent = os.path.dirname(self.repo)
        name = os.path.basename(self.repo)
        self.assertBlocked(
            f"GIT_DIR={self.repo}/.git GIT_WORK_TREE={name} "
            f"git -C {parent} add {self.repo}")
        self.assertBlocked(
            f"git -C {parent} --work-tree={name} add {self.repo}")
        self.assertAllowed(
            f"GIT_WORK_TREE={name} git -C {parent} add {self.repo}/sub")

    def test_unresolvable_work_tree_blocks_absolute_paths(self) -> None:
        """With the base unknown, an absolute path may well be the whole tree."""
        self.assertBlocked('GIT_WORK_TREE="$TREE" git add /some/absolute/path')

    def test_pathspec_file_still_asks(self) -> None:
        decision, _ = check_git_add_command(
            "git add --pathspec-from-file=list.txt")
        self.assertEqual(decision, "ask")


if __name__ == "__main__":
    unittest.main()
