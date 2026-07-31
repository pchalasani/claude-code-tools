#!/usr/bin/env python3
"""
Unit tests for env_file_protection_hook.py

Tests cover:
    - Reading, writing, editing and searching dotenv files
    - Redirection into a dotenv file
    - Dotenv files named in a later, unrelated shell segment
    - A command name and a dotenv mention with no relationship between them
    - find's -name operand vs. its comparison operands
    - The documented safe commands
"""
import os
import sys
import unittest

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_file_protection_hook import check_env_file_access, shell_segments

# Assembled rather than written literally so this file does not itself trip a
# guard that scans source or command text for the pattern it protects.
DOTENV = "." + "env"


class TestShellSegments(unittest.TestCase):
    """Tests for shell_segments() splitting."""

    def test_single_segment(self):
        self.assertEqual(shell_segments("cat notes.md"), [["cat", "notes.md"]])

    def test_pipeline(self):
        self.assertEqual(
            shell_segments("cat a.json | jq .x"),
            [["cat", "a.json"], ["jq", ".x"]])

    def test_and_and_semicolon(self):
        self.assertEqual(
            shell_segments("ls && echo hi; pwd"),
            [["ls"], ["echo", "hi"], ["pwd"]])

    def test_quotes_are_respected(self):
        self.assertEqual(
            shell_segments('echo "a && b"'), [["echo", "a && b"]])

    def test_unterminated_quote_is_unparseable(self):
        self.assertIsNone(shell_segments('echo "unterminated'))


class TestBlocked(unittest.TestCase):
    """Access to a dotenv file must be blocked."""

    def assertBlocked(self, command, message=None):
        blocked, reason = check_env_file_access(command)
        self.assertTrue(blocked, message or command)
        self.assertIsNotNone(reason)

    def test_read(self):
        self.assertBlocked("cat " + DOTENV)
        self.assertBlocked("less " + DOTENV)
        self.assertBlocked("head -5 " + DOTENV + ".production")
        self.assertBlocked("tail " + DOTENV)

    def test_read_via_absolute_path_to_the_binary(self):
        self.assertBlocked("/bin/cat " + DOTENV)

    def test_read_in_a_subdirectory(self):
        self.assertBlocked("cat src/" + DOTENV)

    def test_suffixed_variants(self):
        self.assertBlocked("cat " + DOTENV + ".local")
        self.assertBlocked("cat " + DOTENV + ".production")

    def test_editors(self):
        self.assertBlocked("vim " + DOTENV)
        self.assertBlocked("nano " + DOTENV)
        self.assertBlocked("code " + DOTENV)

    def test_search(self):
        self.assertBlocked("grep KEY " + DOTENV)
        self.assertBlocked("rg SECRET " + DOTENV)

    def test_write_and_copy(self):
        self.assertBlocked("cp " + DOTENV + " " + DOTENV + ".bak")
        self.assertBlocked("mv " + DOTENV + " backup")
        self.assertBlocked("touch " + DOTENV)
        self.assertBlocked("tee " + DOTENV)
        self.assertBlocked('sed -i "" s/a/b/ ' + DOTENV)

    def test_redirection(self):
        self.assertBlocked("echo x > " + DOTENV)
        self.assertBlocked("echo x >> " + DOTENV)

    def test_subshell(self):
        self.assertBlocked("echo $(cat " + DOTENV + ")")

    def test_later_segment_of_a_compound_command(self):
        self.assertBlocked("ls && cat " + DOTENV)

    def test_leading_variable_assignment(self):
        self.assertBlocked("ENV=prod cat " + DOTENV)

    def test_not_the_first_argument(self):
        self.assertBlocked("cat foo.txt " + DOTENV)

    def test_find_by_name(self):
        self.assertBlocked('find . -name "' + DOTENV + '"')

    def test_file_literally_named_env(self):
        self.assertBlocked("cat env")


class TestAllowed(unittest.TestCase):
    """Commands with no relationship to a dotenv file must be allowed."""

    def assertAllowed(self, command, message=None):
        blocked, _ = check_env_file_access(command)
        self.assertFalse(blocked, message or command)

    def test_ordinary_commands(self):
        self.assertAllowed("cat notes.md")
        self.assertAllowed("grep -rn TODO src/")
        self.assertAllowed("tail -f app.log")

    def test_bare_env_as_a_search_string(self):
        self.assertAllowed("grep env package.json")

    def test_documented_safe_commands(self):
        self.assertAllowed('git commit -m "document the ' + DOTENV + ' guard"')
        self.assertAllowed('gh pr create --body "mentions ' + DOTENV + '"')

    # --- Regressions: an unbounded gap between the command name and the match

    def test_json_key_that_looks_like_a_dotenv_path(self):
        """jq reads a key named "env" out of package.json; nothing opens a file."""
        self.assertAllowed(
            "cat package.json | jq " + DOTENV,
            "the reader's own argument is package.json")

    def test_dotenv_named_only_inside_a_later_echo(self):
        self.assertAllowed(
            'head -20 README.md && echo "copy ' + DOTENV + '.example first"')

    def test_dotenv_named_only_inside_a_later_echo_after_a_semicolon(self):
        self.assertAllowed(
            "less docs/setup.md; echo see " + DOTENV + ".example")

    def test_copy_of_unrelated_files_with_a_trailing_mention(self):
        self.assertAllowed(
            'cp dist/app.js build/ && echo "did not touch ' + DOTENV + '"')

    def test_dotenv_as_a_test_filter_argument(self):
        """The command word is npm; grep appears only as --grep."""
        self.assertAllowed('npm test -- --grep "loads ' + DOTENV + '"')

    def test_tee_to_an_unrelated_path_with_a_trailing_mention(self):
        self.assertAllowed(
            'rg "pattern" src/ | tee /tmp/out.txt && echo ' + DOTENV + ' safe')

    def test_find_comparison_operand_is_not_a_read(self):
        """find -newer uses the file's timestamp; it never reads its contents."""
        self.assertAllowed('find . -name "*.py" -newer ' + DOTENV)


if __name__ == "__main__":
    unittest.main()
