#!/usr/bin/env python3
"""
Unit tests for the `git commit -a` check in git_add_block_hook.py

Tests cover:
    - Option tokens vs. substrings of paths and quoted text
    - Short option clusters (-am, -aS)
    - Long options with attached and separate values
    - Options that do and do not supply a commit message
    - The `--` pathspec separator
"""
import os
import sys
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_add_block_hook import _commit_option_tokens, check_git_add_command


def _blocks(command):
    """True when the hook blocks outright (as opposed to allowing or asking)."""
    return check_git_add_command(command)[0] is True


class TestCommitOptionTokens(unittest.TestCase):
    """Tests for _commit_option_tokens() parsing."""

    def test_short_cluster_is_split(self):
        self.assertEqual(_commit_option_tokens("git commit -am 'x'"),
                         ['-a', '-m'])

    def test_value_of_a_value_option_is_not_an_option(self):
        self.assertEqual(_commit_option_tokens("git commit -m -a"), ['-m'])

    def test_long_option_with_attached_value(self):
        self.assertEqual(_commit_option_tokens("git commit --message=hi"),
                         ['--message'])

    def test_long_option_with_separate_value(self):
        self.assertEqual(_commit_option_tokens("git commit --file /tmp/m.txt"),
                         ['--file'])

    def test_pathspec_separator_ends_options(self):
        self.assertEqual(
            _commit_option_tokens("git commit -a -- src/-m-file.txt"), ['-a'])

    def test_path_argument_is_not_an_option(self):
        self.assertEqual(
            _commit_option_tokens("git commit -F /tmp/claude-agent/msg.txt"),
            ['-F'])


class TestBlockedCommitAll(unittest.TestCase):
    """`git commit -a` with no message opens an editor and must be blocked."""

    def test_bare_commit_all(self):
        self.assertTrue(_blocks("git commit -a"))

    def test_commit_all_long_form(self):
        self.assertTrue(_blocks("git commit --all"))

    def test_commit_all_with_no_verify(self):
        self.assertTrue(_blocks("git commit -a --no-verify"))

    # --- Regressions: a hyphenated word containing "m" cancelled the check

    def test_template_does_not_supply_a_message(self):
        """--template seeds the editor; it does not avoid opening one."""
        self.assertTrue(_blocks("git commit -a --template=/tmp/t.txt"),
                        "'--template' contains an 'm' but supplies no message")

    def test_allow_empty_message_does_not_supply_a_message(self):
        self.assertTrue(_blocks("git commit -a --allow-empty-message"),
                        "'--allow-empty-message' contains an 'm' but supplies none")

    def test_pathspec_containing_dash_m_does_not_cancel_the_check(self):
        self.assertTrue(_blocks("git commit -a -- src/-m-file.txt"))


class TestAllowedCommits(unittest.TestCase):
    """Commands that supply a message must not be blocked."""

    def test_message_flag(self):
        self.assertFalse(_blocks("git commit -m 'x'"))
        self.assertFalse(_blocks("git commit -a -m 'x'"))
        self.assertFalse(_blocks("git commit -am 'x'"))

    def test_amend_reuses_the_previous_message(self):
        self.assertFalse(_blocks("git commit -a --amend"))

    def test_sign_option_does_not_swallow_the_message_flag(self):
        self.assertFalse(_blocks("git commit -aS -m 'x'"),
                         "-S takes an attached value only")

    # --- Regressions: a path containing "-a" read as the stage-everything flag

    def test_message_file_under_a_path_containing_dash_a(self):
        """-F supplies the message; '-agent' in the path is not an option."""
        self.assertFalse(_blocks("git commit -F /tmp/claude-agent/msg.txt"),
                         "a path segment starting '-a' is not the -a flag")

    def test_long_message_file_under_a_path_containing_dash_a(self):
        self.assertFalse(_blocks("git commit --file=/tmp/branch-a/msg.txt"))

    def test_message_file_alone_never_stages_everything(self):
        self.assertFalse(_blocks("git commit -F /tmp/release-alpha/msg.txt"))

    def test_dash_a_inside_a_quoted_message(self):
        self.assertFalse(_blocks("git commit -m 'refactor -a handling'"))


if __name__ == "__main__":
    unittest.main()
