"""Literal shell code executed by evaluators and command launchers."""

import shlex

from shell_scan import _split_command_lines, _split_line_on_operators

from shell_wrappers import (
    _command_words, _strip_literal_redirections, _exec_command,
    _FD, _FD_VALUE, _FIND_VALUE, _takes_value,
)


_SHELLS = {'sh', 'bash', 'dash', 'zsh', 'ksh', 'csh', 'tcsh',
           'fish', 'ash', 'yash'}


def _launched_commands(words: list[str]) -> list[list[str]]:
    """Include literal child commands launched by find and fd exec options."""
    words = _command_words(words)
    if not words:
        return []
    commands = [words]
    program = words[0].rsplit('/', 1)[-1]
    index = 1
    while index < len(words) and (program == 'find' or program in _FD):
        word = words[index]
        if program in _FD and word == '--':
            break
        child = _exec_command(program, word, words[index + 1:])
        if child is not None:
            if program == 'find':
                end = next((i for i, token in enumerate(child)
                            if token == ';' or (token == '+' and i > 0
                                                and child[i - 1] == '{}')),
                           len(child))
                commands.extend(_launched_commands(child[:end]))
                index += end + 2
                continue
            commands.extend(_launched_commands(child))
            break
        if program == 'find' and word == '-fprintf':
            index += 2
        elif program == 'find' and word in _FIND_VALUE:
            index += 1
        elif program in _FD and _takes_value(word, _FD_VALUE):
            index += 1
        index += 1
    return commands


def _evaluator_payload(words: list[str]) -> str | None:
    """Return the literal executable argument of one shell evaluator."""
    name = words[0].rsplit('/', 1)[-1]
    arguments = words[1:]
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    if name == 'eval':
        return ' '.join(arguments)
    if name == 'trap':
        if arguments and arguments[0] not in ('-', '-p', '-l'):
            return arguments[0]
        return None
    if name not in _SHELLS:
        return None
    index = 0
    evaluates = False
    while index < len(arguments):
        option = arguments[index]
        if option == '--':
            index += 1
            break
        if option == '-' or not option.startswith(('-', '+')):
            break
        if option.startswith('--'):
            index += 2 if option in ('--rcfile', '--init-file') else 1
            continue
        evaluates |= option.startswith('-') and 'c' in option
        # Bash permits c before or after an o/O option in the same cluster.
        index += 2 if any(c in option[1:] for c in 'oO') else 1
    return arguments[index] if evaluates and index < len(arguments) else None


def literal_shell_payloads(command: str) -> list[str]:
    """Extract code arguments without interpreting ordinary quoted data.

    Args:
        command: A command fragment after quote-aware separator splitting.

    Returns:
        Literal code passed to shell evaluators, including through launchers.
    """
    try:
        words = shlex.split(_strip_literal_redirections(command))
    except ValueError:
        return []  # Conservative fragments can contain incomplete quotes.
    return [payload for child in _launched_commands(words)
            if (payload := _evaluator_payload(child))]


def needs_conservative_shell_scan(command: str) -> bool:
    """Keep legacy inspection when a shell may consume code from stdin/files."""
    try:
        words = shlex.split(_strip_literal_redirections(command))
    except ValueError:
        return False
    for child in _launched_commands(words):
        name = child[0].rsplit('/', 1)[-1]
        if name in {'ssh', 'su', 'docker', 'kubectl', 'busybox',
                    'uv', 'setsid', 'chroot', 'watch', 'parallel'}:
            return True  # Retain legacy inspection of opaque shell launchers.
        # Unknown launchers can still contain an explicit shell -c operand.
        # Preserve legacy inspection without enumerating every platform tool.
        if any(word.rsplit('/', 1)[-1] in _SHELLS
               and _evaluator_payload(child[index:]) is not None
               for index, word in enumerate(child[1:], start=1)):
            return True
        payload = _evaluator_payload(child)
        if payload and ('$(' in payload or '`' in payload):
            return True  # Generated shell code is not a literal payload.
        if name in _SHELLS | {'source', '.'} and payload is None:
            return True
        if payload and any(needs_conservative_shell_scan(part)
                           for line in _split_command_lines(payload)
                           for part in _split_line_on_operators(line)):
            return True  # Nested consumers inherit the outer pipeline's stdin.
    return False
