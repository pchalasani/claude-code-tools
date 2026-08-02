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
import subprocess
import sys
import tempfile
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_add_block_hook import _commit_option_tokens, check_git_add_command


def _blocks(command: str) -> bool:
    """True when the hook blocks outright (as opposed to allowing or asking)."""
    return check_git_add_command(command)[0] is True


class TestCommitOptionTokens(unittest.TestCase):
    """Tests for _commit_option_tokens() parsing."""

    def test_short_cluster_is_split(self) -> None:
        self.assertEqual(_commit_option_tokens("git commit -am 'x'"),
                         ['-a', '-m'])

    def test_value_of_a_value_option_is_not_an_option(self) -> None:
        self.assertEqual(_commit_option_tokens("git commit -m -a"), ['-m'])

    def test_long_option_with_attached_value(self) -> None:
        self.assertEqual(_commit_option_tokens("git commit --message=hi"),
                         ['--message'])

    def test_long_option_with_separate_value(self) -> None:
        self.assertEqual(_commit_option_tokens("git commit --file /tmp/m.txt"),
                         ['--file'])

    def test_unambiguous_long_option_abbreviations_are_canonical(self) -> None:
        self.assertEqual(_commit_option_tokens("git commit --edi"), ['--edit'])
        self.assertEqual(
            _commit_option_tokens("git commit --mess=x"), ['--message'])

    def test_pathspec_separator_ends_options(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -a -- src/-m-file.txt"), ['-a'])

    def test_path_argument_is_not_an_option(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -F /tmp/claude-agent/msg.txt"),
            ['-F'])

    def test_attached_short_value_ends_cluster(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -atmessage"), ['-a', '-t'])

    def test_attached_signing_key_ends_cluster(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -aSalpha"), ['-a', '-S'])

    def test_bare_sign_option_does_not_consume_next_option(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -aS -m 'x'"),
            ['-a', '-S', '-m'])

    def test_required_short_value_consumes_next_token(self) -> None:
        self.assertEqual(
            _commit_option_tokens("git commit -at /tmp/template -m 'x'"),
            ['-a', '-t', '-m'])


class TestBlockedCommitAll(unittest.TestCase):
    """`git commit -a` with no message opens an editor and must be blocked."""

    def test_bare_commit_all(self) -> None:
        self.assertTrue(_blocks("git commit -a"))

    def test_commit_all_long_form(self) -> None:
        self.assertTrue(_blocks("git commit --all"))

    def test_commit_all_with_no_verify(self) -> None:
        self.assertTrue(_blocks("git commit -a --no-verify"))

    # --- Regressions: a hyphenated word containing "m" cancelled the check

    def test_template_does_not_supply_a_message(self) -> None:
        """--template seeds the editor; it does not avoid opening one."""
        self.assertTrue(_blocks("git commit -a --template=/tmp/t.txt"),
                        "'--template' contains an 'm' but supplies no message")

    def test_allow_empty_message_does_not_supply_a_message(self) -> None:
        self.assertTrue(_blocks("git commit -a --allow-empty-message"),
                        "'--allow-empty-message' contains an 'm' but supplies none")

    def test_pathspec_containing_dash_m_does_not_cancel_the_check(self) -> None:
        self.assertTrue(_blocks("git commit -a -- src/-m-file.txt"))

    def test_reedit_message_short_form_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -ac HEAD"))

    def test_reedit_message_long_form_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -a --reedit-message HEAD"))

    def test_bare_amend_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -a --amend"))

    def test_squash_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -a --squash HEAD"))

    def test_fixup_amend_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -a --fixup=amend:HEAD"))
        self.assertTrue(_blocks("git commit -a --fixup amend:HEAD"))

    def test_fixup_reword_opens_editor(self) -> None:
        self.assertTrue(_blocks("git commit -a --fixup=reword:HEAD"))
        self.assertTrue(_blocks("git commit -a --fixup reword:HEAD"))

    def test_edit_forces_editor_with_message(self) -> None:
        self.assertTrue(_blocks("git commit -am 'x' --edit"))

    def test_abbreviated_edit_and_negated_message_open_editor(self) -> None:
        self.assertTrue(_blocks("git commit -am x --edi"))
        self.assertTrue(_blocks("git commit -am x --no-mess"))

    def test_negated_message_sources_open_editor(self) -> None:
        commands = (
            "git commit -a -m x --no-message",
            "git commit -a -F msg.txt --no-file",
            "git commit -a -C HEAD --no-reuse-message",
            "git commit -a --fixup=HEAD --no-fixup",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(_blocks(command))

    def test_commit_all_after_working_directory_option(self) -> None:
        self.assertTrue(_blocks("git -C repo commit -a"))
        self.assertTrue(_blocks("git -Crepo commit -a"))

    def test_commit_all_after_config_option(self) -> None:
        self.assertTrue(_blocks("git -c core.editor=vim commit -a"))
        self.assertTrue(_blocks("git -ccore.editor=vim commit -a"))

    def test_quoted_operator_in_working_directory(self) -> None:
        self.assertTrue(_blocks("git -C 'repo|archive' commit -a"))

    def test_commit_all_after_attr_source(self) -> None:
        self.assertTrue(_blocks("git --attr-source HEAD commit -a"))
        self.assertTrue(_blocks("git --attr-source=HEAD commit -a"))

    def test_commit_all_in_compound_command(self) -> None:
        self.assertTrue(_blocks("echo ready && git commit -a"))

    def test_commit_all_with_absolute_git_executable(self) -> None:
        self.assertTrue(_blocks("/usr/bin/git commit -a"))

    def test_commit_all_after_multiple_global_options(self) -> None:
        self.assertTrue(
            _blocks("/usr/bin/git -P -C repo -c x.y=z commit --all")
        )

    def test_commit_all_after_attached_exec_path(self) -> None:
        self.assertTrue(_blocks("git --exec-path=/usr/lib/git-core commit -a"))

    def test_commit_all_after_env_wrapper(self) -> None:
        self.assertTrue(_blocks("GIT_EDITOR=vim git commit -a"))
        self.assertTrue(_blocks("env -i GIT_EDITOR=vim git commit -a"))
        self.assertTrue(_blocks("env -P /usr/bin git commit -a"))
        self.assertTrue(_blocks("env -iP /usr/bin git commit -a"))
        self.assertTrue(_blocks("env -S 'git commit -a'"))
        self.assertTrue(_blocks("env -iS 'git commit -a'"))

    def test_commit_all_after_attached_env_unset(self) -> None:
        self.assertTrue(_blocks("env -uSHELL git commit -a"))

    def test_commit_all_after_env_separator_assignment(self) -> None:
        self.assertTrue(_blocks("env -- GIT_EDITOR=vim git commit -a"))


class TestBlockedGitAdd(unittest.TestCase):
    """Dangerous add forms remain blocked after supported Git prefixes."""

    def test_add_all_after_working_directory_option(self) -> None:
        self.assertTrue(_blocks("git -C . add -A"))
        self.assertTrue(_blocks("git -C. add --all"))

    def test_add_all_after_other_global_options(self) -> None:
        self.assertTrue(_blocks("git -P -c core.pager=cat add -a"))
        self.assertTrue(_blocks("/usr/bin/git --attr-source HEAD add ."))

    def test_add_all_long_option_abbreviations(self) -> None:
        self.assertTrue(_blocks("git add --a"))
        self.assertTrue(_blocks("git add --al"))
        self.assertTrue(_blocks("git add --all"))

    def test_add_all_after_assignment_and_env_prefixes(self) -> None:
        self.assertTrue(_blocks("GIT_OPTIONAL_LOCKS=0 git -C . add -A"))
        self.assertTrue(_blocks("env -i git -C . add --all"))

    def test_add_all_after_env_split_string(self) -> None:
        self.assertTrue(_blocks("env -S 'git add .'"))
        self.assertTrue(_blocks("env --split-string='git add -A'"))
        self.assertTrue(_blocks("env -iS 'git add --all'"))
        self.assertTrue(_blocks("env -S'git add .'"))

    def test_env_split_preserves_prior_chdir(self) -> None:
        self.assertTrue(_blocks("env -C /tmp -S 'git add .'"))

    def test_attached_env_unset_does_not_hide_add(self) -> None:
        self.assertTrue(_blocks("env -uSHELL git add -A"))
        self.assertTrue(_blocks("env -uSSH_AUTH_SOCK git add ."))

    def test_env_separator_assignments_do_not_hide_add(self) -> None:
        self.assertTrue(_blocks("env -- X=1 git add -A"))
        decision, _ = check_git_add_command(
            "env -- GIT_DIR=/other/.git git add tracked.txt"
        )
        self.assertEqual(decision, "ask")

    def test_env_split_checks_modified_file_in_chdir(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(
                ["git", "init"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            path = os.path.join(repo, "tracked.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("staged\n")
            subprocess.run(
                ["git", "add", "tracked.txt"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("modified\n")

            decision, _ = check_git_add_command(
                f"env --chdir={repo} -S 'git add tracked.txt'"
            )
            self.assertEqual(decision, "ask")

    def test_attached_env_chdir_checks_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(
                ["git", "init"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            path = os.path.join(repo, "tracked.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("staged\n")
            subprocess.run(
                ["git", "add", "tracked.txt"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("modified\n")

            commands = (
                f"env -C{repo} git add tracked.txt",
                f"env -iC {repo} git add tracked.txt",
            )
            for command in commands:
                with self.subTest(command=command):
                    decision, _ = check_git_add_command(command)
                    self.assertEqual(decision, "ask")

    def test_dynamic_chdir_requires_approval(self) -> None:
        decision, _ = check_git_add_command(
            'git -C "$REPO" add tracked.txt'
        )
        self.assertEqual(decision, "ask")

    def test_repository_routing_requires_approval(self) -> None:
        commands = (
            "GIT_DIR=/other/.git GIT_WORK_TREE=/other git add tracked.txt",
            "env GIT_DIR=/other/.git git add tracked.txt",
            "git --git-dir=/other/.git --work-tree=/other add tracked.txt",
        )
        for command in commands:
            with self.subTest(command=command):
                decision, _ = check_git_add_command(command)
                self.assertEqual(decision, "ask")

    def test_uninspectable_repository_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision, _ = check_git_add_command(
                f"git -C {directory} add tracked.txt"
            )
        self.assertEqual(decision, "ask")

    def test_pathspec_file_requires_approval(self) -> None:
        for command in (
            "git add --pathspec-from-file=paths.txt",
            "git add --pathspec-from-file paths.txt",
            "git add --pathspec-file-nul --pathspec-from-file=paths.txt",
        ):
            with self.subTest(command=command):
                decision, _ = check_git_add_command(command)
                self.assertEqual(decision, "ask")

    def test_quoted_modified_filename_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(
                ["git", "init"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            path = os.path.join(repo, "tracked file.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("staged\n")
            subprocess.run(
                ["git", "add", "tracked file.txt"], cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("modified\n")

            decision, _ = check_git_add_command(
                f"git -C {repo} add 'tracked file.txt'"
            )
            self.assertEqual(decision, "ask")


class TestAllowedCommits(unittest.TestCase):
    """Commands that supply a message must not be blocked."""

    def test_message_flag(self) -> None:
        self.assertFalse(_blocks("git commit -m 'x'"))
        self.assertFalse(_blocks("git commit -a -m 'x'"))
        self.assertFalse(_blocks("git commit -am 'x'"))

    def test_abbreviated_message_sources(self) -> None:
        self.assertFalse(_blocks("git commit -a --mess=x"))
        self.assertFalse(_blocks("git commit -a --reuse-mess=HEAD"))

    def test_message_source_after_negation_avoids_editor(self) -> None:
        self.assertFalse(_blocks("git commit -a --no-message -m x"))
        self.assertFalse(_blocks("git commit -a --no-file -F msg.txt"))
        self.assertFalse(
            _blocks("git commit -a --no-reuse-message -C HEAD")
        )
        self.assertFalse(_blocks("git commit -a --no-fixup --fixup=HEAD"))

    def test_amend_with_no_edit(self) -> None:
        self.assertFalse(_blocks("git commit -a --amend --no-edit"))

    def test_amend_with_message(self) -> None:
        self.assertFalse(_blocks("git commit -a --amend -m 'x'"))

    def test_reuse_message_does_not_open_editor(self) -> None:
        self.assertFalse(_blocks("git commit -a -C HEAD"))

    def test_plain_fixup_does_not_open_editor(self) -> None:
        self.assertFalse(_blocks("git commit -a --fixup=HEAD"))
        self.assertFalse(_blocks("git commit -a --fixup HEAD"))
        self.assertFalse(_blocks("git commit -a --fixup=amend"))
        self.assertFalse(_blocks("git commit -a --fixup reword"))

    def test_sign_option_does_not_swallow_the_message_flag(self) -> None:
        self.assertFalse(_blocks("git commit -aS -m 'x'"),
                         "-S takes an attached value only")

    # --- Regressions: a path containing "-a" read as the stage-everything flag

    def test_message_file_under_a_path_containing_dash_a(self) -> None:
        """-F supplies the message; '-agent' in the path is not an option."""
        self.assertFalse(_blocks("git commit -F /tmp/claude-agent/msg.txt"),
                         "a path segment starting '-a' is not the -a flag")

    def test_long_message_file_under_a_path_containing_dash_a(self) -> None:
        self.assertFalse(_blocks("git commit --file=/tmp/branch-a/msg.txt"))

    def test_message_file_alone_never_stages_everything(self) -> None:
        self.assertFalse(_blocks("git commit -F /tmp/release-alpha/msg.txt"))

    def test_dash_a_inside_a_quoted_message(self) -> None:
        self.assertFalse(_blocks("git commit -m 'refactor -a handling'"))

    def test_prefixed_commit_with_message(self) -> None:
        self.assertFalse(_blocks("git -C repo commit -am 'x'"))

    def test_prefixed_non_commit_command(self) -> None:
        self.assertFalse(_blocks("git -C repo status -a"))

    def test_assignment_prefixed_message_and_non_commit_are_allowed(self) -> None:
        self.assertFalse(_blocks("GIT_EDITOR=vim git commit -am 'x'"))
        self.assertFalse(_blocks("GIT_EDITOR=vim git status -a"))

        self.assertFalse(_blocks("env --ignore-environment git commit -am 'x'"))
        self.assertFalse(_blocks("env -u GIT_EDITOR git status -a"))

    def test_invalid_global_option_does_not_reveal_commit(self) -> None:
        self.assertFalse(_blocks("git --unknown commit -a"))

    def test_terminal_global_options_do_not_run_commit(self) -> None:
        self.assertFalse(_blocks("git --exec-path foo commit -a"))
        self.assertFalse(_blocks("git --list-cmds=main commit -a"))


if __name__ == "__main__":
    unittest.main()
