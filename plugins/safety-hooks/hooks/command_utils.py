"""Shared utilities for bash command parsing."""
import os
import re
import subprocess

# Cache for alias expansions (populated on first use)
_alias_cache: dict[str, str] | None = None


def _load_alias_cache() -> dict[str, str]:
    """
    Load all shell aliases into a cache dict.

    Sources the shell rc file and runs 'alias' to get all aliases.
    Avoids -i (interactive) flag to prevent TTY issues when run as
    a background process by Claude Code.
    Returns empty dict on failure.
    """
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache

    _alias_cache = {}
    shell = os.environ.get('SHELL', '/bin/bash')

    try:
        # Avoid -i (interactive) flag which can cause TTY issues
        # Source rc file explicitly to get aliases without interactive mode
        if 'zsh' in shell:
            cmd = [shell, '-c', 'source ~/.zshrc 2>/dev/null; alias']
        else:
            cmd = [shell, '-c', 'source ~/.bashrc 2>/dev/null; alias']

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,  # Explicitly close stdin
            start_new_session=True,  # Isolate from terminal control
            env={**os.environ, 'PS1': '', 'TERM': 'dumb'},
        )
        output = result.stdout

        # Strip ANSI escape sequences
        output = re.sub(r'\x1b\][^\x07]*\x07', '', output)
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)

        # Parse alias output - handles both bash and zsh formats:
        # bash: alias gcam='git commit -am'
        # zsh:  gcam='git commit -a -m' or gcam="git commit -a -m"
        for line in output.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # Remove leading 'alias ' if present (bash format)
            if line.startswith('alias '):
                line = line[6:]
            # Parse name=value
            if '=' in line:
                name, _, value = line.partition('=')
                name = name.strip()
                value = value.strip()
                # Remove surrounding quotes
                if (value.startswith("'") and value.endswith("'")) or \
                   (value.startswith('"') and value.endswith('"')):
                    value = value[1:-1]
                if name:
                    _alias_cache[name] = value
    except Exception:
        pass  # Fail silently, return empty cache

    return _alias_cache


def expand_alias(command: str) -> str:
    """
    Expand shell alias in the first token of a command.

    Uses cached alias lookups for performance. The cache is populated
    once per hook invocation by sourcing the shell rc file.

    Args:
        command: A single bash command (not compound).

    Returns:
        Command with first token expanded if it's an alias,
        otherwise the original command unchanged.

    Example:
        >>> # With alias gco='git checkout'
        >>> expand_alias("gco -f")
        'git checkout -f'
    """
    parts = command.split(None, 1)  # Split into [first_token, rest]
    if not parts:
        return command

    first_token = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Skip if already a known command or path
    if first_token in ('git', 'rm', 'cat', 'less', 'nano', 'vim') or '/' in first_token:
        return command

    # Look up in alias cache
    alias_cache = _load_alias_cache()
    if first_token in alias_cache:
        expansion = alias_cache[first_token]
        return f"{expansion} {rest}".strip()

    return command


def expand_command_aliases(command: str) -> str:
    """
    Expand aliases in a possibly compound bash command.

    Splits compound command on shell operators, expands each subcommand's
    alias, and reconstructs the command.

    Recognized operators:
        - && (AND)
        - || (OR)
        - ; (sequential)
        - | (pipe)
        - & (background)

    Args:
        command: A bash command string, possibly compound.

    Returns:
        Command with aliases expanded in each subcommand.

    Example:
        >>> # With alias gco='git checkout', gcam='git commit -am'
        >>> expand_command_aliases("gco -f && gcam 'msg'")
        "git checkout -f && git commit -am 'msg'"
    """
    if not command:
        return command

    # Find the operators and their positions to preserve them.
    # This regex captures the operators as well as the commands.
    # Multi-character operators (&&, ||) must come before single-character
    # variants ([;&|]) to prevent partial matching.
    parts = re.split(r'(\s*(?:&&|\|\||[;&|])\s*)', command)

    result = []
    for part in parts:
        # Check if this part is an operator
        if re.match(r'\s*(?:&&|\|\||[;&|])\s*', part):
            result.append(part)
        elif part.strip():
            # It's a command, expand its alias
            result.append(expand_alias(part.strip()))
        else:
            result.append(part)

    return ''.join(result)


def _split_command_lines(command: str) -> list[str]:
    """
    Split on the newlines the shell treats as command separators.

    A newline ends a command like ';' does, but only outside quotes and
    when it is not escaped: a backslash-newline continues the line, and a
    newline inside a quoted word is part of that word.

    Args:
        command: A bash command string, possibly spanning several lines.

    Returns:
        The command lines, without the separating newlines. A continued
        line is returned joined, with the backslash-newline removed the
        way the shell removes it, so the command it continues into is
        recognisable ("cat <<EOF ; \\\\\\n rm x" ends up as one line).
    """
    lines = []
    pieces = []
    line_start = 0
    index = 0
    quote = None
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            if index + 1 < len(command) and command[index + 1] == '\n':
                # A backslash-newline continues the line; the shell removes
                # both characters, so neither is part of any command.
                pieces.append(command[line_start:index])
                line_start = index + 2
            index += 2
            continue
        if quote is None and command.startswith("$'", index):
            # ANSI-C quoting: a backslash escapes the closing quote.
            end = _end_of_single_quote(command, index + 1, escapes=True)
            index = len(command) if end is None else end + 1
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
        elif quote is None and character == '\n':
            pieces.append(command[line_start:index])
            lines.append(''.join(pieces))
            pieces = []
            line_start = index + 1
        index += 1
    pieces.append(command[line_start:])
    lines.append(''.join(pieces))
    return lines


def extract_subcommands(command: str) -> list[str]:
    """
    Split compound bash command into individual subcommands.

    Splits on shell chaining operators:
        - && (AND)
        - || (OR)
        - ; (sequential)
        - | (pipe)
        - & (background)
        - a newline outside quotes, which ends a command just as ';' does

    Multi-character operators (&&, ||) are matched before single-character
    variants to prevent partial matching (e.g., '&&' won't be split as '&' + '&').

    Args:
        command: A bash command string, possibly compound.

    Returns:
        List of individual subcommands with whitespace stripped.

    Example:
        >>> extract_subcommands("cd /tmp && git add . && git commit -m 'msg'")
        ['cd /tmp', 'git add .', "git commit -m 'msg'"]
        >>> extract_subcommands("echo ok | rm foo")
        ['echo ok', 'rm foo']
        >>> extract_subcommands("sleep 1 & rm bar")
        ['sleep 1', 'rm bar']
        >>> extract_subcommands("echo ok\\nrm foo")
        ['echo ok', 'rm foo']
    """
    if not command:
        return []
    subcommands = []
    for line in _split_command_lines(command):
        subcommands.extend(re.split(r'\s*(?:&&|\|\||[;&|])\s*', line))
    return [cmd.strip() for cmd in subcommands if cmd.strip()]


def _extract_balanced_paren_content(command: str, start_idx: int) -> str | None:
    """
    Extract content from balanced parentheses starting at given index.

    Given a string and the index of an opening '(', finds the matching
    closing ')' accounting for nested parentheses. A ')' inside quotes is
    text, not the closing one: stopping at it would cut the substitution
    short and hide the commands after it, as in "$(printf ')'; rm foo)".

    Args:
        command: The full command string.
        start_idx: Index of the opening '(' character.

    Returns:
        The content between the balanced parentheses (excluding the parens
        themselves), or None if no balanced closing paren is found.

    Example:
        >>> _extract_balanced_paren_content("$(echo $(rm foo))", 1)
        'echo $(rm foo)'
    """
    if start_idx >= len(command) or command[start_idx] != '(':
        return None

    end = _end_of_balanced(command, start_idx, '(')
    if end is None:
        return None
    return command[start_idx + 1:end - 1]


def extract_subshell_commands(command: str) -> list[str]:
    """
    Extract commands embedded in subshells from a bash command string.

    Detects and extracts commands from:
        - $(...) command substitution (modern syntax, handles nesting)
        - `...` backtick command substitution (legacy syntax)

    This is a security measure to detect dangerous commands hidden inside
    subshells, e.g., `echo $(rm -rf /)` or `echo \`rm foo\``.

    Uses balanced parenthesis scanning to correctly handle nested $()
    subshells like `$(echo $(rm foo))`.

    Args:
        command: A bash command string that may contain subshells.

    Returns:
        List of commands found inside subshells. Returns empty list if
        no subshells are found.

    Example:
        >>> extract_subshell_commands("echo $(whoami)")
        ['whoami']
        >>> extract_subshell_commands("echo `rm foo` bar")
        ['rm foo']
        >>> extract_subshell_commands("$(cat file) | $(rm -rf /)")
        ['cat file', 'rm -rf /']
        >>> extract_subshell_commands("echo $(echo $(rm foo))")
        ['echo $(rm foo)']
    """
    if not command:
        return []

    subshell_commands = []

    # Extract from $(...) - modern command substitution
    # Use balanced parenthesis scanning to handle nested subshells
    i = 0
    while i < len(command) - 1:
        if command[i:i+2] == '$(':
            # Found start of $(), extract balanced content
            inner_cmd = _extract_balanced_paren_content(command, i + 1)
            if inner_cmd is not None:
                inner_cmd = inner_cmd.strip()
                if inner_cmd:
                    subshell_commands.append(inner_cmd)
                # Skip past this subshell to avoid re-matching nested ones
                # at the top level (they'll be found via recursion)
                i += 2 + len(inner_cmd) + 1  # $( + content + )
                continue
        i += 1

    # Extract from `...` - backtick command substitution (legacy syntax)
    # Backticks cannot be nested, so a simple pattern works
    backtick_pattern = r'`([^`]+)`'
    for match in re.finditer(backtick_pattern, command):
        inner_cmd = match.group(1).strip()
        if inner_cmd:
            subshell_commands.append(inner_cmd)

    return subshell_commands


def _end_of_single_quote(command: str, start: int, escapes: bool) -> int | None:
    """
    Return the index of the quote closing the one at start.

    Args:
        command: The full command string.
        start: Index of the opening quote character.
        escapes: Whether backslash escapes the quote inside this span, as
            it does in ANSI-C quoting ($'...') but not in plain '...'.

    Returns:
        Index of the closing quote, or None when it is unterminated.
    """
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


def _heredoc_delimiter(
        command: str, start: int) -> tuple[str, bool, bool, int] | None:
    """
    Parse the delimiter word of the '<<' operator at the given index.

    Args:
        command: The full command string.
        start: Index of the '<' that starts the '<<' operator.

    Returns:
        Tuple (delimiter, quoted, strip_tabs, end) where delimiter is the
        word after quote removal, quoted says whether any part of it was
        quoted (so the shell performs no expansion in the body),
        strip_tabs reflects the '<<-' form, and end is the index just past
        the delimiter word. Returns None when no delimiter word is there.
    """
    index = start + 2
    strip_tabs = index < len(command) and command[index] == '-'
    if strip_tabs:
        index += 1
    while index < len(command) and command[index] in ' \t':
        index += 1
    delimiter_parts = []
    quoted = False
    while index < len(command) and command[index] not in ' \t\r\n;&|<>()':
        character = command[index]
        if (character == '$' and index + 1 < len(command)
                and command[index + 1] in "'\""):
            # $'EOF' and $"EOF" quote the delimiter; the '$' is not part
            # of it, so bash ends the body at EOF, not at $EOF.
            ansi_c = command[index + 1] == "'"
            end = _end_of_single_quote(command, index + 1, escapes=ansi_c)
            if end is None:
                return None
            content = command[index + 2:end]
            if '\\' in content:
                # Both forms unescape: $'...' decodes ANSI-C escapes and
                # $"..." removes double-quote ones. Rather than replicate
                # that, report no heredoc, which hands the body to the
                # guards as commands.
                return None
            quoted = True
            delimiter_parts.append(content)
            index = end + 1
        elif (command.startswith('$(', index)
                or command.startswith('${', index)):
            # No expansion happens on a delimiter word, so "$(echo EOF)" is
            # the delimiter, literally, parentheses and all.
            opener = '(' if command[index + 1] == '(' else '{'
            end = _end_of_balanced(command, index + 1, opener)
            if end is None:
                return None
            delimiter_parts.append(command[index:end])
            index = end
        elif character == '`':
            # A backtick span is part of the word, and just as literal.
            end = command.find('`', index + 1)
            if end == -1:
                return None
            delimiter_parts.append(command[index:end + 1])
            index = end + 1
        elif character in "'\"":
            quoted = True
            end = _end_of_single_quote(command, index, escapes=False)
            if end is None:
                return None
            content = command[index + 1:end]
            if character == '"' and '\\' in content:
                # Quote removal would drop backslashes here. Rather than
                # replicate that, report no heredoc: nothing is blanked and
                # the guards see the body, which is the safe direction.
                return None
            delimiter_parts.append(content)
            index = end + 1
        elif character == '\\':
            if index + 1 >= len(command):
                return None
            quoted = True
            delimiter_parts.append(command[index + 1])
            index += 2
        else:
            delimiter_parts.append(character)
            index += 1
    delimiter = ''.join(delimiter_parts)
    if not delimiter:
        return None
    return delimiter, quoted, strip_tabs, index


def _ends_with_continuation(line: str) -> bool:
    """
    Report whether a physical line ends in a backslash-newline pair.

    Args:
        line: One physical line, without its newline.

    Returns:
        True when the line ends with an odd number of backslashes, so the
        last one escapes the newline instead of itself.
    """
    return (len(line) - len(line.rstrip('\\'))) % 2 == 1


def _end_of_heredoc_body(
        command: str, start: int, delimiter: str, strip_tabs: bool,
        join_continuations: bool) -> tuple[int, int] | None:
    """
    Find the closing delimiter line of one heredoc body.

    Args:
        command: The full command string.
        start: Index where the body begins (just past a newline).
        delimiter: The delimiter word that ends the body.
        strip_tabs: Whether the '<<-' form allows leading tabs on it.
        join_continuations: Whether backslash-newline pairs are removed
            while reading the body, as they are for an unquoted delimiter.
            A delimiter can then be split over two lines ("EO\\" + "F").

    Returns:
        Tuple (delimiter_line_start, body_end): the index where the line
        holding the delimiter begins -- which is where the body ends, and
        with '<<-' may be a leading tab rather than the delimiter itself
        -- and the index just past that line. None when the body is never
        closed.
    """
    line_start = start
    while line_start <= len(command):
        # Read one logical line, which may span several physical ones.
        parts = []
        scan = line_start
        while True:
            line_end = command.find('\n', scan)
            if line_end == -1:
                line_end = len(command)
            part = command[scan:line_end].removesuffix('\r')
            if (join_continuations and line_end < len(command)
                    and _ends_with_continuation(part)):
                parts.append(part[:-1])
                scan = line_end + 1
                continue
            parts.append(part)
            break
        line = ''.join(parts)
        if strip_tabs:
            line = line.lstrip('\t')
        if line == delimiter:
            return line_start, min(line_end + 1, len(command))
        if line_end == len(command):
            return None
        line_start = line_end + 1
    return None


def _heredoc_bodies(
        command: str, start: int,
        pending: list[tuple[str, bool, bool, int]]) -> tuple[int, list[str]] | None:
    """
    Consume the bodies of every heredoc declared on one command line.

    A command line may open several heredocs ("cat <<A <<'B'"); their
    bodies follow the line in declaration order, one after another. Taking
    them one at a time would read the second body as shell syntax.

    Args:
        command: The full command string.
        start: Index where the first body begins (just past the newline
            that ends the command line).
        pending: The (delimiter, quoted, strip_tabs, depth) tuples in the
            order the heredocs were declared; depth is unused here.

    Returns:
        Tuple (end, expanded_bodies): the index just past the last body's
        delimiter line, and the bodies whose expansions the shell runs
        (those with an unquoted delimiter). None when any body is
        unterminated, in which case no body is treated as data.
    """
    cursor = start
    expanded_bodies = []
    for delimiter, quoted, strip_tabs, _ in pending:
        bounds = _end_of_heredoc_body(
            command, cursor, delimiter, strip_tabs,
            join_continuations=not quoted)
        if bounds is None:
            return None
        delimiter_line_start, body_end = bounds
        if not quoted:
            expanded_bodies.append(command[cursor:delimiter_line_start])
        cursor = body_end
    return cursor, expanded_bodies


# A '#' only opens a comment at the start of a word, so it must follow
# whitespace or an operator (or start the command). ')' is included because
# '(echo x)# comment' is a comment; the cost is that the rarer '$(date)#tag',
# where '#' is mid-word, is misread as one -- which only ever makes this
# function blank less, i.e. hand MORE text to the guards.
_COMMENT_PRECEDERS = ' \t\n;&|(<>)'


def _end_of_balanced(command: str, start: int, opener: str) -> int | None:
    """
    Return the index just past the bracket balancing the one at start.

    Used to step over a span whose contents are not commands: an arithmetic
    expansion $((...)), a parameter expansion ${...} or an array subscript
    a[...]. A '<<' inside any of them is text or a shift, not a heredoc
    operator.

    Args:
        command: The full command string.
        start: Index of the opening bracket.
        opener: The opening bracket character, '(', '{' or '['.

    Returns:
        Index just past the matching close, or None when unbalanced.
    """
    closer = {'(': ')', '{': '}', '[': ']'}[opener]
    depth = 0
    index = start
    quote = None
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            # An escaped brace is expansion text, not the closing one.
            index += 2
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            index += 1
            continue
        if quote is None:
            if character == opener:
                depth += 1
            elif character == closer:
                depth -= 1
                if depth == 0:
                    return index + 1
        index += 1
    return None


def strip_heredoc_bodies(command: str) -> tuple[str, list[str]]:
    """
    Blank heredoc bodies and return the bodies the shell still expands.

    A heredoc body is data, not code: the shell never runs its lines as
    commands. When the delimiter is quoted (<<'EOF', <<"EOF", <<\\EOF) the
    shell performs no expansion at all, so nothing in the body executes.
    With an unquoted delimiter (<<EOF) only the body's expansions -- $(...)
    and backticks -- execute.

    Bodies begin after the command line that opens them, which is not the
    same as the next newline: a backslash-newline continues that line, and
    a newline inside quotes or inside an unfinished command substitution
    does not end it. Every heredoc opened on one line is consumed, in
    order, and a command substitution is its own parsing unit with its own
    bodies.

    Args:
        command: A bash command string, possibly containing heredocs.

    Returns:
        Tuple (command_without_bodies, expanded_bodies). The first element
        is the command with every heredoc body (and its closing delimiter
        line) replaced by whitespace, preserving offsets and line breaks.
        The second is the list of bodies whose expansions do execute.

    Example:
        >>> strip_heredoc_bodies("cat <<'EOF'\\n`rm x`\\nEOF")[1]
        []
        >>> strip_heredoc_bodies("cat <<EOF\\n`rm x`\\nEOF")[1]
        ['`rm x`\\n']
    """
    expanded_bodies = []
    pieces = []
    copied_to = 0
    index = 0
    quote = None
    # A command substitution is parsed as its own unit, so it has its own
    # command lines and its own heredoc bodies. Track how deep we are in
    # one, and remember the depth each heredoc was opened at. Grouping
    # parentheses are tracked too, so that the ')' of a nested group is not
    # mistaken for the one closing the substitution.
    open_parens: list[str] = []
    depth = 0
    in_backtick = False
    # Heredocs opened on the command line now being scanned. Their bodies
    # all follow that line, in the order the operators appeared.
    pending: list[tuple[str, bool, bool, int]] = []
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            # A backslash-newline continues the command line, so the body
            # does not start here; skipping the pair keeps that newline
            # out of the body-start decision below.
            index += 2
            continue
        if quote is None and command.startswith("$'", index):
            # ANSI-C quoting: a backslash escapes the closing quote.
            end = _end_of_single_quote(command, index + 1, escapes=True)
            index = len(command) if end is None else end + 1
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            index += 1
            continue
        if quote is None and (
                (command.startswith('$(', index)
                 and not command.startswith('$((', index))
                or command.startswith('<(', index)
                or command.startswith('>(', index)):
            # A command substitution $( ), or a process substitution <( )
            # or >( ): each is parsed as its own unit, so a newline inside
            # one does not end the command line that contains it.
            open_parens.append('$(')
            depth += 1
            index += 2
            continue
        if quote is None and character == '`':
            # Backticks cannot nest, so one toggles the substitution.
            depth += -1 if in_backtick else 1
            in_backtick = not in_backtick
            index += 1
            continue
        if quote is None and character == ')' and open_parens:
            if open_parens.pop() == '$(':
                depth -= 1
                # A heredoc whose substitution closed before any newline
                # never gets a body: bash reads it from inside there.
                pending = [item for item in pending if item[3] <= depth]
            index += 1
            continue
        if quote is None and character == '\n' and pending:
            if pending[0][3] != depth:
                # This newline is inside a deeper command substitution, so
                # it does not end the line that opened these heredocs.
                index += 1
                continue
            # The command line ended: its heredoc bodies start here.
            bodies = _heredoc_bodies(command, index + 1, pending)
            pending = []
            if bodies is None:
                index += 1
                continue
            body_end, expanded = bodies
            pieces.append(command[copied_to:index + 1])
            pieces.append(''.join(
                '\n' if char == '\n' else ' '
                for char in command[index + 1:body_end]
            ))
            expanded_bodies.extend(expanded)
            copied_to = body_end
            index = body_end
            continue
        if quote is None and character == '#' and (
                index == 0 or command[index - 1] in _COMMENT_PRECEDERS):
            # A comment runs to end of line, so any '<<' in it is text.
            newline = command.find('\n', index)
            if newline == -1:
                break
            # Stop on the newline itself: it may still end a command line
            # whose heredoc bodies start right after the comment.
            index = newline
            continue
        if quote is None and command.startswith('((', index):
            # '<<' inside $((1 << 2)) is a left shift, not a heredoc.
            arithmetic_end = _end_of_balanced(command, index, '(')
            if arithmetic_end is not None:
                index = arithmetic_end
                continue
        if quote is None and command.startswith('${', index):
            # '<<' inside ${x:-<<EOF} is expansion text, not a heredoc.
            expansion_end = _end_of_balanced(command, index + 1, '{')
            if expansion_end is not None:
                index = expansion_end
                continue
        if quote is None and command.startswith('$[', index):
            # $[1 << 2] is the deprecated arithmetic form, still a shift.
            legacy_end = _end_of_balanced(command, index + 1, '[')
            if legacy_end is not None:
                index = legacy_end
                continue
        if quote is None and character == '(':
            # A grouping or subshell paren. It is not a parsing unit of its
            # own, but it has to be balanced so that its ')' is not taken
            # for the one closing a substitution. Checked after the
            # arithmetic and expansion skips above, which own their parens.
            open_parens.append('(')
            index += 1
            continue
        if (quote is None and character == '[' and index > 0
                and (command[index - 1].isalnum()
                     or command[index - 1] == '_')):
            # An array subscript is an arithmetic context, so the '<<' in
            # "a[1<<2]=foo" is a shift. A '[' that does not follow a name
            # is the test command or a glob, and is left alone.
            subscript_end = _end_of_balanced(command, index, '[')
            if subscript_end is not None:
                index = subscript_end
                continue
        if (quote is None and command.startswith('<<', index)
                and not command.startswith('<<<', index)):
            delimiter = _heredoc_delimiter(command, index)
            if delimiter is not None:
                word, quoted, strip_tabs, end = delimiter
                pending.append((word, quoted, strip_tabs, depth))
                index = end
                continue
        index += 1
    pieces.append(command[copied_to:])
    return ''.join(pieces), expanded_bodies


def extract_all_commands(command: str) -> list[str]:
    """
    Recursively extract all commands from a bash command string.

    This combines subcommand extraction (splitting on shell operators)
    with subshell extraction (commands inside $() or backticks) to
    provide a comprehensive list of all commands that will be executed.

    This is the recommended function for security hooks that need to
    inspect all commands, including those hidden in subshells.

    Heredoc bodies are treated as the data they are: a quoted delimiter
    (<<'EOF') means nothing in the body executes, and an unquoted one
    (<<EOF) means only the body's $() and backtick expansions execute.

    Args:
        command: A bash command string, possibly compound with subshells.

    Returns:
        List of all individual commands, including those from subshells.

    Example:
        >>> extract_all_commands("echo $(rm foo) && ls")
        ['echo $(rm foo)', 'ls', 'rm foo']
        >>> extract_all_commands("cat `echo secret` | grep pass")
        ['cat `echo secret`', 'grep pass', 'echo secret']
        >>> extract_all_commands("cat > f <<'EOF'\\n`rm x` is blocked\\nEOF")
        ["cat > f <<'EOF'"]
    """
    if not command:
        return []

    # Heredoc bodies are data, so their lines are not commands. Blank them
    # out first; expanded_bodies holds the bodies (unquoted delimiters only)
    # whose substitutions the shell does run.
    stripped, expanded_bodies = strip_heredoc_bodies(command)

    all_commands = []

    # First, extract top-level subcommands (split on operators)
    subcommands = extract_subcommands(stripped)
    all_commands.extend(subcommands)

    # Then, extract commands from subshells within the original command
    subshell_cmds = extract_subshell_commands(stripped)

    # Only the substitutions inside an unquoted heredoc body execute; the
    # surrounding text is literal, so it is not split into subcommands.
    for body in expanded_bodies:
        subshell_cmds.extend(extract_subshell_commands(body))

    # Recursively process subshell commands (they may contain nested subshells
    # or chained operators)
    for subcmd in subshell_cmds:
        all_commands.extend(extract_all_commands(subcmd))

    return all_commands
