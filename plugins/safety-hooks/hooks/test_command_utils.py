#!/usr/bin/env python3
"""
Unit tests for command_utils.py

Tests cover:
    - Shell operator splitting (&&, ||, ;, |, &)
    - Subshell extraction ($() and backticks)
    - Combined extraction via extract_all_commands()
    - Edge cases and regression tests for security bypasses
"""
import unittest
import sys
import os

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from command_utils import (
    extract_subcommands,
    extract_subshell_commands,
    extract_all_commands,
    expand_command_aliases,
    strip_heredoc_bodies,
)


class TestExtractSubcommands(unittest.TestCase):
    """Tests for extract_subcommands() shell operator splitting."""

    def test_empty_command(self):
        """Empty string returns empty list."""
        self.assertEqual(extract_subcommands(""), [])
        self.assertEqual(extract_subcommands("   "), [])

    def test_single_command(self):
        """Single command without operators."""
        self.assertEqual(extract_subcommands("ls -la"), ["ls -la"])
        self.assertEqual(extract_subcommands("git status"), ["git status"])

    def test_and_operator(self):
        """Split on && (AND) operator."""
        result = extract_subcommands("cd /tmp && ls")
        self.assertEqual(result, ["cd /tmp", "ls"])

    def test_or_operator(self):
        """Split on || (OR) operator."""
        result = extract_subcommands("test -f file || echo missing")
        self.assertEqual(result, ["test -f file", "echo missing"])

    def test_semicolon_operator(self):
        """Split on ; (sequential) operator."""
        result = extract_subcommands("echo hello; echo world")
        self.assertEqual(result, ["echo hello", "echo world"])

    def test_pipe_operator(self):
        """Split on | (pipe) operator - security regression test."""
        result = extract_subcommands("echo ok | rm foo")
        self.assertEqual(result, ["echo ok", "rm foo"])

        result = extract_subcommands("cat file | grep pattern | wc -l")
        self.assertEqual(result, ["cat file", "grep pattern", "wc -l"])

    def test_background_operator(self):
        """Split on & (background) operator - security regression test."""
        result = extract_subcommands("sleep 1 & rm bar")
        self.assertEqual(result, ["sleep 1", "rm bar"])

    def test_mixed_operators(self):
        """Multiple different operators in one command."""
        result = extract_subcommands("cmd1 && cmd2 | cmd3; cmd4 || cmd5 & cmd6")
        self.assertEqual(result, ["cmd1", "cmd2", "cmd3", "cmd4", "cmd5", "cmd6"])

    def test_preserves_command_arguments(self):
        """Arguments within commands are preserved."""
        result = extract_subcommands("git add . && git commit -m 'msg with spaces'")
        self.assertEqual(result, ["git add .", "git commit -m 'msg with spaces'"])

    def test_double_operators_not_split_incorrectly(self):
        """&& should not become two & splits."""
        result = extract_subcommands("cmd1 && cmd2")
        # Should be 2 commands, not 3 (which would happen if && was split as & twice)
        self.assertEqual(len(result), 2)
        self.assertEqual(result, ["cmd1", "cmd2"])


class TestExtractSubshellCommands(unittest.TestCase):
    """Tests for extract_subshell_commands() subshell detection."""

    def test_empty_command(self):
        """Empty string returns empty list."""
        self.assertEqual(extract_subshell_commands(""), [])

    def test_no_subshells(self):
        """Command without subshells returns empty list."""
        self.assertEqual(extract_subshell_commands("ls -la"), [])
        self.assertEqual(extract_subshell_commands("echo hello"), [])

    def test_dollar_paren_subshell(self):
        """Extract command from $() syntax."""
        result = extract_subshell_commands("echo $(whoami)")
        self.assertEqual(result, ["whoami"])

    def test_backtick_subshell(self):
        """Extract command from backtick syntax."""
        result = extract_subshell_commands("echo `whoami`")
        self.assertEqual(result, ["whoami"])

    def test_multiple_subshells(self):
        """Multiple subshells in one command."""
        result = extract_subshell_commands("$(cmd1) foo $(cmd2)")
        self.assertEqual(result, ["cmd1", "cmd2"])

    def test_mixed_subshell_syntax(self):
        """Both $() and backticks in same command."""
        result = extract_subshell_commands("$(cmd1) and `cmd2`")
        self.assertIn("cmd1", result)
        self.assertIn("cmd2", result)

    def test_subshell_with_arguments(self):
        """Subshell containing command with arguments."""
        result = extract_subshell_commands("echo $(cat /etc/passwd)")
        self.assertEqual(result, ["cat /etc/passwd"])

    def test_security_bypass_rm_in_subshell(self):
        """Detect rm hidden in subshell - security test."""
        result = extract_subshell_commands("echo $(rm -rf /)")
        self.assertEqual(result, ["rm -rf /"])

        result = extract_subshell_commands("cat `rm foo`")
        self.assertEqual(result, ["rm foo"])

    def test_nested_subshell_extraction(self):
        """Nested $() subshells are properly extracted - P1 security fix."""
        # This was a bypass: $(echo $(rm foo)) would only extract "echo $(rm foo"
        # truncated at first ), missing the inner rm command
        result = extract_subshell_commands("echo $(echo $(rm foo))")
        self.assertEqual(result, ["echo $(rm foo)"])

    def test_deeply_nested_subshells(self):
        """Multiple levels of nesting are handled."""
        result = extract_subshell_commands("$(a $(b $(c)))")
        self.assertEqual(result, ["a $(b $(c))"])

    def test_multiple_nested_subshells(self):
        """Multiple nested subshells at same level."""
        result = extract_subshell_commands("$(cmd1 $(inner1)) $(cmd2 $(inner2))")
        self.assertIn("cmd1 $(inner1)", result)
        self.assertIn("cmd2 $(inner2)", result)


class TestExtractAllCommands(unittest.TestCase):
    """Tests for extract_all_commands() comprehensive extraction."""

    def test_empty_command(self):
        """Empty string returns empty list."""
        self.assertEqual(extract_all_commands(""), [])

    def test_simple_command(self):
        """Simple command without operators or subshells."""
        result = extract_all_commands("ls -la")
        self.assertEqual(result, ["ls -la"])

    def test_chained_commands(self):
        """Commands chained with operators."""
        result = extract_all_commands("cmd1 && cmd2 | cmd3")
        self.assertIn("cmd1", result)
        self.assertIn("cmd2", result)
        self.assertIn("cmd3", result)

    def test_subshell_commands(self):
        """Commands inside subshells are extracted."""
        result = extract_all_commands("echo $(rm foo)")
        self.assertIn("echo $(rm foo)", result)  # Top-level command
        self.assertIn("rm foo", result)  # Subshell command

    def test_combined_operators_and_subshells(self):
        """Both operators and subshells in same command."""
        result = extract_all_commands("echo $(rm foo) && ls")
        self.assertIn("echo $(rm foo)", result)
        self.assertIn("ls", result)
        self.assertIn("rm foo", result)

    def test_nested_operators_in_subshell(self):
        """Operators inside subshell are also split."""
        result = extract_all_commands("$(cmd1 && cmd2)")
        self.assertIn("cmd1", result)
        self.assertIn("cmd2", result)

    def test_security_pipe_bypass(self):
        """Detect rm after pipe - security regression test."""
        result = extract_all_commands("echo ok | rm -rf /tmp/x")
        self.assertIn("rm -rf /tmp/x", result)

    def test_security_background_bypass(self):
        """Detect rm after background operator - security regression test."""
        result = extract_all_commands("sleep 1 & rm foo")
        self.assertIn("rm foo", result)

    def test_security_subshell_bypass(self):
        """Detect rm hidden in subshell - security regression test."""
        result = extract_all_commands("echo $(rm secret)")
        self.assertIn("rm secret", result)

    def test_quoted_heredoc_backticks_are_literal(self):
        """A quoted delimiter means the shell expands nothing in the body."""
        command = "cat > notes.md <<'MD'\nThe `rm -rf x` guard.\nMD"
        result = extract_all_commands(command)
        self.assertNotIn("rm -rf x", result)
        self.assertIn("cat > notes.md <<'MD'", result)

    def test_double_quoted_heredoc_backticks_are_literal(self):
        """A double-quoted delimiter is just as literal as a single-quoted one."""
        command = 'cat > notes.md <<"MD"\nThe `rm -rf x` guard.\nMD'
        self.assertNotIn("rm -rf x", extract_all_commands(command))

    def test_backslash_quoted_heredoc_backticks_are_literal(self):
        """A backslash-escaped delimiter also disables expansion."""
        command = "cat > notes.md <<\\MD\nThe `rm -rf x` guard.\nMD"
        self.assertNotIn("rm -rf x", extract_all_commands(command))

    def test_unquoted_heredoc_substitutions_are_extracted(self):
        """An unquoted delimiter still expands $() and backticks - regression."""
        backticks = "cat > notes.md <<MD\nThe `rm -rf x` guard.\nMD"
        self.assertIn("rm -rf x", extract_all_commands(backticks))

        dollar = "cat > notes.md <<MD\nThe $(rm -rf y) guard.\nMD"
        self.assertIn("rm -rf y", extract_all_commands(dollar))

    def test_unquoted_heredoc_body_text_is_not_a_command(self):
        """Body lines are data even when the delimiter is unquoted."""
        command = "cat > notes.md <<MD\nrm -rf /tmp/x\nMD"
        self.assertNotIn("rm -rf /tmp/x", extract_all_commands(command))

    def test_command_chained_after_heredoc_is_extracted(self):
        """Only the body is blanked, never the line introducing it."""
        command = "cat <<'MD' && rm foo\nliteral text\nMD"
        self.assertIn("rm foo", extract_all_commands(command))

    def test_heredoc_tab_stripped_delimiter(self):
        """<<- indents the closing delimiter with tabs."""
        command = "cat > notes.md <<-'MD'\n\tThe `rm -rf x` guard.\n\tMD"
        self.assertNotIn("rm -rf x", extract_all_commands(command))

    def test_unterminated_heredoc_keeps_current_behavior(self):
        """Without a closing delimiter nothing is treated as a heredoc body."""
        command = "cat > notes.md <<'MD'\nThe `rm -rf x` guard."
        self.assertIn("rm -rf x", extract_all_commands(command))

    def test_herestring_is_not_a_heredoc(self):
        """<<< is a here-string, whose expansions do execute."""
        command = "cat <<< `rm -rf x`"
        self.assertIn("rm -rf x", extract_all_commands(command))


class TestStripHeredocBodies(unittest.TestCase):
    """Tests for strip_heredoc_bodies() heredoc detection."""

    def test_no_heredoc_is_unchanged(self):
        """Commands without heredocs pass through untouched."""
        self.assertEqual(strip_heredoc_bodies("ls -la"), ("ls -la", []))

    def test_quoted_body_is_blanked_and_not_returned(self):
        """A quoted body is removed from the command and never expanded."""
        command = "cat <<'MD'\nrm -rf x\nMD"
        stripped, bodies = strip_heredoc_bodies(command)
        self.assertEqual(bodies, [])
        self.assertNotIn("rm", stripped)
        self.assertEqual(len(stripped), len(command))

    def test_unquoted_body_is_returned(self):
        """An unquoted body is blanked but returned for expansion scanning."""
        command = "cat <<MD\nrm -rf x\nMD"
        stripped, bodies = strip_heredoc_bodies(command)
        self.assertEqual(bodies, ["rm -rf x\n"])
        self.assertNotIn("rm", stripped)

    def test_heredoc_operator_inside_quotes_is_ignored(self):
        """A '<<' inside a quoted word does not start a heredoc."""
        command = "echo 'a << b'\nrm foo"
        self.assertEqual(strip_heredoc_bodies(command), (command, []))

    def test_heredoc_operator_inside_comment_is_ignored(self):
        """A '<<' in a comment must not swallow the lines that follow."""
        command = "# <<END\nrm -rf /tmp/x\nEND"
        self.assertEqual(strip_heredoc_bodies(command), (command, []))

    def test_comment_after_subshell_close_is_ignored(self):
        """'(echo x)# <<END' is a comment, so the next lines are commands."""
        command = "(echo x)# <<END\nrm -rf /tmp/x\nEND"
        self.assertEqual(strip_heredoc_bodies(command), (command, []))

    def test_hash_inside_word_is_not_a_comment(self):
        """A '#' that is not word-initial does not start a comment."""
        command = "echo a#b <<'MD'\nrm -rf x\nMD"
        stripped, bodies = strip_heredoc_bodies(command)
        self.assertEqual(bodies, [])
        self.assertNotIn("rm", stripped)

    def test_left_shift_in_arithmetic_is_not_a_heredoc(self):
        """'<<' inside $(( )) is a shift operator, not a heredoc."""
        command = "echo $((1 << 2))\nrm -rf /tmp/x\n2"
        self.assertEqual(strip_heredoc_bodies(command), (command, []))

    def test_nested_arithmetic_is_skipped_whole(self):
        """Nested parentheses inside arithmetic are handled."""
        command = "echo $(( (1 << 2) + 3 ))\nrm -rf /tmp/x\n3 ))"
        self.assertEqual(strip_heredoc_bodies(command), (command, []))


class TestExpandCommandAliases(unittest.TestCase):
    """Tests for expand_command_aliases() with updated operator support."""

    def test_empty_command(self):
        """Empty command returns empty string."""
        self.assertEqual(expand_command_aliases(""), "")

    def test_single_command_no_alias(self):
        """Single command without alias passes through."""
        # Commands like 'git' are skipped for alias expansion
        result = expand_command_aliases("git status")
        self.assertEqual(result, "git status")

    def test_preserves_pipe_operator(self):
        """Pipe operator is preserved in output."""
        result = expand_command_aliases("git log | grep fix")
        self.assertIn("|", result)

    def test_preserves_background_operator(self):
        """Background operator is preserved in output."""
        result = expand_command_aliases("git fetch & git status")
        self.assertIn("&", result)

    def test_preserves_all_operators(self):
        """All operators are preserved."""
        cmd = "cmd1 && cmd2 || cmd3; cmd4 | cmd5 & cmd6"
        result = expand_command_aliases(cmd)
        self.assertIn("&&", result)
        self.assertIn("||", result)
        self.assertIn(";", result)
        self.assertIn("|", result)
        self.assertIn("&", result)


if __name__ == "__main__":
    unittest.main()
