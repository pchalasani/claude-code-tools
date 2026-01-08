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

    Splits compound command, expands each subcommand's alias,
    and reconstructs the command.

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

    # Find the operators and their positions to preserve them
    # This regex captures the operators as well as the commands
    parts = re.split(r'(\s*(?:&&|\|\||;)\s*)', command)

    result = []
    for part in parts:
        # Check if this part is an operator
        if re.match(r'\s*(?:&&|\|\||;)\s*', part):
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

    Splits on &&, ||, and ; operators.

    Args:
        command: A bash command string, possibly compound.

    Returns:
        List of individual subcommands.

    Example:
        >>> extract_subcommands("cd /tmp && git add . && git commit -m 'msg'")
        ['cd /tmp', 'git add .', "git commit -m 'msg'"]
    """
    if not command:
        return []
    subcommands = re.split(r'\s*(?:&&|\|\||;)\s*', command)
    return [cmd.strip() for cmd in subcommands if cmd.strip()]
