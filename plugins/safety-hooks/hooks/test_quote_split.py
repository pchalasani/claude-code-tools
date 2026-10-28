"""Shell operators inside quotes are data, not command separators."""
import pytest
from command_utils import extract_all_commands, extract_subcommands
from rm_block_hook import check_rm_command


def test_operators_inside_quotes_do_not_split():
    assert extract_subcommands("rg 'a|rm|b' f && ls") == ["rg 'a|rm|b' f", "ls"]
    assert extract_subcommands('echo "x; y" ; ls') == ['echo "x; y"', "ls"]
    assert extract_subcommands("echo $'a\\'|b' | wc") == ["echo $'a\\'|b'", "wc"]


def test_operators_outside_quotes_still_split():
    assert extract_subcommands("a&&b||c;d|e&f") == ["a", "b", "c", "d", "e", "f"]
    assert extract_all_commands("echo $(rm x | y)")[-2:] == ["rm x", "y"]


@pytest.mark.parametrize("command", [
    "rg -c '^(g[a-z]*|rm|git)=' aliases.md",
    "rg 'a|rm|b' notes.md",
    'echo "a; rm b"',
    "grep -E 'x&&rm' f",
])
def test_quoted_rm_is_not_a_call(command):
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "echo 'a|b' | rm foo",
    "rg 'x' f && rm foo",
    """echo "$(printf '"')" ; rm foo""",
    """echo "`printf '"'`" ; rm foo""",
    """echo "$(echo ")")" ; rm foo""",
])
def test_rm_after_a_quoted_word_is_still_a_call(command):
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "echo ok # '\ntrue ; rm foo",
    'echo ok # "\ntrue ; rm foo',
    """echo $(printf '%s' "a$(printf '"')"); rm foo""",
    """echo "$(printf '%s' "a$(printf '"')")" ; rm foo""",
    """echo "$(printf '"')"\ntrue ; rm foo""",
])
def test_comments_and_substitutions_preserve_later_commands(command: str) -> None:
    """Quoting in comments and substitutions cannot hide later commands."""
    assert "rm foo" in extract_all_commands(command)
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "echo '# a|rm|b'",
    'echo "# a; rm foo"',
    "echo prefix#suffix 'a|rm|b'",
])
def test_hash_inside_a_word_is_not_a_comment(command: str) -> None:
    """A literal hash preserves the quoting rules for its containing word."""
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize("separator", ["; ", "\n"])
def test_heredoc_substitution_does_not_hide_later_rm(separator: str) -> None:
    """Unsupported heredoc boundaries must preserve following commands."""
    command = 'echo "$(cat <<\'EOF\'\n) "\nEOF\n)"' + separator + "rm foo"
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    r'echo `printf "x\`true\`"; rm foo`',
    r'echo "`printf "x\`true\`"; rm foo`"',
])
def test_escaped_backticks_do_not_hide_later_rm(command: str) -> None:
    """Legacy nested backticks retain conservative command inspection."""
    assert check_rm_command(command)[0]


def test_continued_whitespace_still_allows_a_shell_comment() -> None:
    """Removing a continuation cannot turn a comment into expansion text."""
    command = 'echo $(echo \\\n# )"\ntrue); rm foo'
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'echo "$(case x in x) echo hi;; esac; rm foo)"',
    'echo "$(case x in x) echo hi;; esac\nrm foo)"',
    'echo "$(case x in x) echo hi;; esac)"; rm foo',
    'echo "$(echo "$(case x in x) echo hi;; esac; rm foo)")"',
    'echo "`case x in x) echo hi;; esac; rm foo`"',
])
def test_case_patterns_cannot_hide_rm(command: str) -> None:
    """Unsupported case patterns retain conservative command inspection."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "echo $(printf 'case x in x) a|rm|b')",
    'echo "$(printf \"case a|rm|b\")"',
])
def test_quoted_case_text_preserves_operator_quoting(command: str) -> None:
    """A quoted occurrence of case is ordinary data, not a case pattern."""
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize("keyword", [
    "ca\\\nse", "case\\\n", "c\\\na\\\ns\\\ne",
])
def test_continued_case_keyword_cannot_hide_rm(keyword: str) -> None:
    """Bash removes continuations before recognizing the case keyword."""
    command = f'echo "$({keyword} x in x) echo hi;; esac; rm foo)"'
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'echo "$(declare -A a; a[)]=x; rm foo)"',
    'echo "$(declare -A a=([)]=x); rm foo)"',
    'echo "$(declare -A a; a[)]=x\nrm foo)"',
])
def test_array_subscript_parenthesis_cannot_hide_rm(command: str) -> None:
    """Array subscript text cannot truncate an enclosing substitution."""
    assert check_rm_command(command)[0]
