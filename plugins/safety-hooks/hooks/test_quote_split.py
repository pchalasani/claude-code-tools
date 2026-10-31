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
    'echo `echo \\\\"; rm; `', 'echo ${x:-`echo \\\\"; rm; `}',
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
    'echo "$(cat <\\\n<\'EOF\'\n) "\nEOF\n)"; rm foo',
])
def test_unsupported_substitution_boundary_cannot_hide_rm(command: str) -> None:
    """Unsupported substitution text cannot hide later executable commands."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'echo ${x:- # }; rm foo', 'echo ${| echo hi; rm foo; }',
    'echo ${x:- # }\nrm foo', 'echo ${ echo hi; rm foo; }',
    'echo "$(echo ${x:- # }; rm foo)"', 'echo ${x:-${| rm foo; }}',
])
def test_parameter_expansion_boundaries_cannot_hide_rm(command: str) -> None:
    """Parameter text and brace commands preserve executable separators."""
    assert check_rm_command(command)[0]


def test_parameter_expansion_operators_remain_data() -> None:
    """Literal separators in a parameter value are not executable commands."""
    assert not check_rm_command('echo "${x:-a|rm|b}"')[0]


@pytest.mark.parametrize("command", [
    'echo ${x:-{}; rm foo # }', 'echo "${x:-{}"; rm foo # }',
])
def test_literal_open_brace_cannot_hide_rm(command: str) -> None:
    """Only nested expansions, not literal braces, delay the closing brace."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'cat ${x:-<(echo hi; rm foo)}', 'printf data >${x:->(cat; rm foo)}',
])
def test_parameter_process_substitution_cannot_hide_rm(command: str) -> None:
    """Opaque process substitutions retain conservative command splitting."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "echo x\r# comment; rm foo", 'echo "$(echo x\r# comment; rm foo)"',
    'echo <(true)#tag; rm foo',
])
def test_word_suffix_hash_does_not_start_a_comment(command: str) -> None:
    """A hash following literal word text does not start a comment."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'echo "$(rm foo; echo [x])"', 'echo "$(rm foo; echo case)"',
])
def test_conservative_split_keeps_earlier_subcommands(command: str) -> None:
    """A later unsupported construct cannot hide the first inner command."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'echo "$(rm foo; echo <(true)#tag)"',
    'echo "$(rm foo; echo >(true)#tag)"',
    'echo "$(echo <(true)#tag; rm foo)"',
    'echo "$(echo >(true)#tag; rm foo)"',
])
def test_nested_process_suffix_hash_is_word_text(command: str) -> None:
    """A process substitution ends a word segment, not a shell comment gap."""
    assert check_rm_command(command)[0]


def test_legacy_bash_parameter_close_cannot_hide_rm() -> None:
    """Bash 3.2 can close an unquoted substitution inside parameter text."""
    assert check_rm_command('echo $(echo ${x:-); rm foo # })')[0]


@pytest.mark.parametrize("prefix", [
    "echo $((1 << 2)); ",
    'echo "$(case x in x) :;; esac)"; ',
])
def test_conservative_fallback_joins_continued_commands(prefix: str) -> None:
    """A fallback still joins command words continued over a physical newline."""
    assert check_rm_command(prefix + "r\\\nm foo")[0]


@pytest.mark.parametrize("command", [
    "echo $\\\n(echo ok; rm foo)",
    'echo "$(echo $\\\n(printf x; rm foo))"',
])
def test_continued_substitution_marker_is_extracted(command: str) -> None:
    """Splitting and recursive extraction inspect the same continued text."""
    assert check_rm_command(command)[0]


def test_single_quoted_continuation_is_literal_data() -> None:
    """Single quotes prevent removal of an apparent line continuation."""
    assert not check_rm_command("echo '$\\\n(rm foo)'")[0]


@pytest.mark.parametrize("suffix", [
    ' "$((1 << 2))"',
    ' "$(echo case)"',
])
def test_fallback_preserves_earlier_substitution(suffix: str) -> None:
    """Fallback splitting cannot destroy substitutions earlier on the line."""
    assert check_rm_command('echo "$(rm \'a|b\')"' + suffix)[0]


def test_fallback_preserves_single_quoted_continuation() -> None:
    """A conservative fallback must not manufacture an executable marker."""
    assert not check_rm_command("echo '$\\\n(rm foo)' $((1 << 2))")[0]


@pytest.mark.parametrize("command", [
    "bash -c 'echo ok; rm foo'",
    '/bin/bash -lc "echo ok; rm foo"',
    "sh -c 'echo ok && rm foo'",
    "eval 'echo ok; rm foo'",
    "trap 'echo ok; rm foo' EXIT",
    "env bash -c 'echo ok; rm foo'",
    "command bash -c 'echo ok; rm foo'",
])
def test_literal_shell_payloads_are_inspected(command: str) -> None:
    """Quoted code passed to an evaluator remains executable code."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "bash -c \"printf '%s' 'a|rm|b'\"",
    "printf '%s' \"bash -c 'echo ok; rm foo'\"",
    "trap -p 'echo ok; rm foo'",
])
def test_evaluator_data_arguments_remain_literal(command: str) -> None:
    """Only an evaluator's executable payload receives recursive inspection."""
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "bash -c -e 'echo ok; rm foo'",
    "bash -c -- 'echo ok; rm foo'",
    "bash -oc posix 'echo ok; rm foo'",
    "2>/dev/null bash -c 'echo ok; rm foo'",
    "bash 2>/dev/null -c 'echo ok; rm foo'",
    "bash -c 2>/dev/null 'echo ok; rm foo'",
    "bash -c 'echo ok; rm foo' # unmatched '",
    "find /dev/null -exec bash -c 'echo ok; rm foo' \\;",
    "fd x -x bash -c 'echo ok; rm foo'",
])
def test_shell_payload_options_and_launchers(command: str) -> None:
    """Shell option ordering, redirects and launchers preserve executable code."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "if true; then bash -c 'echo ok; rm foo'; fi",
    "(bash -c 'echo ok; rm foo')",
    "bash 2>&1 -c 'echo ok; rm foo'",
    "! bash -c 'echo ok; rm foo'",
    "f() { bash -c 'echo ok; rm foo'; }; f",
])
def test_shell_control_and_descriptor_payloads(command: str) -> None:
    """Control syntax and descriptor duplication cannot hide quoted code."""
    assert check_rm_command(command)[0]


def test_descriptor_redirect_preserves_literal_payload_data() -> None:
    """An ampersand in a redirect must not fragment a literal code argument."""
    assert not check_rm_command("bash 2>&1 -c \"printf '%s' 'a|rm|b'\"")[0]


@pytest.mark.parametrize("command", [
    r"echo \>& rm foo",
    r"echo \<& rm foo",
    "foo-bar() { bash -c 'echo ok; rm foo'; }; foo-bar",
    "f ( ) { bash -c 'echo ok; rm foo'; }; f",
    "f( ) { bash -c 'echo ok; rm foo'; }; f",
])
def test_escaped_redirects_and_function_spacing(command: str) -> None:
    """Escaped data and legal function spellings retain command inspection."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "bash -s <<< 'echo ok; rm foo'",
    "source /dev/stdin <<< 'echo ok; rm foo'",
    "printf '%s' 'echo ok; rm foo' | bash",
    "/bin/tcsh -f -c 'echo ok; rm foo'",
])
def test_stdin_and_additional_shell_evaluators(command: str) -> None:
    """Executable input and other shell frontends retain legacy inspection."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'printf %s "echo ok; rm foo" |\nbash',
    'timeout 10 bash -c "echo ok; rm foo"',
])
def test_multiline_pipeline_and_timeout_payloads(command: str) -> None:
    """A physical newline and a timeout wrapper do not hide executable text."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "su -c 'echo ok; rm foo'",
    "ssh host 'echo ok; rm foo'",
    "printf '%s' 'echo ok; rm foo' | bash -c 'bash'",
])
def test_remote_evaluators_and_nested_stdin_consumers(command: str) -> None:
    """Remote evaluation and nested shells retain conservative inspection."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    'docker exec container sh -c "echo ok; rm foo"',
    'kubectl exec pod -- sh -c "echo ok; rm foo"',
    'busybox sh -c "echo ok; rm foo"',
])
def test_opaque_container_and_multicall_launchers(command: str) -> None:
    """Opaque launchers preserve legacy scanning of their executable strings."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "&>/dev/null rm foo",
    "&>>/dev/null /bin/rm foo",
])
def test_leading_combined_output_redirect_cannot_hide_rm(command: str) -> None:
    """Combined stdout/stderr redirects cannot become a command word."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "uv run --no-project --no-sync bash -c 'echo ok; rm foo'",
    "setsid bash -c 'echo ok; rm foo'",
    "arch -arm64 /bin/bash -c 'echo ok; rm foo'",
    "flock /tmp/lock bash -c 'echo ok; rm foo'",
    "chroot / bash -c 'echo ok; rm foo'",
    "watch -n 1 'echo ok; rm foo'",
    "parallel 'echo {}; rm foo' ::: one",
])
def test_unsupported_launchers_retain_legacy_scan(command: str) -> None:
    """Opaque launchers must preserve the protection provided by main."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "exec {fd}>/dev/null bash -c 'echo ok; rm foo'",
    "{out_fd}>>/dev/null bash -c 'echo ok; rm foo'",
    "bash {input_fd}</dev/null -c 'echo ok; rm foo'",
    "{fd}>/dev/null rm foo",
])
def test_named_descriptor_redirections_preserve_command(command: str) -> None:
    """A named descriptor belongs to the redirect, not the command words."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "exec {fd}>/dev/null bash -c \"printf '%s' 'a|rm|b'\"",
    "echo '{fd}' > /dev/null \"bash -c 'echo ok; rm foo'\"",
])
def test_named_descriptors_preserve_quoted_data(command: str) -> None:
    """Removing a real descriptor must leave ordinary quoted data literal."""
    assert not check_rm_command(command)[0]
