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
])
def test_rm_after_a_quoted_word_is_still_a_call(command):
    assert check_rm_command(command)[0]
