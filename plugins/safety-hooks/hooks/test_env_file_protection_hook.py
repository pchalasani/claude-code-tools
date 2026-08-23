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
import shlex
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

    def test_newline_is_a_separator(self):
        self.assertEqual(
            shell_segments("echo ok\ncat notes.md"),
            [["echo", "ok"], ["cat", "notes.md"]])

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

    def test_source_builtins(self):
        self.assertBlocked("source " + DOTENV)
        self.assertBlocked(". config/" + DOTENV + ".local")

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
        self.assertBlocked("ag SECRET " + DOTENV)
        self.assertBlocked("ack SECRET " + DOTENV)

    def test_script_reader_file_operands(self):
        self.assertBlocked("sed -n p " + DOTENV)
        self.assertBlocked("sed -e p " + DOTENV)
        self.assertBlocked("sed -f " + DOTENV + " README.md")
        self.assertBlocked("awk '{print}' " + DOTENV)
        self.assertBlocked("awk -f " + DOTENV + " README.md")

    def test_search_options_with_attached_dotenv_values(self):
        self.assertBlocked("grep -f" + DOTENV + " target.txt")
        self.assertBlocked("grep -Hf" + DOTENV + " target.txt")
        self.assertBlocked("rg -g" + DOTENV + " SECRET .")
        self.assertBlocked("rg -ug" + DOTENV + " SECRET .")
        self.assertBlocked("rg -f" + DOTENV + " target.txt")
        self.assertBlocked("grep -r SECRET . --include=" + DOTENV)
        self.assertBlocked("grep -r SECRET . --include=" + DOTENV + "*")
        self.assertBlocked("grep --exclude-from=" + DOTENV + " SECRET .")
        self.assertBlocked("rg --iglob=" + DOTENV + " SECRET .")
        self.assertBlocked("rg --ignore-file " + DOTENV + " SECRET .")
        self.assertBlocked("rg --ignore-file=" + DOTENV + " SECRET .")
        self.assertBlocked("grep -r SECRET . --inc=" + DOTENV)
        self.assertBlocked("grep --exclude-f=" + DOTENV + " SECRET .")
        self.assertBlocked("grep --reg=SECRET " + DOTENV)

    def test_search_inclusion_globs_that_select_dotenv(self):
        self.assertBlocked("grep -r SECRET . --include='*.env*'")
        self.assertBlocked("grep -r --include '*.env*' SECRET .")
        self.assertBlocked("rg -g '*.env*' SECRET .")
        self.assertBlocked("rg --iglob='[.]ENV*' SECRET .")
        self.assertBlocked("rg --hidden --glob='.env/config' SECRET .")
        self.assertBlocked("grep -r --include=.env.production SECRET .")
        self.assertBlocked("rg -g '.[e]nv.[0-9]*' SECRET .")
        self.assertBlocked("rg -g '[^a]env' SECRET .")
        self.assertBlocked("grep --include='[^a]env' SECRET .")
        self.assertBlocked("grep -r SECRET --include='*env' .")
        self.assertBlocked("grep -r SECRET --include='*env*' .")
        self.assertBlocked("grep -r SECRET --include='*nv' .")
        self.assertBlocked("grep -r SECRET --include='*[e]nv' .")
        self.assertBlocked("rg SECRET -g '*e?v' .")

    def test_write_and_copy(self):
        self.assertBlocked("cp " + DOTENV + " " + DOTENV + ".bak")
        self.assertBlocked("mv " + DOTENV + " backup")
        self.assertBlocked("touch " + DOTENV)
        self.assertBlocked("tee " + DOTENV)
        self.assertBlocked('sed -i "" s/a/b/ ' + DOTENV)

    def test_redirection(self):
        self.assertBlocked("echo x > " + DOTENV)
        self.assertBlocked("echo x >> " + DOTENV)
        self.assertBlocked("git commit > " + DOTENV)
        self.assertBlocked("echo x > env")
        self.assertBlocked("echo x &> " + DOTENV)
        self.assertBlocked("echo x &>> " + DOTENV)
        self.assertBlocked("echo x >&" + DOTENV)
        self.assertBlocked("exec 3<> " + DOTENV)

    def test_subshell(self):
        self.assertBlocked("echo $(cat " + DOTENV + ")")

    def test_process_substitutions(self):
        self.assertBlocked("diff <(cat " + DOTENV + ") /dev/null")
        self.assertBlocked("echo secret > >(tee " + DOTENV + ")")

    def test_unquoted_heredoc_substitutions(self):
        self.assertBlocked("cat <<EOF\n$(cat " + DOTENV + ")\nEOF")
        self.assertBlocked("cat <<EOF\n`cat " + DOTENV + "`\nEOF")

    def test_nested_and_double_quoted_subshells(self):
        self.assertBlocked('echo "$(printf %s $(cat ' + DOTENV + '))"')
        self.assertBlocked("echo $(echo $(cat " + DOTENV + "))")

    def test_later_segment_of_a_compound_command(self):
        self.assertBlocked("ls && cat " + DOTENV)

    def test_leading_variable_assignment(self):
        self.assertBlocked("ENV=prod cat " + DOTENV)

    def test_not_the_first_argument(self):
        self.assertBlocked("cat foo.txt " + DOTENV)

    def test_find_by_name(self):
        self.assertBlocked("find " + DOTENV + " -delete")
        self.assertBlocked("find src " + DOTENV + ".local -delete")
        for option in ('-H', '-L', '-P', '--'):
            self.assertBlocked("find " + option + " " + DOTENV + " -delete")
        self.assertBlocked('find . -name "' + DOTENV + '"')
        self.assertBlocked("find . -iname " + DOTENV + " -delete")
        self.assertBlocked("find . -path '*/" + DOTENV + "' -delete")
        self.assertBlocked("find . -ipath '*/*.ENV.local' -delete")
        self.assertBlocked("find . -wholename '*/.env' -delete")
        self.assertBlocked("find . -iwholename '*/.ENV.local' -delete")
        self.assertBlocked("find . -regex '.*/[.]env' -delete")
        self.assertBlocked("find . -name '.?nv' -delete")
        self.assertBlocked("find . -name '[.]e[n]v' -delete")
        self.assertBlocked("find . -name '[[:punct:]]env' -delete")
        self.assertBlocked("find . -name '[[:graph:]]env' -delete")
        self.assertBlocked("find '!secrets' -path '!secrets/[.]env' -delete")
        self.assertBlocked(r"find . -regex '.*/\.env' -delete")
        self.assertBlocked("find . -regex '.*[.]env' -delete")
        self.assertBlocked(r"find . -regex '.*\.env' -delete")
        self.assertBlocked("find . -not -path '*/build/*' -name '.env'")
        self.assertBlocked("find . ! -name '*.log' -path '*/.env'")
        self.assertBlocked("find . ! ! -name '.env'")
        self.assertBlocked("find . -not -not -name '.env' -print")
        # -o re-selects what the negation excluded, so negation is not
        # honoured when the expression contains a disjunction.
        self.assertBlocked(r"find . ! -name '.env' -o -exec cat {} \;")
        self.assertBlocked("find . ! -name '.env' -or -delete")
        self.assertBlocked("find . ! -name '.env' , -exec cat {} +")
        # '!' here is -printf's format operand, not a negation.
        self.assertBlocked(r"find . -printf '!' -name '.env' -exec cat {} \;")
        # Same trick through two-operand -fprintf FILE FORMAT.
        self.assertBlocked("find . -fprintf /tmp/list '!' -name '.env' -delete")
        # An action left of the negation runs before the exclusion filters.
        # (The \; -exec spelling splits into another shell segment and is a
        # pre-existing gap on main; the single-segment forms are covered.)
        self.assertBlocked("find . -delete ! -name '.env'")
        self.assertBlocked("find . -exec cat {} + ! -name '.env'")
        self.assertBlocked(r"find . -fprintf .env '%p\n' ! -name '.env'")
        # Output actions write to their FILE operand.
        self.assertBlocked("find . -fprintf .env '%p'")
        self.assertBlocked("find . -fprint .env")
        self.assertBlocked("find . -fls .env.local")

    def test_file_literally_named_env(self):
        self.assertBlocked("cat env")
        self.assertBlocked("cat ENV")
        self.assertBlocked("cat ./ENV")

    def test_mixed_case_dotenv_name(self):
        self.assertBlocked("cat " + DOTENV.upper() + ".LOCAL")

    def test_literal_prefix_with_shell_suffix(self):
        self.assertBlocked("cat " + DOTENV + "*")
        self.assertBlocked("cat " + DOTENV + "$SUFFIX")
        self.assertBlocked("cat " + DOTENV + "{,.local}")

    def test_parameter_expansion_can_resolve_to_dotenv(self):
        for operator in (':-', ':=', ':+', ':?'):
            with self.subTest(operator=operator):
                expansion = "${FILE" + operator + DOTENV + "}"
                self.assertBlocked("cat " + expansion)
                self.assertBlocked("echo value > " + expansion)

    def test_newline_separates_commands(self):
        self.assertBlocked("echo okay\ncat " + DOTENV)

    def test_supported_wrappers(self):
        self.assertBlocked("sudo command cat " + DOTENV)
        self.assertBlocked("sudo --user root cat " + DOTENV)
        self.assertBlocked("sudo -nu root cat " + DOTENV)
        self.assertBlocked("sudo -nuroot cat " + DOTENV)
        self.assertBlocked("env -i MODE=test cat " + DOTENV)
        self.assertBlocked("sudo sh -c 'cat " + DOTENV + "'")

    def test_generic_wrappers(self):
        for prefix in (
                "exec ", "time ", "nohup ", "nice -n 5 ", "timeout 2 ",
                "xargs "):
            with self.subTest(prefix=prefix):
                self.assertBlocked(prefix + "cat " + DOTENV)
        self.assertBlocked("eval 'cat " + DOTENV + "'")

    def test_eval_option_terminator(self):
        self.assertBlocked("eval -- 'cat " + DOTENV + "'")

    def test_xargs_reader_with_stdin_supplied_paths(self):
        self.assertBlocked("printf '%s\\n' " + DOTENV + " | xargs cat")
        self.assertBlocked("printf '%s\\n' " + DOTENV + " | xargs -n1 cat")

    def test_env_split_string(self):
        self.assertBlocked("env -S 'cat " + DOTENV + "'")
        self.assertBlocked("env --split-string 'cat " + DOTENV + "'")
        self.assertBlocked("env --split-string='cat " + DOTENV + "'")
        self.assertBlocked("env -S 'cat' " + DOTENV)
        self.assertBlocked("env --split-string='cat' " + DOTENV)
        self.assertBlocked("env -iS 'cat " + DOTENV + "'")
        self.assertBlocked("env -iS'cat " + DOTENV + "'")
        self.assertBlocked("env -S '-- cat' " + DOTENV)
        self.assertBlocked("env --split-string='-- cat' " + DOTENV)
        self.assertBlocked("env -S '-i cat " + DOTENV + "'")
        self.assertBlocked("env -S '-C /tmp cat " + DOTENV + "'")
        self.assertBlocked("env -S '-u HOME cat " + DOTENV + "'")

    def test_env_options_before_split_string(self):
        for prefix in (
                "-u UNUSED", "-uUNUSED", "--unset UNUSED",
                "--unset=UNUSED", "-C /tmp", "-C/tmp",
                "--chdir /tmp", "--chdir=/tmp", "-iu UNUSED",
                "-a name", "-aname", "--argv0=name",
                "-P /usr/bin", "-P/usr/bin"):
            with self.subTest(prefix=prefix):
                self.assertBlocked(
                    "env " + prefix + " -S 'cat " + DOTENV + "'")

    def test_shell_command_strings(self):
        for shell in ("sh", "bash", "zsh", "dash", "ksh"):
            with self.subTest(shell=shell):
                self.assertBlocked(shell + " -ec 'cat " + DOTENV + "'")

    def test_backticks(self):
        self.assertBlocked("echo `cat " + DOTENV + "`")

    def test_find_exec(self):
        self.assertBlocked("find . -exec cat " + DOTENV + " \\;")

    def test_input_redirection(self):
        self.assertBlocked("wc -l < " + DOTENV)

    def test_nesting_limit_fails_closed_for_protected_access(self):
        command = "cat " + DOTENV
        for _ in range(12):
            command = "sh -c " + shlex.quote(command)
        self.assertBlocked(command)


class TestAllowed(unittest.TestCase):
    """Commands with no relationship to a dotenv file must be allowed."""

    def assertAllowed(self, command, message=None):
        blocked, _ = check_env_file_access(command)
        self.assertFalse(blocked, message or command)

    def test_ordinary_commands(self):
        self.assertAllowed("cat notes.md")
        self.assertAllowed("grep -rn TODO src/")
        self.assertAllowed("tail -f app.log")

    def test_heredoc_literal_text_is_not_executed(self):
        self.assertAllowed("cat <<'EOF'\n$(cat " + DOTENV + ")\nEOF")
        self.assertAllowed("cat <<\\EOF\n`cat " + DOTENV + "`\nEOF")
        self.assertAllowed("cat <<EOF\ncat " + DOTENV + "\nEOF")

    def test_reader_name_as_non_executable_argument(self):
        self.assertAllowed("echo cat " + DOTENV)
        self.assertAllowed("false cat " + DOTENV)
        self.assertAllowed("git commit -m cat " + DOTENV)

    def test_bare_env_as_a_search_string(self):
        self.assertAllowed("grep env package.json")

    def test_dotenv_search_pattern_is_not_a_file_access(self):
        self.assertAllowed("grep .env README.md")
        self.assertAllowed(r"rg '\.env' docs/")
        self.assertAllowed("grep -e .env README.md")
        self.assertAllowed("rg --regexp=.env docs/")
        self.assertAllowed("ag .env README.md")
        self.assertAllowed("ack .env README.md")

    def test_search_value_options_precede_dotenv_pattern(self):
        self.assertAllowed("grep -A 2 .env README.md")
        self.assertAllowed("grep -B2 .env README.md")
        self.assertAllowed("grep --context 2 .env README.md")
        self.assertAllowed("grep --max-count=1 .env README.md")
        self.assertAllowed("rg -C 2 .env README.md")
        self.assertAllowed("rg -j4 .env README.md")
        self.assertAllowed("rg --threads 2 .env README.md")
        self.assertAllowed("rg --max-columns=80 .env README.md")
        self.assertAllowed("grep --label stdin .env README.md")
        self.assertAllowed("rg --color never .env README.md")
        self.assertAllowed("rg --replace replacement .env README.md")

    def test_negated_search_globs_are_exclusions(self):
        self.assertAllowed("rg -g '!.env*' SECRET .")
        self.assertAllowed("rg --glob='!**/.env*' SECRET .")
        self.assertAllowed("rg -g '[a-z]*.py' SECRET .")

    def test_ordinary_globs_do_not_select_dotenv(self):
        """A '*' must not be allowed to spell out the literal '.env'."""
        self.assertAllowed("find . -name '*.ts'")
        self.assertAllowed("find . -path '*/dist/*'")
        self.assertAllowed("grep -rn TODO --include='*.py' .")
        self.assertAllowed("rg TODO -g '*.rs'")
        self.assertAllowed("grep -r SECRET --include='*' .")
        # Accepted trade-off: '*.local' can match '.env.local', but only by
        # '*' expanding over the whole '.env' core; treated as ordinary.
        self.assertAllowed("grep -r X --include='*.local' .")

    def test_negated_find_predicates_are_exclusions(self):
        self.assertAllowed("find . ! -path '*/build/*'")
        self.assertAllowed("find . -not -path '*/build/*' -name '*.go'")
        self.assertAllowed("find . ! -path '*/.env'")
        self.assertAllowed("find . -not -name '.env'")
        self.assertAllowed("find . -type f ! -name '.env'")
        # Exclusion before the action: -exec never sees the dotenv.
        self.assertAllowed(r"find . ! -name '.env' -exec cat {} \;")
        self.assertAllowed("find . -fprint /tmp/list ! -name '*.log'")

    def test_quoted_or_escaped_shell_globs_are_literal(self):
        self.assertAllowed("cat '.[e]nv'")
        self.assertAllowed(r"cat .\[e\]nv")
        self.assertAllowed("echo x > '.[e]nv'")

    def test_dotenv_script_text_is_not_a_file_access(self):
        self.assertAllowed("sed -n '/.env/p' README.md")
        self.assertAllowed("awk '/.env/ {print}' README.md")

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
        self.assertAllowed('find . -name "README.md" -newer ' + DOTENV)

    def test_quoted_redirect_explanation(self):
        self.assertAllowed('echo "write > ' + DOTENV + ' manually"')
        self.assertAllowed("echo '>' " + DOTENV)
        self.assertAllowed("echo \\> " + DOTENV)

    def test_single_quoted_backticks_are_literal(self):
        self.assertAllowed("echo '`cat " + DOTENV + "`'")

    def test_literal_substitution_text(self):
        self.assertAllowed("echo '$(cat " + DOTENV + ")'")
        self.assertAllowed("echo \\$(cat " + DOTENV + ")")
        self.assertAllowed("echo '<(cat " + DOTENV + ")'")
        self.assertAllowed('echo ">(tee ' + DOTENV + ')"')

    def test_deeply_nested_harmless_text(self):
        command = "echo " + DOTENV
        for _ in range(12):
            command = "sh -c " + shlex.quote(command)
        self.assertAllowed(command)

        quoted_command = 'echo "cat ' + DOTENV + ' is protected"'
        for _ in range(12):
            quoted_command = "sh -c " + shlex.quote(quoted_command)
        self.assertAllowed(quoted_command)


if __name__ == "__main__":
    unittest.main()
