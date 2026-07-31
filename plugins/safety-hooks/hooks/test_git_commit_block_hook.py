#!/usr/bin/env python3
"""
Unit tests for git_commit_block_hook.py

Tests cover:
    - `git -C <dir> commit` and other global-option forms reaching the gate
    - Compound commands
    - The session-scoped allow flag
    - Commands that merely mention "git commit" staying allowed
"""
import os
import sys
import tempfile
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_commit_block_hook import check_git_commit_command, git_subcommand


class TestGitSubcommand(unittest.TestCase):
    """Tests for git_subcommand() resolution past git's global options."""

    def test_plain(self):
        self.assertEqual(git_subcommand("git commit -m 'x'"), "commit")

    def test_dash_c_directory(self):
        self.assertEqual(git_subcommand("git -C /tmp/repo commit -m 'x'"), "commit")

    def test_dash_c_config(self):
        self.assertEqual(
            git_subcommand("git -c user.name=Bot commit -m 'x'"), "commit")

    def test_combined_global_options(self):
        self.assertEqual(
            git_subcommand("git -C /tmp/repo -c core.pager=cat commit -m 'x'"),
            "commit")

    def test_no_verify_is_not_a_global_option(self):
        self.assertEqual(git_subcommand("git commit --no-verify -m 'x'"), "commit")

    def test_other_subcommands(self):
        self.assertEqual(git_subcommand("git status"), "status")
        self.assertEqual(git_subcommand("git -C /tmp/repo status"), "status")

    def test_not_git(self):
        self.assertIsNone(git_subcommand("echo git commit"))
        self.assertIsNone(git_subcommand(""))

    def test_absolute_path_to_git_binary(self):
        self.assertEqual(git_subcommand("/usr/bin/git commit -m 'x'"), "commit")


class TestAsksForApproval(unittest.TestCase):
    """Commands that must reach the approval prompt."""

    def assertAsks(self, command, message=None):
        decision, reason = check_git_commit_command(command)
        self.assertEqual(decision, "ask", message or command)
        self.assertIsNotNone(reason)

    def test_plain_commit(self):
        self.assertAsks("git commit -m 'x'")

    def test_commit_all(self):
        self.assertAsks("git commit -am 'x'")

    def test_compound_command(self):
        self.assertAsks("cd /tmp && git commit -m 'x'")

    def test_commit_tree(self):
        self.assertAsks("git commit-tree abc123 -m 'x'")

    # --- Regressions: git's global options put the subcommand past argv[1]

    def test_dash_c_directory(self):
        """`git -C <dir> commit` is a commit and must still be gated."""
        self.assertAsks(
            "git -C /tmp/some-repo commit -m 'x'",
            "git -C <dir> commit bypassed the approval gate")

    def test_dash_c_directory_relative(self):
        self.assertAsks("git -C ../other-repo commit -m 'x'")

    def test_dash_c_config_override(self):
        self.assertAsks("git -c user.email=bot@example.com commit -m 'x'")

    def test_git_dir_and_work_tree(self):
        self.assertAsks(
            "git --git-dir /tmp/r/.git --work-tree /tmp/r commit -m 'x'")

    def test_dash_c_inside_a_compound_command(self):
        self.assertAsks("echo hi && git -C /tmp/some-repo commit -m 'x'")

    def test_absolute_git_path_with_dash_c(self):
        self.assertAsks("/usr/bin/git -C /tmp/some-repo commit -m 'x'")


class TestAllowed(unittest.TestCase):
    """Commands that must not trigger the prompt."""

    def assertAllows(self, command):
        decision, _ = check_git_commit_command(command)
        self.assertEqual(decision, "allow", command)

    def test_other_git_commands(self):
        self.assertAllows("git status")
        self.assertAllows("git log --oneline")
        self.assertAllows("git -C /tmp/some-repo status")

    def test_commit_mentioned_but_not_run(self):
        self.assertAllows("echo 'remember to git commit'")
        self.assertAllows("grep -r 'git commit' docs/")

    def test_empty(self):
        self.assertAllows("")


class TestSessionAllowFlag(unittest.TestCase):
    """The session-scoped flag file bypasses the prompt."""

    def test_flag_allows_commit(self):
        flag_dir = "/tmp/claude"
        os.makedirs(flag_dir, exist_ok=True)
        session_id = "test-session-" + str(os.getpid())
        flag = os.path.join(flag_dir, f"allow-git-commit.{session_id}")
        with open(flag, "w") as handle:
            handle.write(session_id)
        self.addCleanup(lambda: os.path.exists(flag) and os.remove(flag))

        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id=session_id)
        self.assertEqual(decision, "allow")

        # And the same flag must cover the -C form, or the bypass is
        # inconsistent with the gate.
        decision, _ = check_git_commit_command(
            "git -C /tmp/some-repo commit -m 'x'", session_id=session_id)
        self.assertEqual(decision, "allow")

    def test_without_flag_still_asks(self):
        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id="no-such-session-" + str(os.getpid()))
        self.assertEqual(decision, "ask")


if __name__ == "__main__":
    unittest.main()
