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


def extract_subcommands(command: str) -> list[str]:
    """
    Split compound bash command into individual subcommands.

    Splits on shell chaining operators:
        - && (AND)
        - || (OR)
        - ; (sequential)
        - | (pipe)
        - & (background)

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
    """
    if not command:
        return []
    subcommands = re.split(r'\s*(?:&&|\|\||[;&|])\s*', command)
    return [cmd.strip() for cmd in subcommands if cmd.strip()]


def _extract_balanced_paren_content(command: str, start_idx: int) -> str | None:
    """
    Extract content from balanced parentheses starting at given index.

    Given a string and the index of an opening '(', finds the matching
    closing ')' accounting for nested parentheses.

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

    depth = 0
    for i in range(start_idx, len(command)):
        if command[i] == '(':
            depth += 1
        elif command[i] == ')':
            depth -= 1
            if depth == 0:
                # Found the matching closing paren
                return command[start_idx + 1:i]

    # No matching closing paren found
    return None


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


def _heredoc_bounds(
        command: str, start: int) -> tuple[int, int, int, bool] | None:
    """
    Locate one heredoc introduced by the '<<' at the given index.

    Args:
        command: The full command string.
        start: Index of the '<' that starts the '<<' operator.

    Returns:
        Tuple (body_start, delimiter_start, body_end, quoted) where
        body_start/delimiter_start bound the heredoc body, body_end is just
        past the closing delimiter line, and quoted says whether the
        delimiter was quoted (so the shell performs no expansion in the
        body). Returns None when this is not a complete, simple heredoc.
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
        if character in "'\"":
            quoted = True
            end = command.find(character, index + 1)
            if end == -1:
                return None
            delimiter_parts.append(command[index + 1:end])
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
    body_start = command.find('\n', index)
    if body_start == -1:
        return None
    body_start += 1
    line_start = body_start
    while line_start <= len(command):
        line_end = command.find('\n', line_start)
        if line_end == -1:
            line_end = len(command)
        line = command[line_start:line_end].removesuffix('\r')
        if strip_tabs:
            line = line.lstrip('\t')
        if line == delimiter:
            body_end = min(line_end + 1, len(command))
            return body_start, line_start, body_end, quoted
        if line_end == len(command):
            return None
        line_start = line_end + 1
    return None


# A '#' only opens a comment at the start of a word, so it must follow
# whitespace or an operator (or start the command).
_COMMENT_PRECEDERS = ' \t\n;&|(<>'


def _end_of_arithmetic(command: str, start: int) -> int | None:
    """
    Return the index just past the '))' closing an arithmetic expansion.

    Args:
        command: The full command string.
        start: Index of the first '(' of the opening '((' .

    Returns:
        Index just past the matching close, or None when unbalanced.
    """
    depth = 0
    for index in range(start, len(command)):
        if command[index] == '(':
            depth += 1
        elif command[index] == ')':
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def strip_heredoc_bodies(command: str) -> tuple[str, list[str]]:
    """
    Blank heredoc bodies and return the bodies the shell still expands.

    A heredoc body is data, not code: the shell never runs its lines as
    commands. When the delimiter is quoted (<<'EOF', <<"EOF", <<\\EOF) the
    shell performs no expansion at all, so nothing in the body executes.
    With an unquoted delimiter (<<EOF) only the body's expansions -- $(...)
    and backticks -- execute.

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
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            index += 2
            continue
        if character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            index += 1
            continue
        if quote is None and character == '#' and (
                index == 0 or command[index - 1] in _COMMENT_PRECEDERS):
            # A comment runs to end of line, so any '<<' in it is text.
            newline = command.find('\n', index)
            if newline == -1:
                break
            index = newline + 1
            continue
        if quote is None and command.startswith('((', index):
            # '<<' inside $((1 << 2)) is a left shift, not a heredoc.
            arithmetic_end = _end_of_arithmetic(command, index)
            if arithmetic_end is not None:
                index = arithmetic_end
                continue
        if (quote is None and command.startswith('<<', index)
                and not command.startswith('<<<', index)):
            heredoc = _heredoc_bounds(command, index)
            if heredoc is not None:
                body_start, delimiter_start, body_end, quoted = heredoc
                pieces.append(command[copied_to:body_start])
                pieces.append(''.join(
                    '\n' if char == '\n' else ' '
                    for char in command[body_start:body_end]
                ))
                if not quoted:
                    expanded_bodies.append(
                        command[body_start:delimiter_start])
                copied_to = body_end
                index = body_end
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
