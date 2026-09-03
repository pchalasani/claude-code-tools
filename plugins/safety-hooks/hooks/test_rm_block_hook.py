#!/usr/bin/env python3
"""
Unit tests for rm_block_hook.py

Tests cover:
    - Direct rm command blocking
    - rm with absolute paths (/bin/rm, /usr/bin/rm)
    - rm chained with shell operators (&&, ||, ;, |, &)
    - rm hidden in subshells ($() and backticks)
    - Safe commands that should pass through
"""
import unittest
import sys
import os

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rm_block_hook import check_rm_command, _is_rm_command


class TestIsRmCommand(unittest.TestCase):
    """Tests for _is_rm_command() single command detection."""

    def test_bare_rm(self):
        """Bare 'rm' command is detected."""
        self.assertTrue(_is_rm_command("rm"))

    def test_rm_with_args(self):
        """rm with arguments is detected."""
        self.assertTrue(_is_rm_command("rm foo.txt"))
        self.assertTrue(_is_rm_command("rm -rf /tmp/test"))
        self.assertTrue(_is_rm_command("rm -f file1 file2"))

    def test_rm_with_path(self):
        """rm with absolute path is detected."""
        self.assertTrue(_is_rm_command("/bin/rm foo"))
        self.assertTrue(_is_rm_command("/usr/bin/rm -rf /"))

    def test_not_rm_commands(self):
        """Commands that are not rm should not be detected."""
        self.assertFalse(_is_rm_command("ls"))
        self.assertFalse(_is_rm_command("echo rm"))
        self.assertFalse(_is_rm_command("grep rm file"))
        self.assertFalse(_is_rm_command("firmware"))  # Contains 'rm' but not rm command
        self.assertFalse(_is_rm_command(""))

    def test_whitespace_handling(self):
        """Whitespace is normalized."""
        self.assertTrue(_is_rm_command("  rm   foo  "))
        self.assertTrue(_is_rm_command("\trm\t-rf\t/"))


class TestCheckRmCommand(unittest.TestCase):
    """Tests for check_rm_command() comprehensive detection."""

    def test_direct_rm_blocked(self):
        """Direct rm commands are blocked."""
        blocked, reason = check_rm_command("rm foo.txt")
        self.assertTrue(blocked)
        self.assertIsNotNone(reason)

    def test_rm_with_path_blocked(self):
        """rm with absolute path is blocked."""
        blocked, _ = check_rm_command("/bin/rm foo")
        self.assertTrue(blocked)

        blocked, _ = check_rm_command("/usr/bin/rm -rf /")
        self.assertTrue(blocked)

    def test_safe_commands_pass(self):
        """Safe commands are not blocked."""
        blocked, reason = check_rm_command("ls -la")
        self.assertFalse(blocked)
        self.assertIsNone(reason)

        blocked, _ = check_rm_command("git status")
        self.assertFalse(blocked)

        blocked, _ = check_rm_command("echo hello")
        self.assertFalse(blocked)

    # Security bypass tests - these are the key regression tests

    def test_pipe_bypass_blocked(self):
        """rm after pipe operator is blocked - security regression test."""
        blocked, _ = check_rm_command("echo ok | rm foo")
        self.assertTrue(blocked, "rm after pipe should be blocked")

        blocked, _ = check_rm_command("cat file | rm -rf /tmp")
        self.assertTrue(blocked, "rm in pipe chain should be blocked")

    def test_background_bypass_blocked(self):
        """rm after background operator is blocked - security regression test."""
        blocked, _ = check_rm_command("sleep 1 & rm foo")
        self.assertTrue(blocked, "rm after background operator should be blocked")

        blocked, _ = check_rm_command("cmd & /bin/rm bar")
        self.assertTrue(blocked, "rm with path after & should be blocked")

    def test_and_operator_blocked(self):
        """rm after && operator is blocked."""
        blocked, _ = check_rm_command("cd /tmp && rm foo")
        self.assertTrue(blocked)

    def test_or_operator_blocked(self):
        """rm after || operator is blocked."""
        blocked, _ = check_rm_command("test -f x || rm y")
        self.assertTrue(blocked)

    def test_semicolon_operator_blocked(self):
        """rm after ; operator is blocked."""
        blocked, _ = check_rm_command("echo done; rm foo")
        self.assertTrue(blocked)

    def test_subshell_dollar_paren_blocked(self):
        """rm inside $() subshell is blocked - security regression test."""
        blocked, _ = check_rm_command("echo $(rm foo)")
        self.assertTrue(blocked, "rm in $() subshell should be blocked")

        blocked, _ = check_rm_command("$(rm -rf /)")
        self.assertTrue(blocked, "bare $() with rm should be blocked")

    def test_subshell_backtick_blocked(self):
        """rm inside backtick subshell is blocked - security regression test."""
        blocked, _ = check_rm_command("echo `rm foo`")
        self.assertTrue(blocked, "rm in backticks should be blocked")

        blocked, _ = check_rm_command("cat `rm bar`")
        self.assertTrue(blocked, "rm in backticks should be blocked")

    def test_quoted_heredoc_documenting_rm_is_allowed(self):
        """Writing a doc that mentions rm in a code span is not deletion.

        Regression test for issue #187: a single-quoted heredoc delimiter
        disables every expansion, so the backticks in the body are literal.
        """
        command = "cat > notes.md <<'MD'\nThe `rm` guard blocks deletions.\nMD"
        blocked, _ = check_rm_command(command)
        self.assertFalse(blocked, "literal rm in a quoted heredoc is data")

    def test_unquoted_heredoc_substitution_still_blocked(self):
        """An unquoted delimiter really does run the substitution."""
        command = "cat > notes.md <<MD\nThe `rm -rf x` guard.\nMD"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm in an unquoted heredoc should be blocked")

    def test_rm_chained_after_heredoc_still_blocked(self):
        """Blanking the body must not hide the command that opens it."""
        blocked, _ = check_rm_command("cat <<'MD' && rm foo\nliteral\nMD")
        self.assertTrue(blocked, "rm after a heredoc redirect should be blocked")

    def test_comment_cannot_fake_a_heredoc(self):
        """A '<<' in a comment must not blank out a following rm."""
        blocked, _ = check_rm_command("# <<END\ntrue; rm -rf /tmp/x\nEND")
        self.assertTrue(blocked, "rm after a commented '<<' should be blocked")

    def test_comment_after_subshell_cannot_fake_a_heredoc(self):
        """A comment right after ')' must not blank out a following rm."""
        blocked, _ = check_rm_command("(echo x)# <<END\ntrue; rm -rf /tmp/x\nEND")
        self.assertTrue(blocked, "rm after '(...)# <<' should be blocked")

    def test_parameter_expansion_cannot_fake_a_heredoc(self):
        """'<<' in ${x:-<<EOF} must not blank out a following rm."""
        blocked, _ = check_rm_command("echo ${x:-<<EOF}\ntrue; rm -rf /tmp/x\nEOF}")
        self.assertTrue(blocked, "rm after '${x:-<<EOF}' should be blocked")

    def test_arithmetic_shift_cannot_fake_a_heredoc(self):
        """A left shift in $(( )) must not blank out a following rm."""
        blocked, _ = check_rm_command("echo $((1 << 2))\ntrue; rm -rf /tmp/x\n2")
        self.assertTrue(blocked, "rm after an arithmetic shift should be blocked")

    def test_newline_separated_rm_is_blocked(self):
        """A newline ends a command, so the next line is a command too."""
        blocked, _ = check_rm_command("echo hi\nrm -rf /tmp/x")
        self.assertTrue(blocked, "rm on its own line should be blocked")

    def test_ansi_c_delimiter_cannot_fake_a_heredoc(self):
        """<<$'EOF' ends at EOF, so a later rm is not inside the body."""
        command = "cat <<$'EOF'\nliteral\nEOF\nrm -rf /tmp/x\n$EOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after a $'EOF' heredoc should be blocked")

    def test_escaped_brace_cannot_fake_a_heredoc(self):
        """'\\}' does not close ${...}, so '<<EOF' in it opens no heredoc."""
        blocked, _ = check_rm_command(
            "echo ${x:-\\}<<EOF}\nrm -rf /tmp/x\nEOF}")
        self.assertTrue(blocked, "rm after '${x:-\\}<<EOF}' should be blocked")

    def test_line_continuation_cannot_hide_rm_in_a_body(self):
        """A continued header means the rm runs before the body starts."""
        command = "cat <<EOF ; \\\nrm -rf /tmp/x\nliteral\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm on a continued header should be blocked")

    def test_second_heredoc_body_cannot_hide_rm(self):
        """Both bodies on a two-heredoc header are data, so the rm is not."""
        command = "cat <<A <<'B'\nfirst\nA\necho <<X\nB\nrm -rf /tmp/x\nX"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after two heredoc bodies should be blocked")

    def test_documenting_rm_in_a_second_heredoc_body_is_allowed(self):
        """The second body is data, so documenting rm in it is not deletion."""
        command = "cat <<A <<'B'\nliteral a\nA\nThe `rm -rf x` guard\nB"
        blocked, _ = check_rm_command(command)
        self.assertFalse(blocked, "literal rm in the second body is data")

    def test_legacy_arithmetic_shift_cannot_fake_a_heredoc(self):
        """A shift in the deprecated $[ ] form must not blank a following rm."""
        blocked, _ = check_rm_command("echo $[1 << 2]\nrm -rf /tmp/x\n2]")
        self.assertTrue(blocked, "rm after 'echo $[1 << 2]' should be blocked")

    def test_array_subscript_shift_cannot_fake_a_heredoc(self):
        """A shift inside an array subscript must not blank a following rm."""
        blocked, _ = check_rm_command("a[1<<2]=foo\nrm -rf /tmp/x\n2]=foo")
        self.assertTrue(blocked, "rm after 'a[1<<2]=foo' should be blocked")

    def test_process_substitution_cannot_hide_rm(self):
        """The rm runs inside <( ), before the heredoc body starts."""
        command = "cat <<EOF <(echo x\nrm -rf /tmp/x\n)\nliteral\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm inside <( ) on the header is a command")

    def test_localized_quote_delimiter_cannot_hide_rm(self):
        """$\"A\\\\B\" unescapes, so bash ends that body before the rm."""
        command = 'cat <<$"A\\\\B"\ndata\nA\\B\nrm -rf /tmp/x\nA\\\\B'
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after a '$\"A\\\\B\"' body should be blocked")

    def test_double_quoted_escape_delimiter_cannot_hide_rm(self):
        """Bash ends that body at 'A\\B', so the next line is a command."""
        command = 'cat <<"A\\\\B"\ndata\nA\\B\nrm -rf /tmp/x\nA\\\\B'
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after a '\"A\\\\B\"' body should be blocked")

    def test_continued_delimiter_line_cannot_hide_rm(self):
        """An unquoted body ends at a delimiter split across two lines."""
        command = "cat <<EOF\ndata\nEO\\\nF\nrm -rf /tmp/x\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after a continued delimiter is a command")

    def test_nested_group_paren_cannot_hide_rm(self):
        """The substitution is still open, so the rm inside it is a command."""
        command = "cat <<EOF $( (echo x)\nrm -rf /tmp/x\n)\nliteral\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm inside a still-open $( ) should be blocked")

    def test_substitution_delimiter_cannot_fake_a_heredoc(self):
        """'<<$(echo EOF)' ends at that literal line, so the rm is a command."""
        command = "cat <<$(echo EOF)\npayload\n$(echo EOF)\nrm -rf /tmp/x\n$"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm after a '$(echo EOF)' body should be blocked")

    def test_rm_in_a_substitution_before_the_body_is_blocked(self):
        """An unfinished $( ) means the rm runs before the body starts."""
        command = "cat <<'EOF' $(echo start\nrm -rf /tmp/x)\nliteral\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm inside $( ) on the header is a command")

    def test_rm_in_backticks_before_the_body_is_blocked(self):
        """Backticks hold the header open just as $( ) does."""
        command = "cat <<'EOF' `echo start\nrm -rf /tmp/x`\nliteral\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm inside backticks on the header is a command")

    def test_bodyless_heredoc_in_a_substitution_cannot_hide_rm(self):
        """The substitution closes first, so the next line is a command."""
        command = "echo $(cat <<'EOF') tail\nrm -rf /tmp/x\nEOF"
        blocked, _ = check_rm_command(command)
        self.assertTrue(blocked, "rm is not a body when the body never starts")

    def test_documenting_rm_inside_a_substitution_heredoc_is_allowed(self):
        """A heredoc body inside $( ) is still data."""
        command = "echo $(cat <<'EOF'\nThe `rm -rf x` guard.\nEOF\n)"
        blocked, _ = check_rm_command(command)
        self.assertFalse(blocked, "literal rm in a quoted body is data")

    def test_complex_bypass_attempts(self):
        """Complex commands attempting to hide rm are blocked."""
        # Multiple levels of indirection
        blocked, _ = check_rm_command("echo safe | cat | rm evil")
        self.assertTrue(blocked, "rm at end of pipe chain should be blocked")

        # Subshell inside chained command
        blocked, _ = check_rm_command("echo $(rm foo) && ls")
        self.assertTrue(blocked, "rm in subshell with && should be blocked")

        # Background with subshell
        blocked, _ = check_rm_command("$(rm x) & echo done")
        self.assertTrue(blocked, "rm in subshell with & should be blocked")

    def test_nested_subshell_bypass_blocked(self):
        """rm hidden in nested $() subshells is blocked - P1 security fix."""
        # This was a bypass: the regex stopped at first ), missing inner rm
        blocked, _ = check_rm_command("echo $(echo $(rm foo))")
        self.assertTrue(blocked, "rm in nested subshell should be blocked")

        # Deeper nesting
        blocked, _ = check_rm_command("$(cat $(ls $(rm secret)))")
        self.assertTrue(blocked, "rm in deeply nested subshell should be blocked")

    def test_reason_message_content(self):
        """Blocked commands include helpful guidance in reason."""
        blocked, reason = check_rm_command("rm foo")
        self.assertTrue(blocked)
        self.assertIn("TRASH", reason)
        self.assertIn("mv", reason)
        self.assertIn("TRASH-FILES.md", reason)


class TestEdgeCases(unittest.TestCase):
    """Edge case tests for robustness."""

    def test_empty_command(self):
        """Empty command is not blocked."""
        blocked, _ = check_rm_command("")
        self.assertFalse(blocked)

    def test_whitespace_only(self):
        """Whitespace-only command is not blocked."""
        blocked, _ = check_rm_command("   ")
        self.assertFalse(blocked)

    def test_rm_in_string_not_blocked(self):
        """String containing 'rm' but not as command passes."""
        # 'rm' as part of echo string
        blocked, _ = check_rm_command("echo 'do not rm this'")
        self.assertFalse(blocked, "rm in quoted string should not be blocked")

    def test_command_starting_with_rm_prefix(self):
        """Commands starting with 'rm' prefix but not rm command."""
        blocked, _ = check_rm_command("rmdir empty_dir")
        self.assertFalse(blocked, "rmdir should not be blocked")


if __name__ == "__main__":
    unittest.main()
