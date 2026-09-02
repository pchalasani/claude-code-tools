#!/usr/bin/env python3
"""
Git commit hook that asks for user permission before allowing commits.
Uses the "ask" decision type to prompt user in the UI.
"""
import json
import os
import re
import shlex
import sys

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

# git's own global options that consume the NEXT token as their value. They sit
# between "git" and the subcommand, which is why the subcommand is not always
# argv[1] and a "starts with git commit" test is not enough.
_GIT_GLOBAL_VALUE_OPTS = {
    "-C",
    "-c",
    "--attr-source",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}

# Global flags that Git accepts before a subcommand without consuming a value.
_GIT_GLOBAL_FLAGS = {
    "-p",
    "-P",
    "--bare",
    "--glob-pathspecs",
    "--icase-pathspecs",
    "--literal-pathspecs",
    "--no-advice",
    "--no-lazy-fetch",
    "--no-optional-locks",
    "--no-pager",
    "--no-replace-objects",
    "--noglob-pathspecs",
    "--paginate",
}

# These options complete the Git invocation rather than preceding a subcommand.
_GIT_TERMINAL_OPTS = {
    "-h",
    "-v",
    "--help",
    "--html-path",
    "--info-path",
    "--man-path",
    "--version",
}


# Subcommands that write a commit object. `commit-tree` is included because
# the previous `startswith('git commit')` test matched it, and prompting for it
# is consistent with this hook's purpose.
COMMIT_SUBCOMMANDS = ('commit', 'commit-tree')
_MAX_SUBSTITUTION_DEPTH = 12
_ENV_VALUE_OPTS = {"-C", "-P", "-S", "-u", "--argv0", "--chdir", "--unset"}
_ENV_SHORT_VALUE_OPTS = frozenset("uCPS")


def _is_assignment(token: str) -> bool:
    """Return whether a token is a simple shell assignment word."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token))


def _env_short_value(token: str) -> tuple[str, str | None] | None:
    """Return the first value-taking option in a short ``env`` cluster."""
    if not token.startswith("-") or token.startswith("--"):
        return None
    for index, option in enumerate(token[1:]):
        if option in _ENV_SHORT_VALUE_OPTS:
            return f"-{option}", token[index + 2:] or None
    return None


def _expand_env_split(tokens: list[str]) -> list[str] | None:
    """Expand an ``env -S`` command string into its effective argv."""
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return tokens

    index += 1
    while index < len(tokens):
        token = tokens[index]
        value: str | None = None
        consumed = 1
        if token in ("-S", "--split-string"):
            if index + 1 >= len(tokens):
                return None
            value = tokens[index + 1]
            consumed = 2
        elif token.startswith("--split-string="):
            value = token.split("=", 1)[1]
        elif short_value := _env_short_value(token):
            option, value = short_value
            if value is None:
                if index + 1 >= len(tokens):
                    return None
                value = tokens[index + 1]
                consumed = 2
            if option != "-S":
                index += consumed
                continue
        if value is not None:
            try:
                expanded = shlex.split(value)
            except ValueError:
                return None
            return tokens[:index] + expanded + tokens[index + consumed:]
        if _is_assignment(token):
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return tokens


def _skip_command_prefix(tokens: list[str]) -> int | None:
    """Return the executable index after assignments and a simple env."""
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return index

    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in _ENV_VALUE_OPTS:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if token.startswith(("--argv0=", "--chdir=", "--unset=")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    return index


def _dollar_substitution_end(command: str, start: int) -> int | None:
    """Return the closing parenthesis index for a ``$(`` substitution."""
    depth = 1
    quote: str | None = None
    index = start
    while index < len(command):
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        elif quote is None:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return None


def _backtick_substitution_end(command: str, start: int) -> int | None:
    """Return the closing index for an executable backtick substitution."""
    index = start
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == "`":
            return index
        index += 1
    return None


def _heredoc_body(
    command: str,
    start: int,
) -> tuple[int, int, bool] | None:
    """Return the body bounds and quoting state for a simple heredoc."""
    index = start + 2
    strip_tabs = index < len(command) and command[index] == "-"
    if strip_tabs:
        index += 1
    while index < len(command) and command[index] in " \t":
        index += 1
    delimiter_parts: list[str] = []
    quoted = False
    while index < len(command) and command[index] not in " \t\r\n;&|<>()":
        character = command[index]
        if character in {"'", '"'}:
            quoted = True
            end = command.find(character, index + 1)
            if end == -1:
                return None
            delimiter_parts.append(command[index + 1:end])
            index = end + 1
        elif character == "\\":
            if index + 1 >= len(command):
                return None
            quoted = True
            delimiter_parts.append(command[index + 1])
            index += 2
        else:
            delimiter_parts.append(character)
            index += 1
    delimiter = "".join(delimiter_parts)
    if not delimiter:
        return None
    body_start = command.find("\n", index)
    if body_start == -1:
        return None
    body_start += 1
    line_start = body_start
    while line_start <= len(command):
        line_end = command.find("\n", line_start)
        if line_end == -1:
            line_end = len(command)
        line = command[line_start:line_end].removesuffix("\r")
        if strip_tabs:
            line = line.lstrip("\t")
        if line == delimiter:
            return body_start, min(line_end + 1, len(command)), quoted
        if line_end == len(command):
            return None
        line_start = line_end + 1
    return None


def _heredoc_substitution_commands(body: str, depth: int) -> list[str]:
    """Extract executable substitutions from an unquoted heredoc body."""
    commands: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "\\":
            index += 2
            continue
        if body.startswith("$(", index):
            end = _dollar_substitution_end(body, index + 2)
            content_start = index + 2
        elif body[index] == "`":
            end = _backtick_substitution_end(body, index + 1)
            content_start = index + 1
        else:
            index += 1
            continue
        if end is None:
            index += 1
            continue
        content = body[content_start:end]
        if depth >= _MAX_SUBSTITUTION_DEPTH:
            if _commit_at_nesting_limit(content):
                commands.append("git commit")
        else:
            commands.extend(_split_shell_commands(content, depth + 1))
        index = end + 1
    return commands


def _commit_at_nesting_limit(command: str) -> bool:
    """Conservatively find an unquoted commit at a shell command boundary."""
    index = 0
    quote: str | None = None
    command_start = True
    while index < len(command):
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            index += 1
            continue
        if quote is None:
            if command_start and not character.isspace():
                if git_subcommand(command[index:]) in COMMIT_SUBCOMMANDS:
                    return True
                command_start = character in "(<>{"
            elif character in ";&|\r\n(":
                command_start = True
        index += 1
    return False


def _split_shell_commands(command: str, depth: int = 0) -> list[str]:
    """Split a shell command on unquoted chaining operators.

    This intentionally implements only the local behavior needed by this hook.
    In particular, operators in quoted Git option values and escaped operators
    stay within the command that contains them.

    Args:
        command: A shell command that may contain compound commands.

    Returns:
        Nonempty command segments with surrounding whitespace removed.
    """
    commands: list[str] = []
    start = 0
    index = 0
    quote: str | None = None

    while index < len(command):
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if quote != "'" and command.startswith("$(", index):
            end = _dollar_substitution_end(command, index + 2)
            if end is not None:
                if depth >= _MAX_SUBSTITUTION_DEPTH:
                    if _commit_at_nesting_limit(command[index + 2:end]):
                        commands.append("git commit")
                else:
                    commands.extend(
                        _split_shell_commands(command[index + 2:end], depth + 1)
                    )
                index = end + 1
                continue
        if quote is None and command[index:index + 2] in {"<(", ">("}:
            end = _dollar_substitution_end(command, index + 2)
            if end is not None:
                body = command[index + 2:end]
                if depth >= _MAX_SUBSTITUTION_DEPTH:
                    if _commit_at_nesting_limit(body):
                        commands.append("git commit")
                else:
                    commands.extend(_split_shell_commands(body, depth + 1))
                index = end + 1
                continue
        escaped_dollar = index >= 2 and command[index - 2:index] == "\\$"
        if quote is None and character == "(" and not escaped_dollar:
            end = _dollar_substitution_end(command, index + 1)
            if end is not None:
                body = command[index + 1:end]
                if depth >= _MAX_SUBSTITUTION_DEPTH:
                    if _commit_at_nesting_limit(body):
                        commands.append("git commit")
                else:
                    commands.extend(_split_shell_commands(body, depth + 1))
                index = end + 1
                continue
        if quote != "'" and character == "`":
            end = _backtick_substitution_end(command, index + 1)
            if end is not None:
                body = command[index + 1:end].replace("\\`", "`")
                if depth >= _MAX_SUBSTITUTION_DEPTH:
                    if _commit_at_nesting_limit(body):
                        commands.append("git commit")
                else:
                    commands.extend(
                        _split_shell_commands(body, depth + 1)
                    )
                index = end + 1
                continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            index += 1
            continue
        if quote is None and command.startswith("<<", index):
            heredoc = _heredoc_body(command, index)
            if heredoc is not None:
                body_start, body_end, delimiter_quoted = heredoc
                header_end = command.find("\n", index)
                commands.extend(
                    _split_shell_commands(command[start:header_end], depth)
                )
                if not delimiter_quoted:
                    commands.extend(
                        _heredoc_substitution_commands(
                            command[body_start:body_end], depth
                        )
                    )
                index = body_end
                start = body_end
                continue
        if quote is None and character in ";&|\r\n":
            segment = command[start:index].strip()
            if segment:
                commands.append(segment)
            pair = command[index:index + 2]
            operator_length = 2 if pair in {"&&", "||", "\r\n"} else 1
            index += operator_length
            start = index
            continue
        index += 1

    segment = command[start:].strip()
    if segment:
        commands.append(segment)
    return commands


def git_subcommand(command: str) -> str | None:
    """Return the Git subcommand invoked by a command, if one is resolved.

    git's global options sit between "git" and the subcommand, so the
    subcommand is not always argv[1]: `git -C <dir> commit` invokes `commit`.

    Args:
        command: A single shell command.

    Returns:
        The resolved Git subcommand, or ``None`` for non-Git commands and Git
        invocations that do not unambiguously resolve a subcommand.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    tokens = _expand_env_split(tokens)
    if tokens is None:
        return None

    executable_index = _skip_command_prefix(tokens)
    if executable_index is None or executable_index >= len(tokens):
        return None
    if os.path.basename(tokens[executable_index]) != "git":
        return None

    i = executable_index + 1
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_TERMINAL_OPTS:
            return None
        if token in _GIT_GLOBAL_VALUE_OPTS:
            if i + 1 >= len(tokens):
                return None
            i += 2
            continue
        if any(
            token.startswith(f"{option}=")
            for option in _GIT_GLOBAL_VALUE_OPTS
            if option.startswith("--")
        ):
            i += 1
            continue
        if token.startswith("-C") and token != "-C":
            i += 1
            continue
        if token.startswith("-c") and token != "-c":
            i += 1
            continue
        if token in _GIT_GLOBAL_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            return None
        break

    return tokens[i] if i < len(tokens) else None


# Setting this environment variable allows commits in every session, without
# depending on a session-scoped flag file. Flag files live in /tmp, which macOS
# reaps after a few days, so a long-lived session used to start prompting again
# partway through its life.
ALLOW_ENV_VAR = "CCTOOLS_ALLOW_GIT"
_TRUTHY = {"1", "true", "yes", "on"}

FLAG_DIR = "/tmp/claude"
ALLOW_FLAG = "allow-git-commit"
DENY_FLAG = "deny-git-commit"


def _flag_path(name: str, session_id: str) -> str:
    return os.path.join(FLAG_DIR, f"{name}.{session_id}")


def env_allows_commit() -> bool:
    """Return whether the environment opts every session into commits."""
    return os.environ.get(ALLOW_ENV_VAR, "").strip().lower() in _TRUTHY


def commit_allowed(session_id: str = "") -> bool:
    """Return whether a commit may proceed without asking the user.

    A session-scoped deny flag (written by ``>allow-git off``) wins over the
    environment variable, so a single session can still opt back into prompts.
    """
    if session_id and os.path.exists(_flag_path(DENY_FLAG, session_id)):
        return False
    if env_allows_commit():
        return True
    return bool(session_id) and os.path.exists(
        _flag_path(ALLOW_FLAG, session_id)
    )


def check_git_commit_command(
    command: str,
    session_id: str = "",
) -> tuple[str, str | None]:
    """Check if a command contains a git commit and request
    user permission.

    Handles compound commands
    (e.g., "cd /path && git commit -m 'msg'").

    Returns tuple: (decision: str, reason: str or None)

    decision is one of: "allow", "ask", "block"
    """
    # Check each subcommand in compound commands
    for subcmd in _split_shell_commands(command):
        if git_subcommand(subcmd) in COMMIT_SUBCOMMANDS:
            if commit_allowed(session_id):
                return "allow", None
            reason = "Git commit requires your approval."
            return "ask", reason

    return "allow", None


# If run as a standalone script
if __name__ == "__main__":
    data = json.load(sys.stdin)

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")
    session_id = data.get("session_id", "")

    decision, reason = check_git_commit_command(command, session_id=session_id)

    if decision == "ask":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason
            }
        }))
    elif decision == "block":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)
