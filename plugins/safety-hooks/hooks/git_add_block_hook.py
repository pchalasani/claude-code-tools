#!/usr/bin/env python3
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

# `git commit` options that consume a SEPARATE following token as their value,
# so that token must not itself be read as an option.
#
# -S/--gpg-sign are deliberately absent: their argument is optional and
# attached-only (-S<keyid>), so consuming the next token would swallow the -m of
# `git commit -aS -m "msg"`.
_COMMIT_VALUE_OPTS: set[str] = {
    '-m', '--message', '-F', '--file', '-c', '--reedit-message',
    '-C', '--reuse-message', '--author', '--date', '--cleanup',
    '-t', '--template', '--trailer', '--fixup', '--squash',
    '--pathspec-from-file',
}

_COMMIT_SHORT_VALUE_OPTS: set[str] = {'-m', '-F', '-c', '-C', '-t'}

# These options take an optional attached value. Once one appears in a short
# cluster, the rest of that token is its value rather than more option letters.
_COMMIT_SHORT_OPTIONAL_VALUE_OPTS: set[str] = {'-S', '-u'}

_COMMIT_MESSAGE_OPTION_FAMILIES = {
    '-m': 'message',
    '--message': 'message',
    '-F': 'file',
    '--file': 'file',
    '-C': 'reuse-message',
    '--reuse-message': 'reuse-message',
}

_COMMIT_NEGATED_MESSAGE_OPTIONS = {
    '--no-message': 'message',
    '--no-file': 'file',
    '--no-reuse-message': 'reuse-message',
}

_COMMIT_LONG_OPTIONS = {
    option for option in _COMMIT_VALUE_OPTS if option.startswith('--')
} | set(_COMMIT_MESSAGE_OPTION_FAMILIES) | set(
    _COMMIT_NEGATED_MESSAGE_OPTIONS
) | {
    '--all', '--allow-empty', '--allow-empty-message', '--amend', '--dry-run',
    '--edit', '--gpg-sign', '--include', '--interactive', '--no-edit',
    '--no-fixup', '--no-gpg-sign', '--no-post-rewrite', '--no-reedit-message',
    '--no-signoff', '--no-status', '--no-verify', '--only', '--patch',
    '--pathspec-file-nul', '--quiet', '--reset-author', '--signoff', '--status',
    '--untracked-files', '--verbose',
}

_GIT_GLOBAL_VALUE_OPTS: set[str] = {
    '-C', '-c', '--attr-source', '--config-env', '--git-dir', '--work-tree',
    '--namespace',
}

_GIT_GLOBAL_FLAG_OPTS: set[str] = {
    '-p', '-P', '--bare', '--no-pager', '--paginate', '--no-replace-objects',
    '--literal-pathspecs', '--glob-pathspecs', '--noglob-pathspecs',
    '--icase-pathspecs', '--no-optional-locks', '--no-lazy-fetch',
}

_ASSIGNMENT_WORD = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_ENV_VALUE_OPTS: set[str] = {'-u', '--unset', '-C', '--chdir', '-P'}
_ENV_SHORT_VALUE_OPTS = frozenset('uCPS')
_UNRESOLVABLE_CHDIR_CHARS = frozenset('$`*?[')
_REPOSITORY_ROUTING_VARS = {'GIT_DIR', 'GIT_WORK_TREE'}
_UNVERIFIED_STAGING_REASON = (
    'Could not safely resolve the target repository or inspect its status. '
    'Approval is required before staging files.'
)
_ADD_ALL_LONG_OPTIONS = {'--a', '--al', '--all'}


def _env_short_value(token: str) -> tuple[str, str | None] | None:
    """Return the first value-taking option in a short ``env`` cluster."""
    if not token.startswith('-') or token.startswith('--'):
        return None
    for index, option in enumerate(token[1:]):
        if option in _ENV_SHORT_VALUE_OPTS:
            attached = token[index + 2:] or None
            return f'-{option}', attached
    return None


def _split_compound_command(command: str) -> list[str]:
    """Split on unquoted, unescaped shell command operators."""
    commands: list[str] = []
    start = 0
    quote: str | None = None
    i = 0
    while i < len(command):
        char = command[i]
        if quote:
            if char == quote:
                quote = None
            elif char == '\\' and quote == '"':
                i += 1
        elif char in "'\"":
            quote = char
        elif char == '\\':
            i += 1
        elif char in ';|&\r\n':
            part = command[start:i].strip()
            if part:
                commands.append(part)
            if char == '\r' and i + 1 < len(command) and command[i + 1] == '\n':
                i += 1
            elif char in '|&' and i + 1 < len(command) and command[i + 1] == char:
                i += 1
            start = i + 1
        i += 1
    final = command[start:].strip()
    if final:
        commands.append(final)
    return commands


def _expand_env_split(argv: list[str]) -> list[str] | None:
    """Expand the command string supplied through ``env -S``."""
    i = 0
    while i < len(argv) and _ASSIGNMENT_WORD.match(argv[i]):
        i += 1
    if i >= len(argv) or Path(argv[i]).name != 'env':
        return argv

    i += 1
    while i < len(argv):
        token = argv[i]
        value: str | None = None
        consumed = 1
        if token == '--':
            break
        if token in {'-S', '--split-string'}:
            if i + 1 >= len(argv):
                return None
            value = argv[i + 1]
            consumed = 2
        elif token.startswith('--split-string='):
            value = token.split('=', 1)[1]
        elif short_value := _env_short_value(token):
            option, value = short_value
            if option != '-S':
                value = None
            elif value is None:
                if i + 1 >= len(argv):
                    return None
                value = argv[i + 1]
                consumed = 2
        if value is not None:
            try:
                expanded = shlex.split(value)
            except ValueError:
                return None
            return argv[:i] + expanded + argv[i + consumed:]
        if _ASSIGNMENT_WORD.match(token):
            i += 1
            continue
        name = token.partition('=')[0]
        short_value = _env_short_value(token)
        if name in _ENV_VALUE_OPTS or short_value:
            attached = short_value[1] if short_value else None
            if '=' not in token and attached is None:
                i += 1
                if i >= len(argv):
                    return None
            i += 1
            continue
        if token.startswith('-'):
            i += 1
            continue
        break
    return argv


def _resolve_git_commit_argv(command: str) -> list[str] | None:
    """Return a canonical Git commit argv after valid global options."""
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    argv = _expand_env_split(argv)
    if argv is None:
        return None

    i = 0
    while i < len(argv) and _ASSIGNMENT_WORD.match(argv[i]):
        i += 1

    if i < len(argv) and Path(argv[i]).name == 'env':
        i += 1
        while i < len(argv):
            token = argv[i]
            name = token.partition('=')[0]
            short_value = _env_short_value(token)
            if _ASSIGNMENT_WORD.match(token):
                i += 1
                continue
            if token == '--':
                i += 1
                while i < len(argv) and _ASSIGNMENT_WORD.match(argv[i]):
                    i += 1
                break
            if name in _ENV_VALUE_OPTS or short_value:
                needs_value = token in _ENV_VALUE_OPTS or (
                    short_value is not None
                    and short_value[1] is None
                )
                if '=' not in token and needs_value:
                    i += 1
                    if i >= len(argv):
                        return None
                i += 1
                continue
            if token.startswith('-'):
                i += 1
                continue
            break

    if i >= len(argv) or Path(argv[i]).name != 'git':
        return None

    i += 1
    while i < len(argv):
        token = argv[i]
        name = token.partition('=')[0]
        if name == '--list-cmds' or token == '--exec-path':
            return None
        if name == '--exec-path':
            i += 1
            continue
        if token in _GIT_GLOBAL_FLAG_OPTS:
            i += 1
            continue
        if token.startswith('-C') and token != '-C':
            i += 1
            continue
        if token.startswith('-c') and token != '-c':
            i += 1
            continue
        if name in _GIT_GLOBAL_VALUE_OPTS:
            if '=' not in token:
                i += 1
                if i >= len(argv):
                    return None
            i += 1
            continue
        break

    if i >= len(argv) or argv[i] != 'commit':
        return None
    return ['git', 'commit', *argv[i + 1:]]


def _resolve_add_chdir(cwd: str | None, value: str) -> str | None:
    """Resolve one chdir value without evaluating shell expansions."""
    if cwd is None or any(char in value for char in _UNRESOLVABLE_CHDIR_CHARS):
        return None
    return os.path.abspath(os.path.join(cwd, value))


def _is_repository_routing_assignment(token: str) -> bool:
    """Return whether an assignment changes which repository Git targets."""
    return token.partition('=')[0] in _REPOSITORY_ROUTING_VARS


def _resolve_git_add_argv(
    command: str,
) -> tuple[list[str], str | None] | None:
    """Return canonical add arguments and the Git working directory."""
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    argv = _expand_env_split(argv)
    if argv is None:
        return None

    cwd = os.getcwd()
    i = 0
    while i < len(argv) and _ASSIGNMENT_WORD.match(argv[i]):
        if _is_repository_routing_assignment(argv[i]):
            cwd = None
        i += 1

    if i < len(argv) and Path(argv[i]).name == 'env':
        i += 1
        while i < len(argv):
            token = argv[i]
            name, separator, attached = token.partition('=')
            short_value = _env_short_value(token)
            if _ASSIGNMENT_WORD.match(token):
                if _is_repository_routing_assignment(token):
                    cwd = None
                i += 1
                continue
            if token == '--':
                i += 1
                while i < len(argv) and _ASSIGNMENT_WORD.match(argv[i]):
                    if _is_repository_routing_assignment(argv[i]):
                        cwd = None
                    i += 1
                break
            if name in _ENV_VALUE_OPTS or short_value:
                value = attached if separator else (
                    short_value[1] if short_value else None
                )
                needs_value = token in _ENV_VALUE_OPTS or (
                    short_value is not None
                    and short_value[1] is None
                )
                if value is None and needs_value:
                    i += 1
                    if i >= len(argv):
                        return None
                    value = argv[i]
                option = short_value[0] if short_value else name
                if option in {'-C', '--chdir'} and value:
                    cwd = _resolve_add_chdir(cwd, value)
                i += 1
                continue
            if token.startswith('-'):
                i += 1
                continue
            break

    if i >= len(argv) or Path(argv[i]).name != 'git':
        return None

    i += 1
    while i < len(argv):
        token = argv[i]
        name, separator, attached = token.partition('=')
        if name == '--list-cmds' or token == '--exec-path':
            return None
        if name == '--exec-path':
            i += 1
            continue
        if token in _GIT_GLOBAL_FLAG_OPTS:
            i += 1
            continue
        if token.startswith('-C') and token != '-C':
            value = token[2:]
            cwd = _resolve_add_chdir(cwd, value)
            i += 1
            continue
        if token.startswith('-c') and token != '-c':
            i += 1
            continue
        if name in _GIT_GLOBAL_VALUE_OPTS:
            value = attached if separator else None
            if value is None:
                i += 1
                if i >= len(argv):
                    return None
                value = argv[i]
            if name == '-C' and value:
                cwd = _resolve_add_chdir(cwd, value)
            elif name in {'--git-dir', '--work-tree'}:
                cwd = None
            i += 1
            continue
        break

    if i >= len(argv) or argv[i] != 'add':
        return None
    return ['git', 'add', *argv[i + 1:]], cwd


def _canonical_commit_long_option(name: str) -> str:
    """Resolve an unambiguous abbreviated ``git commit`` long option."""
    if name in _COMMIT_LONG_OPTIONS:
        return name
    matches = [option for option in _COMMIT_LONG_OPTIONS
               if option.startswith(name)]
    return matches[0] if len(matches) == 1 else name


def _parse_commit_options(
    normalized_cmd: str,
) -> list[tuple[str, str | None]]:
    """Parse the real options of a ``git commit`` command.

    Short clusters are read left to right. A required-value option consumes the
    rest of its token or the following token, while an optional-value option
    consumes only an attached remainder. Everything after ``--`` is a pathspec.

    Args:
        normalized_cmd: The normalized command string to inspect.

    Returns:
        Pairs containing each option name and its attached or separate value.
    """
    argv = _resolve_git_commit_argv(normalized_cmd)
    if argv is None:
        return []

    options: list[tuple[str, str | None]] = []
    i = 2  # skip "git commit"
    while i < len(argv):
        token = argv[i]
        if token == '--':
            break
        if token.startswith('--'):
            name, separator, attached_value = token.partition('=')
            name = _canonical_commit_long_option(name)
            value = attached_value if separator else None
            if name in _COMMIT_VALUE_OPTS and not separator:
                if i + 1 < len(argv):
                    i += 1
                    value = argv[i]
            options.append((name, value))
        elif token.startswith('-') and len(token) > 1:
            cluster = token[1:]
            position = 0
            while position < len(cluster):
                name = '-' + cluster[position]
                remainder = cluster[position + 1:]
                if name in _COMMIT_SHORT_VALUE_OPTS:
                    value = remainder or None
                    if value is None and i + 1 < len(argv):
                        i += 1
                        value = argv[i]
                    options.append((name, value))
                    break
                if name in _COMMIT_SHORT_OPTIONAL_VALUE_OPTS:
                    options.append((name, remainder or None))
                    break
                options.append((name, None))
                position += 1
        i += 1
    return options


def _commit_option_tokens(normalized_cmd: str) -> list[str]:
    """Return the option names in a ``git commit`` command.

    Args:
        normalized_cmd: The normalized command string to inspect.

    Returns:
        Option names without their attached or separate values.
    """
    return [name for name, _value in _parse_commit_options(normalized_cmd)]


def _commit_avoids_editor(
    options: list[tuple[str, str | None]],
) -> bool:
    """Return whether parsed commit options avoid opening an editor.

    Args:
        options: Parsed option-name and value pairs in command order.

    Returns:
        Whether Git has a message source or explicit setting that avoids the
        editor.
    """
    edit_setting: bool | None = None
    for name, _value in options:
        if name in {'-e', '--edit'}:
            edit_setting = True
        elif name == '--no-edit':
            edit_setting = False

    if edit_setting is not None:
        return not edit_setting

    message_sources: set[str] = set()
    reedit_message = False
    fixup_value: str | None = None
    for name, value in options:
        if family := _COMMIT_MESSAGE_OPTION_FAMILIES.get(name):
            message_sources.add(family)
        elif family := _COMMIT_NEGATED_MESSAGE_OPTIONS.get(name):
            message_sources.discard(family)
        elif name in {'-c', '--reedit-message'}:
            reedit_message = True
        elif name == '--no-reedit-message':
            reedit_message = False
        elif name == '--fixup':
            fixup_value = value
        elif name == '--no-fixup':
            fixup_value = None

    if reedit_message:
        return False
    if fixup_value and fixup_value.startswith(('amend:', 'reword:')):
        return False
    return bool(message_sources or fixup_value)


def _is_allowed(flag_name: str, session_id: str = "") -> bool:
    """Check if a session-scoped allow flag is set."""
    if not session_id:
        return False
    return os.path.exists(
        f'/tmp/claude/allow-git-{flag_name}.{session_id}'
    )


def check_git_add_command(command, session_id: str = ""):
    """
    Check if a git add command contains dangerous patterns.
    Handles compound commands (e.g., "cd /path && git add .").

    Returns tuple: (decision, reason) where decision is bool or "ask"/"block"/"allow"
    """
    # Check each subcommand in compound commands
    # Scan ALL subcommands to ensure blocks aren't hidden after asks
    first_ask_result = None

    for subcmd in _split_compound_command(command):
        result = _check_single_git_add_command(subcmd, session_id)
        decision, reason = result

        # Hard blocks return immediately
        if decision is True or decision == "block":
            return result

        # Collect first "ask" but continue scanning for blocks
        if decision == "ask" and first_ask_result is None:
            first_ask_result = result

    # Return ask only after confirming no blocks exist
    if first_ask_result:
        return first_ask_result

    return False, None


def _check_single_git_add_command(command, session_id: str = ""):
    """
    Check a single (non-compound) command for dangerous git add patterns.
    Returns tuple: (decision, reason) where decision is bool or "ask"/"block"/"allow"
    """
    # Normalize recognized add commands after stripping supported Git prefixes.
    normalized_cmd = ' '.join(command.strip().split())
    resolved_add = _resolve_git_add_argv(command)
    repo_cwd: str | None = os.getcwd()
    if resolved_add is not None:
        add_argv, repo_cwd = resolved_add
        normalized_cmd = shlex.join(add_argv)

    # Always allow --dry-run (used internally to detect what would be staged)
    if '--dry-run' in normalized_cmd or '-n' in normalized_cmd.split():
        return False, None

    # Pattern to match git add with problematic flags and dangerous patterns
    # Check for wildcards or dangerous patterns anywhere in the arguments
    if '*' in normalized_cmd and normalized_cmd.startswith('git add'):
        reason = """BLOCKED: Wildcard patterns are not allowed in git add!

DO NOT use wildcards like 'git add *.py' or 'git add *'

Instead, use:
- 'git add <specific-files>' to stage specific files
- 'git ls-files -m "*.py" | xargs git add' if you really need pattern matching

This restriction prevents accidentally staging unwanted files."""
        return True, reason

    # Hard block patterns: -A, --all, -a, ., ../, etc.
    dangerous_pattern = re.compile(
        r'^git\s+add\s+(?:.*\s+)?('
        r'-[a-zA-Z]*[Aa][a-zA-Z]*(\s|$)|'  # Flags containing 'A' or 'a'
        r'--all(\s|$)|'                     # Long form --all
        r'\.(\s|$)|'                        # git add . (current directory)
        r'\.\./[\.\w/]*(\s|$)'             # git add ../ or ../.. patterns
        r')', re.IGNORECASE
    )

    add_arguments = add_argv[2:] if resolved_add is not None else []
    abbreviated_all = any(
        argument.split('=', 1)[0] in _ADD_ALL_LONG_OPTIONS
        for argument in add_arguments
    )
    if dangerous_pattern.search(normalized_cmd) or abbreviated_all:
        reason = """BLOCKED: Dangerous git add pattern detected!

DO NOT use:
- 'git add -A', 'git add -a', 'git add --all' (adds ALL files)
- 'git add .' (adds entire current directory)
- 'git add ../' or similar parent directory patterns
- 'git add *' (wildcard patterns)

Instead, use:
- 'git add <specific-files>' to stage specific files
- 'git add <specific-directory>/' to stage a specific directory (with confirmation)
- 'git add -u' to stage all modified/deleted files (but not untracked)

This restriction prevents accidentally staging unwanted files."""
        return True, reason

    if resolved_add is not None and repo_cwd is None:
        return 'ask', _UNVERIFIED_STAGING_REASON

    if any(
        argument.startswith('--pathspec-')
        for argument in add_arguments
    ):
        if _is_allowed('staging', session_id):
            return False, None
        return 'ask', 'Staging paths supplied through a pathspec file.'

    # Check for git add with a directory
    # Match: git add <dirname>/ or git add <path/to/dir>/
    directory_pattern = re.compile(r'^git\s+add\s+(?!-)[^\s]+/$')
    match = directory_pattern.search(normalized_cmd)

    if match:
        # Extract the directory path from the command
        parts = normalized_cmd.split()
        dir_path = None
        for i, part in enumerate(parts):
            if i > 0 and parts[i-1] == 'add' and part.endswith('/'):
                dir_path = part.rstrip('/')
                break

        if dir_path:
            # Use dry-run to get files that would be staged
            try:
                result = subprocess.run(
                    ['git', 'add', '--dry-run', dir_path + '/'],
                    capture_output=True, text=True, cwd=repo_cwd
                )
                if result.returncode != 0:
                    return 'ask', _UNVERIFIED_STAGING_REASON
                # Parse dry-run output: "add 'filename'" lines
                files = []
                for line in result.stdout.strip().split('\n'):
                    if line.startswith('add '):
                        # Extract filename from "add 'filename'"
                        fname = line[4:].strip().strip("'")
                        files.append(fname)

                if not files:
                    # No files to stage
                    return False, None

                # Check which files are modified vs new
                modified_files = []
                new_files = []
                for f in files:
                    status_result = subprocess.run(
                        ['git', 'status', '--porcelain', f],
                        capture_output=True, text=True, cwd=repo_cwd
                    )
                    if status_result.returncode != 0:
                        return 'ask', _UNVERIFIED_STAGING_REASON
                    status = status_result.stdout.strip()
                    if status:
                        status_code = status[:2]
                        if '?' in status_code:
                            new_files.append(f)
                        else:
                            modified_files.append(f)

                # If only new files, allow without permission
                if not modified_files:
                    return False, None

                # Modified files present - ask for permission
                # Check if staging is allowed via flag file
                if _is_allowed('staging', session_id):
                    return False, None
                file_list = ", ".join(modified_files[:5])
                if len(modified_files) > 5:
                    file_list += f" (+{len(modified_files) - 5} more)"
                reason = (
                    f"Staging directory {dir_path}/ with modified files: {file_list}"
                )
                return "ask", reason

            except Exception:
                # If dry-run fails, fall back to asking permission
                reason = f"Staging directory {dir_path}/ (couldn't verify file status)"
                return "ask", reason

    # Also check for git commit -a without a message flag (which would open an
    # editor).
    #
    # This used to scan the whole command string for `-[a-zA-Z]*a[a-zA-Z]*` and
    # `-[a-zA-Z]*m[a-zA-Z]*`, which matched inside any hyphenated word rather
    # than only real option tokens: a path containing "-a" looked like the
    # stage-everything flag, and any hyphenated word containing an "m"
    # cancelled it again. Parse actual option tokens instead.
    if _resolve_git_commit_argv(normalized_cmd) is not None:
        options = _parse_commit_options(normalized_cmd)
        option_names = {name for name, _value in options}
        has_a_flag = '-a' in option_names or '--all' in option_names
        if has_a_flag and not _commit_avoids_editor(options):
            reason = (
                "Avoid 'git commit -a' when it opens an editor. Use "
                "'gcam \"message\"' instead, which is an alias for "
                "'git commit -a -m'."
            )
            return True, reason

    # Check if staging modified files (not new/untracked) - requires permission
    # This check runs after all blocking patterns pass
    if normalized_cmd.startswith('git add'):
        modified_files = get_modified_files_being_staged(
            normalized_cmd, cwd=repo_cwd)
        if modified_files is None:
            return 'ask', _UNVERIFIED_STAGING_REASON
        if modified_files:
            # Check if staging is allowed via flag file
            if _is_allowed('staging', session_id):
                return False, None
            file_list = ", ".join(modified_files[:5])
            if len(modified_files) > 5:
                file_list += f" (+{len(modified_files) - 5} more)"
            reason = f"Staging modified files: {file_list}"
            return "ask", reason

    return False, None


def get_modified_files_being_staged(command, cwd=None):
    """
    Extract files from git add command and return those that are modified
    (not new/untracked). Returns empty list if only staging new files.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if len(parts) < 3 or parts[0] != 'git' or parts[1] != 'add':
        return []

    # Extract file arguments (skip 'git add' and any flags)
    files = []
    for part in parts[2:]:
        if not part.startswith('-'):
            files.append(part)

    if not files:
        return []

    modified_files = []
    for f in files:
        try:
            # Check git status for this file
            result = subprocess.run(
                ['git', 'status', '--porcelain', f],
                capture_output=True, text=True, cwd=cwd or os.getcwd()
            )
            if result.returncode != 0:
                return None
            status = result.stdout.strip()
            if status:
                # Status codes: ?? = untracked, M = modified, A = staged
                # We want to flag modified files (not untracked)
                status_code = status[:2]
                if '?' not in status_code:  # Not untracked = modified/staged
                    modified_files.append(f)
        except Exception:
            return None

    return modified_files


# If run as a standalone script
if __name__ == "__main__":
    import json
    import sys

    data = json.load(sys.stdin)

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    should_block, reason = check_git_add_command(command)

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)
