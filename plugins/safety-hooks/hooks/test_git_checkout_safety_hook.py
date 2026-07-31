#!/usr/bin/env python3
"""
Unit tests for git_checkout_safety_hook.py

Tests cover:
    - Destructive checkouts that also mention "-b" somewhere
    - `git -C <dir> checkout` (the subcommand is not always argv[1])
    - Option tokens vs. substrings of ordinary filenames
    - Branch creation and --help staying exempt
    - The uncommitted-changes probe reading the repository the command targets
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_checkout_safety_hook import (
    check_git_checkout_command,
    parse_git_checkout,
)


class TestParseGitCheckout(unittest.TestCase):
    """Tests for parse_git_checkout() command splitting."""

    def test_plain_checkout(self):
        args, _ = parse_git_checkout("git checkout main")
        self.assertEqual(args, ["main"])

    def test_not_a_checkout(self):
        self.assertEqual(parse_git_checkout("git status"), (None, None))
        self.assertEqual(parse_git_checkout("ls checkout"), (None, None))
        self.assertEqual(parse_git_checkout(""), (None, None))

    def test_checkout_word_in_an_argument_is_not_the_subcommand(self):
        """A branch named "checkout" is not a checkout subcommand."""
        self.assertEqual(parse_git_checkout("git switch checkout"), (None, None))

    def test_dash_c_retargets_the_repository(self):
        """`git -C <dir>` runs in <dir>, so that is the repo to judge."""
        with tempfile.TemporaryDirectory() as tmp:
            args, repo_dir = parse_git_checkout(f"git -C {tmp} checkout main")
            self.assertEqual(args, ["main"])
            self.assertEqual(os.path.realpath(repo_dir), os.path.realpath(tmp))

    def test_absolute_path_to_git_binary(self):
        args, _ = parse_git_checkout("/usr/bin/git checkout main")
        self.assertEqual(args, ["main"])

    def test_unresolvable_target_reports_unknown_not_cwd(self):
        """An unexpanded -C value must not fall back to the caller's repo."""
        args, repo_dir = parse_git_checkout('git -C "$REPO" checkout main')
        self.assertEqual(args, ["main"])
        self.assertIsNone(repo_dir)

    def test_work_tree_reports_unknown(self):
        _, repo_dir = parse_git_checkout("git --work-tree=/somewhere checkout main")
        self.assertIsNone(repo_dir)


class TestAlwaysBlocked(unittest.TestCase):
    """Destructive forms that must be blocked regardless of repo state."""

    def test_force_checkout(self):
        blocked, reason = check_git_checkout_command("git checkout -f")
        self.assertTrue(blocked)
        self.assertIn("FORCES checkout", reason)

    def test_force_long_form(self):
        blocked, _ = check_git_checkout_command("git checkout --force main")
        self.assertTrue(blocked)

    def test_checkout_dot(self):
        blocked, reason = check_git_checkout_command("git checkout .")
        self.assertTrue(blocked)
        self.assertIn("DISCARD ALL", reason)

    def test_checkout_double_dash_dot(self):
        blocked, _ = check_git_checkout_command("git checkout HEAD -- .")
        self.assertTrue(blocked)

    def test_checkout_double_dash_path(self):
        blocked, _ = check_git_checkout_command("git checkout HEAD -- src/app.ts")
        self.assertTrue(blocked)

    # --- Regressions: these are the forms a substring test for "-b" let through

    def test_force_combined_with_branch_creation(self):
        """`-f` discards work even when a branch is also being created."""
        blocked, reason = check_git_checkout_command("git checkout -f -b feature")
        self.assertTrue(blocked, "-f must be blocked even alongside -b")
        self.assertIn("FORCES checkout", reason)

    def test_force_in_a_short_option_cluster(self):
        blocked, _ = check_git_checkout_command("git checkout -fb feature")
        self.assertTrue(blocked, "-f inside a cluster must still be seen")

    def test_checkout_dot_with_branch_creation(self):
        blocked, _ = check_git_checkout_command("git checkout . -b feature")
        self.assertTrue(blocked, "'.' pathspec must be blocked even alongside -b")

    def test_filename_containing_dash_b_is_not_an_option(self):
        """"-b" inside a filename must not disarm the guard."""
        blocked, _ = check_git_checkout_command(
            "git checkout HEAD -- src/my-button.ts")
        self.assertTrue(blocked, "'my-button.ts' contains '-b' but is a filename")

    def test_branch_name_containing_dash_b_is_not_an_option(self):
        blocked, _ = check_git_checkout_command("git checkout -f my-branch")
        self.assertTrue(blocked)

    # --- Regressions: the `git -C <dir> <subcommand>` form

    def test_dash_c_force_checkout(self):
        blocked, reason = check_git_checkout_command(
            "git -C /some/other/repo checkout -f")
        self.assertTrue(blocked, "git -C <dir> checkout is still a checkout")
        self.assertIn("FORCES checkout", reason)

    def test_dash_c_checkout_dot(self):
        blocked, _ = check_git_checkout_command("git -C /some/other/repo checkout .")
        self.assertTrue(blocked)

    def test_compound_command_with_dash_c(self):
        blocked, _ = check_git_checkout_command(
            "echo hi && git -C /some/other/repo checkout -f")
        self.assertTrue(blocked)


class TestAllowed(unittest.TestCase):
    """Forms that must stay allowed."""

    def test_branch_creation(self):
        blocked, _ = check_git_checkout_command("git checkout -b feature")
        self.assertFalse(blocked)

    def test_help(self):
        self.assertFalse(check_git_checkout_command("git checkout --help")[0])
        self.assertFalse(check_git_checkout_command("git checkout -h")[0])

    def test_non_checkout_commands(self):
        self.assertFalse(check_git_checkout_command("git status")[0])
        self.assertFalse(check_git_checkout_command("git commit -m 'x'")[0])
        self.assertFalse(check_git_checkout_command("echo git checkout -f")[0])

    def test_unresolvable_target_is_not_probed(self):
        """Unknown target: command-level checks apply, no repo is probed."""
        blocked, _ = check_git_checkout_command('git -C "$REPO" checkout main')
        self.assertFalse(blocked)
        blocked, _ = check_git_checkout_command('git -C "$REPO" checkout -f')
        self.assertTrue(blocked, "command-level checks still apply")


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class TestUncommittedChangesProbeTargetsTheRightRepo(unittest.TestCase):
    """The warning must describe the repo the command targets, not the cwd."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "other-repo")
        os.makedirs(self.repo)
        _git("init", cwd=self.repo)
        with open(os.path.join(self.repo, "dirty-file.txt"), "w") as handle:
            handle.write("uncommitted\n")

        self.clean = os.path.join(self.tmp, "clean-cwd")
        os.makedirs(self.clean)
        _git("init", cwd=self.clean)

        self.original_cwd = os.getcwd()
        os.chdir(self.clean)
        self.addCleanup(os.chdir, self.original_cwd)

    def test_warns_about_the_target_repo(self):
        blocked, reason = check_git_checkout_command(
            f"git -C {self.repo} checkout main")
        self.assertTrue(blocked, "the target repo has uncommitted changes")
        self.assertIn("dirty-file.txt", reason)

    def test_clean_target_is_allowed_even_from_a_dirty_cwd(self):
        with open(os.path.join(self.clean, "local-noise.txt"), "w") as handle:
            handle.write("only in the cwd\n")
        blocked, _ = check_git_checkout_command(
            f"git -C {self.repo} checkout main")
        self.assertTrue(blocked, "target repo is the dirty one here")

        # And the mirror image: a clean target must not inherit the cwd's dirt.
        pristine = os.path.join(self.tmp, "pristine")
        os.makedirs(pristine)
        _git("init", cwd=pristine)
        blocked, _ = check_git_checkout_command(
            f"git -C {pristine} checkout main")
        self.assertFalse(blocked, "clean target must not inherit the cwd's dirt")


if __name__ == "__main__":
    unittest.main()
