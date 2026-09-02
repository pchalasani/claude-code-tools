#!/usr/bin/env python3
"""
Unit tests for git_commit_block_hook.py

Tests cover:
    - `git -C <dir> commit` and other global-option forms reaching the gate
    - Compound commands
    - The session-scoped allow flag, the deny flag, and CCTOOLS_ALLOW_GIT
    - Commands that merely mention "git commit" staying allowed
"""
import os
import json
import subprocess
import sys
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from git_commit_block_hook import (
    ALLOW_ENV_VAR,
    check_git_commit_command,
    git_subcommand,
)

_SAVED_ENV: str | None = None


def setUpModule() -> None:
    """Run the gate tests with the ambient allow-everywhere setting cleared."""
    global _SAVED_ENV
    _SAVED_ENV = os.environ.pop(ALLOW_ENV_VAR, None)


def tearDownModule() -> None:
    if _SAVED_ENV is None:
        os.environ.pop(ALLOW_ENV_VAR, None)
    else:
        os.environ[ALLOW_ENV_VAR] = _SAVED_ENV


class TestGitSubcommand(unittest.TestCase):
    """Tests for git_subcommand() resolution past git's global options."""

    def test_plain(self) -> None:
        self.assertEqual(git_subcommand("git commit -m 'x'"), "commit")

    def test_dash_c_directory(self) -> None:
        self.assertEqual(
            git_subcommand("git -C /tmp/repo commit -m 'x'"), "commit"
        )

    def test_dash_c_config(self) -> None:
        self.assertEqual(
            git_subcommand("git -c user.name=Bot commit -m 'x'"), "commit")

    def test_combined_global_options(self) -> None:
        self.assertEqual(
            git_subcommand("git -C /tmp/repo -c core.pager=cat commit -m 'x'"),
            "commit")

    def test_attr_source_forms(self) -> None:
        self.assertEqual(
            git_subcommand("git --attr-source HEAD commit -m 'x'"), "commit"
        )
        self.assertEqual(
            git_subcommand("git --attr-source=HEAD commit -m 'x'"), "commit"
        )

    def test_no_verify_is_not_a_global_option(self) -> None:
        self.assertEqual(
            git_subcommand("git commit --no-verify -m 'x'"), "commit"
        )

    def test_other_subcommands(self) -> None:
        self.assertEqual(git_subcommand("git status"), "status")
        self.assertEqual(git_subcommand("git -C /tmp/repo status"), "status")

    def test_terminal_and_unknown_global_options(self) -> None:
        self.assertIsNone(git_subcommand("git --help commit"))
        self.assertIsNone(git_subcommand("git --version commit"))
        self.assertIsNone(git_subcommand("git --future-option commit"))

    def test_not_git(self) -> None:
        self.assertIsNone(git_subcommand("echo git commit"))
        self.assertIsNone(git_subcommand(""))

    def test_absolute_path_to_git_binary(self) -> None:
        self.assertEqual(git_subcommand("/usr/bin/git commit -m 'x'"), "commit")

class TestAsksForApproval(unittest.TestCase):
    """Commands that must reach the approval prompt."""

    def assertAsks(self, command: str, message: str | None = None) -> None:
        decision, reason = check_git_commit_command(command)
        self.assertEqual(decision, "ask", message or command)
        self.assertIsNotNone(reason)

    def test_plain_commit(self) -> None:
        self.assertAsks("git commit -m 'x'")

    def test_assignment_prefixes(self) -> None:
        self.assertAsks("MODE=prod git commit -m x")

    def test_env_prefixes(self) -> None:
        self.assertAsks("env MODE=prod git commit -m x")
        self.assertAsks("env -i --unset HOME MODE=prod git commit -m x")

    def test_env_split_string_prefixes(self) -> None:
        self.assertAsks("env -S 'git commit -m x'")
        self.assertAsks("env --split-string='git commit -m x'")
        self.assertAsks("env -iS 'git commit -m x'")
        self.assertAsks("env -S'git commit -m x'")
        self.assertAsks("env -S '-i git commit -m x'")
        self.assertAsks("env -S '-C /tmp git commit -m x'")
        self.assertAsks("env -S '-u HOME git commit -m x'")
        self.assertAsks("env -S '-- git commit -m x'")

    def test_attached_env_values_containing_s(self) -> None:
        self.assertAsks("env -uSHELL git commit -m x")
        self.assertAsks("env -uSSH_AUTH_SOCK git commit -m x")
        self.assertAsks("env -CStore git commit -m x")

    def test_commit_all(self) -> None:
        self.assertAsks("git commit -am 'x'")

    def test_compound_command(self) -> None:
        self.assertAsks("cd /tmp && git commit -m 'x'")

    def test_newline_separators(self) -> None:
        self.assertAsks("echo first\ngit commit -m 'x'")
        self.assertAsks("echo first\r\ngit commit -m 'x'")

    def test_command_substitutions(self) -> None:
        self.assertAsks("echo $(git commit -m 'x')")
        self.assertAsks('echo "result: $(git commit -m \'x\')"')
        self.assertAsks("echo $(printf '%s' $(git commit -m 'x'))")
        self.assertAsks("echo `git commit -m 'x'`")
        self.assertAsks("echo `printf '%s' $(git commit -m 'x')`")
        self.assertAsks(r"echo `echo \`git commit -m x\``")

    def test_unquoted_heredoc_substitutions(self) -> None:
        self.assertAsks("cat <<EOF\n$(git commit -m x)\nEOF")
        self.assertAsks("cat <<EOF\n`git commit -m x`\nEOF")
        self.assertAsks("cat <<-EOF\n\t$(git commit -m x)\n\tEOF")

    def test_subshells_and_process_substitutions(self) -> None:
        self.assertAsks("(git commit -m 'x')")
        self.assertAsks("diff <(git commit -m 'x') expected")
        self.assertAsks("cat >(git commit -m 'x')")

    def test_substitution_recursion_is_bounded(self) -> None:
        deep_echo = "echo " + "$(echo " * 14 + "ok" + ")" * 14
        deep_commit = "$(" * 14 + "git commit -m x" + ")" * 14
        decision, _ = check_git_commit_command(deep_echo)
        self.assertEqual(decision, "allow")
        self.assertAsks(deep_commit)

    def test_commit_tree(self) -> None:
        self.assertAsks("git commit-tree abc123 -m 'x'")

    # --- Regressions: git's global options put the subcommand past argv[1]

    def test_dash_c_directory(self) -> None:
        """`git -C <dir> commit` is a commit and must still be gated."""
        self.assertAsks(
            "git -C /tmp/some-repo commit -m 'x'",
            "git -C <dir> commit bypassed the approval gate")

    def test_dash_c_directory_relative(self) -> None:
        self.assertAsks("git -C ../other-repo commit -m 'x'")

    def test_dash_c_config_override(self) -> None:
        self.assertAsks("git -c user.email=bot@example.com commit -m 'x'")

    def test_attr_source_forms(self) -> None:
        self.assertAsks("git --attr-source HEAD commit -m 'x'")
        self.assertAsks("git --attr-source=HEAD commit -m 'x'")

    def test_git_dir_and_work_tree(self) -> None:
        self.assertAsks(
            "git --git-dir /tmp/r/.git --work-tree /tmp/r commit -m 'x'")

    def test_dash_c_inside_a_compound_command(self) -> None:
        self.assertAsks("echo hi && git -C /tmp/some-repo commit -m 'x'")

    def test_quoted_config_operator_values(self) -> None:
        self.assertAsks(
            "git -c 'alias.audit=!echo one && echo two' commit -m 'x'"
        )
        self.assertAsks(
            'git -c "alias.audit=!echo one | echo two" commit -m \'x\''
        )

    def test_quoted_dash_c_path_with_operators(self) -> None:
        self.assertAsks("git -C '/tmp/repo;archive' commit -m 'x'")
        self.assertAsks("git -C '/tmp/repo && archive' commit -m 'x'")

    def test_absolute_git_path_with_dash_c(self) -> None:
        self.assertAsks("/usr/bin/git -C /tmp/some-repo commit -m 'x'")


class TestAllowed(unittest.TestCase):
    """Commands that must not trigger the prompt."""

    def assertAllows(self, command: str) -> None:
        decision, _ = check_git_commit_command(command)
        self.assertEqual(decision, "allow", command)

    def test_other_git_commands(self) -> None:
        self.assertAllows("git status")
        self.assertAllows("git log --oneline")
        self.assertAllows("git -C /tmp/some-repo status")

    def test_prefixed_non_commit_commands(self) -> None:
        self.assertAllows("MODE=prod git status")
        self.assertAllows("env -i MODE=prod git log --oneline")

    def test_terminal_global_options(self) -> None:
        self.assertAllows("git --help commit")
        self.assertAllows("git --version commit")

    def test_unknown_global_option(self) -> None:
        self.assertAllows("git --future-option commit")

    def test_commit_mentioned_but_not_run(self) -> None:
        self.assertAllows("echo 'remember to git commit'")
        self.assertAllows("grep -r 'git commit' docs/")

    def test_literal_or_escaped_substitutions(self) -> None:
        self.assertAllows("echo '$(git commit -m x)'")
        self.assertAllows(r"echo \$(git commit -m x)")
        self.assertAllows("echo '`git commit -m x`'")
        self.assertAllows(r"echo \`git commit -m x\`")

    def test_git_commit_in_heredoc_body(self) -> None:
        self.assertAllows("cat <<EOF\ngit commit -m x\nEOF")
        self.assertAllows("cat <<END-OF\ngit commit -m x\nEND-OF")
        self.assertAllows("cat <<'EOF'\ngit commit -m x\nEOF")

    def test_quoted_heredoc_substitutions_are_literal(self) -> None:
        self.assertAllows("cat <<'EOF'\n$(git commit -m x)\nEOF")
        self.assertAllows('cat <<"EOF"\n`git commit -m x`\nEOF')
        self.assertAllows("cat <<\\EOF\n$(git commit -m x)\nEOF")
        self.assertAllows("cat <<-'EOF'\n\t$(git commit -m x)\n\tEOF")

    def test_escaped_heredoc_substitutions_are_literal(self) -> None:
        self.assertAllows("cat <<EOF\n\\$(git commit -m x)\nEOF")
        self.assertAllows("cat <<EOF\n\\`git commit -m x\\`\nEOF")

    def test_empty(self) -> None:
        self.assertAllows("")


class TestSessionAllowFlag(unittest.TestCase):
    """The session-scoped flag file bypasses the prompt."""

    def test_flag_allows_commit(self) -> None:
        flag_dir = "/tmp/claude"
        os.makedirs(flag_dir, exist_ok=True)
        session_id = "test-session-" + str(os.getpid())
        flag = os.path.join(flag_dir, f"allow-git-commit.{session_id}")
        with open(flag, "w", encoding="utf-8") as handle:
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

    def test_without_flag_still_asks(self) -> None:
        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id="no-such-session-" + str(os.getpid()))
        self.assertEqual(decision, "ask")

    def test_standalone_entrypoint_forwards_session_id(self) -> None:
        session_id = "standalone-commit-" + str(os.getpid())
        flag = f"/tmp/claude/allow-git-commit.{session_id}"
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        with open(flag, "w", encoding="utf-8") as handle:
            handle.write(session_id)
        self.addCleanup(lambda: os.path.exists(flag) and os.remove(flag))

        payload = {
            "tool_name": "Bash",
            "session_id": session_id,
            "tool_input": {"command": "git commit -m x"},
        }
        script = os.path.join(os.path.dirname(__file__), "git_commit_block_hook.py")
        result = subprocess.run(
            [sys.executable, script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(json.loads(result.stdout), {"decision": "approve"})


class TestAllowEnvVar(unittest.TestCase):
    """CCTOOLS_ALLOW_GIT allows commits without a session-scoped flag file."""

    def setUp(self) -> None:
        self.session_id = "env-session-" + str(os.getpid())
        self.deny = f"/tmp/claude/deny-git-commit.{self.session_id}"
        os.makedirs("/tmp/claude", exist_ok=True)
        self.addCleanup(
            lambda: os.path.exists(self.deny) and os.remove(self.deny))
        self.addCleanup(os.environ.pop, ALLOW_ENV_VAR, None)

    def test_env_var_allows_commit_without_any_flag(self) -> None:
        os.environ[ALLOW_ENV_VAR] = "1"
        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id=self.session_id)
        self.assertEqual(decision, "allow")

    def test_env_var_allows_commit_with_no_session_id(self) -> None:
        os.environ[ALLOW_ENV_VAR] = "true"
        decision, _ = check_git_commit_command("git commit -m 'x'")
        self.assertEqual(decision, "allow")

    def test_falsy_env_var_still_asks(self) -> None:
        os.environ[ALLOW_ENV_VAR] = "0"
        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id=self.session_id)
        self.assertEqual(decision, "ask")

    def test_session_deny_flag_overrides_env_var(self) -> None:
        os.environ[ALLOW_ENV_VAR] = "1"
        with open(self.deny, "w", encoding="utf-8") as handle:
            handle.write(self.session_id)
        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id=self.session_id)
        self.assertEqual(decision, "ask")

    def test_deny_flag_overrides_allow_flag(self) -> None:
        allow = f"/tmp/claude/allow-git-commit.{self.session_id}"
        for path in (allow, self.deny):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.session_id)
        self.addCleanup(lambda: os.path.exists(allow) and os.remove(allow))

        decision, _ = check_git_commit_command(
            "git commit -m 'x'", session_id=self.session_id)
        self.assertEqual(decision, "ask")


if __name__ == "__main__":
    unittest.main()
