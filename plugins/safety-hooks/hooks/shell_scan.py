"""Quote-aware boundaries used by the shell command safety hooks."""

import re


def _split_command_lines(command: str) -> list[str]:
    """Split on unquoted newlines, removing backslash-newline continuations."""
    return _split_shell(command, operators=False)


def _split_line_on_operators(line: str) -> list[str]:
    """Split on shell operators outside quoted words and substitutions."""
    return _split_shell(line, operators=True)


def _split_shell(command: str, operators: bool) -> list[str]:
    """Split at one kind of shell separator while preserving quoted spans.

    Args:
        command: The shell text to split.
        operators: Split on operators when true, otherwise on newlines.

    Returns:
        Command fragments with their original quoting preserved.
    """
    parts: list[str] = []
    pieces: list[str] = []
    start = 0
    index = 0
    quote = None
    word_start = True
    while index < len(command):
        character = command[index]
        if quote is None and character == '#' and word_start:
            # Quotes in comments have no effect on the next command line.
            newline = command.find('\n', index)
            if newline == -1:
                break
            index = newline
            character = '\n'
        if character == '\\' and quote != "'":
            continuation = command[index:index + 2] == '\\\n'
            if continuation and not operators:
                pieces.append(command[start:index])
                start = index + 2
            elif not continuation:
                word_start = False
            index += 2
            continue
        if quote is None and command.startswith("$'", index):
            end = _end_of_single_quote(command, index + 1, escapes=True)
            index = len(command) if end is None else end + 1
            word_start = False
            continue
        if quote != "'" and (command.startswith('$(', index) or character == '`'):
            end = (_end_of_balanced(command, index + 1, '(')
                   if character == '$' else _end_of_backtick(command, index))
            span = command[index:end]
            if end is None or '<<' in span or r'\`' in span:
                # Heredoc bodies and escaped legacy backticks disagree with
                # the downstream substitution extractor's simple boundaries.
                remainder = ''.join(pieces) + command[start:]
                if not operators:
                    # Keep the remainder together so the operator pass can
                    # conservatively inspect it without losing quote context.
                    return parts + [remainder]
                return parts + re.split(r'&&|\|\||[;&|\n]', remainder)
            index = end
            word_start = False
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            word_start = False
        elif quote is None:
            width = 0
            if operators:
                if command.startswith(('&&', '||'), index):
                    width = 2
                elif character in ';&|':
                    width = 1
            elif character == '\n':
                width = 1
            if width:
                pieces.append(command[start:index])
                parts.append(''.join(pieces))
                pieces = []
                index += width
                start = index
                word_start = True
                continue
            word_start = character in ' \t\r\n;&|()<>'
        index += 1
    pieces.append(command[start:])
    parts.append(''.join(pieces))
    return parts


def _end_of_backtick(command: str, start: int) -> int | None:
    """Return the index just past the closing unescaped backtick."""
    index = start + 1
    while index < len(command):
        if command[index] == '\\':
            index += 2
            continue
        if command[index] == '`':
            return index + 1
        index += 1
    return None


def _end_of_single_quote(command: str, start: int, escapes: bool) -> int | None:
    """Return the closing quote index, honoring ANSI-C escapes when requested."""
    quote_character = command[start]
    index = start + 1
    while index < len(command):
        if escapes and command[index] == '\\':
            index += 2
            continue
        if command[index] == quote_character:
            return index
        index += 1
    return None


def _end_of_balanced(command: str, start: int, opener: str) -> int | None:
    """Return the index after a balanced bracket, or None when unbalanced.

    Args:
        command: The full shell text.
        start: Index of the opening bracket.
        opener: The bracket character: '(', '{', or '['.

    Returns:
        Index after the matching close. Nested substitutions maintain their
        own quote state, including when embedded inside double quotes.
    """
    closer = {'(': ')', '{': '}', '[': ']'}[opener]
    depth = 0
    index = start
    quote = None
    word_start = True
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            if command[index:index + 2] != '\\\n':
                word_start = False
            index += 2
            continue
        if quote is None and character == '#' and word_start and opener == '(':
            newline = command.find('\n', index)
            if newline == -1:
                return None
            index = newline
            continue
        if quote is None and command.startswith("$'", index):
            end = _end_of_single_quote(command, index + 1, escapes=True)
            if end is None:
                return None
            index = end + 1
            word_start = False
            continue
        if quote != "'" and command.startswith(('$(', '${'), index):
            end = _end_of_balanced(command, index + 1, command[index + 1])
            if end is None:
                return None
            index = end
            word_start = False
            continue
        if quote != "'" and character == '`':
            end = _end_of_backtick(command, index)
            if end is None:
                return None
            index = end
            word_start = False
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            word_start = False
        elif quote is None:
            if character == opener:
                depth += 1
            elif character == closer:
                depth -= 1
                if depth == 0:
                    return index + 1
            word_start = character in ' \t\r\n;&|()<>'
        index += 1
    return None
