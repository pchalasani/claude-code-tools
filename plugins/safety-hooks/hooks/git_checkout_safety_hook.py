#!/usr/bin/env python3
import os
import re
import shlex
import subprocess
import sys
from typing import List, Optional, Set, Tuple

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

# git's own global options that consume the NEXT token as their value. They sit
# between "git" and the subcommand, so the subcommand is not always argv[1].
_GIT_GLOBAL_VALUE_OPTS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env", "--attr-source",
}

# These options print information and exit instead of dispatching a subcommand.
_GIT_TERMINAL_OPTS = {
    "-h", "-v", "--help", "--version", "--html-path", "--man-path",
    "--info-path",
}

# Options that point git at a repository this module cannot compute. Honouring
# them properly also means handling GIT_DIR / GIT_WORK_TREE and their
# composition with -C; rather than guess, mark the target unknown and let the
# caller decline to probe anything.
_OPAQUE_TARGET_OPTS = ("--work-tree", "--git-dir")

# Short checkout options whose remaining characters are their argument, rather
# than more options in the same cluster. For example, ``-bfeature`` means
# ``-b feature``; the ``f`` in ``feature`` is not ``--force``.
_CHECKOUT_SHORT_VALUE_OPTS = {"b", "B"}

# Shell expansions cannot be reproduced reliably after shlex has split the
# command. Tilde expansion is handled separately because it has deterministic
# local semantics for ordinary home-directory paths.
_UNRESOLVABLE_CHDIR_CHARS = frozenset("$`*?[")

# Pathspecs that mean "everything", as opposed to a named file.
_UNBOUNDED_PATHSPECS = {".", "./", "*", ":/"}
_FORCE_LONG_OPTIONS = {"--f", "--fo", "--for", "--forc", "--force"}

_ENV_VALUE_OPTS = {
    "-a", "--argv0", "-u", "--unset", "-C", "--chdir", "-S",
    "--split-string",
}
_ENV_SHORT_VALUE_OPTS = {"a", "u", "C", "P", "S"}
_REPOSITORY_ROUTING_VARS = {"GIT_DIR", "GIT_WORK_TREE"}
_LEADING_REDIRECTION = re.compile(r"^\d*[<>].+")
_LEADING_REDIRECTION_OPERATOR = re.compile(r"^\d*(?:<>|>>|>|<|>&|<&)\Z")

_DANGER_TEMPLATE = (
    "⚠️  DANGEROUS COMMAND DETECTED!\n\n{message}\n\n"
    "This command will destroy uncommitted work without warning.\n\n"
    "Safer alternatives:\n"
    "- Use 'git stash' to save changes temporarily\n"
    "- Use 'git diff' to see what would be lost\n"
    "- Use 'git restore' for clearer syntax"
)

_UNVERIFIED_REPOSITORY_MESSAGE = (
    "Could not safely resolve the target repository to verify its status.\n"
    "Please manually check 'git status' in the target repository before "
    "proceeding."
)


def _danger(message: str) -> str:
    return _DANGER_TEMPLATE.format(message=message)


def _tokenize(command: str) -> List[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _raw_tokens(command: str) -> Optional[List[str]]:
    """Tokenize while retaining quotes and escapes around token contents."""
    try:
        lexer = shlex.shlex(command, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _is_assignment(token: str) -> bool:
    """Return whether a token is a simple shell assignment word."""
    name, separator, _ = token.partition("=")
    return bool(
        separator
        and name
        and (name[0].isalpha() or name[0] == "_")
        and all(char.isalnum() or char == "_" for char in name)
    )


def _is_repository_routing_assignment(token: str) -> bool:
    """Return whether an assignment changes which repository Git targets."""
    return token.partition("=")[0] in _REPOSITORY_ROUTING_VARS


def _env_short_value_option(
    token: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Return the first value-taking short env option and attached value."""
    if not token.startswith("-") or token.startswith("--"):
        return None, None
    cluster = token[1:]
    for position, option in enumerate(cluster):
        if option in _ENV_SHORT_VALUE_OPTS:
            return option, cluster[position + 1:] or None
    return None, None


def _expand_env_split(
    tokens: List[str],
) -> Tuple[Optional[List[str]], bool]:
    """Expand an ``env -S`` command string into its effective argv."""
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return tokens, False

    index += 1
    while index < len(tokens):
        token = tokens[index]
        value: Optional[str] = None
        consumed = 1
        if token in ("-S", "--split-string"):
            if index + 1 >= len(tokens):
                return None, False
            value = tokens[index + 1]
            consumed = 2
        elif token.startswith("--split-string="):
            value = token.split("=", 1)[1]
        elif token.startswith("-") and not token.startswith("--"):
            option, attached = _env_short_value_option(token)
            if option == "S":
                value = attached
                if not value:
                    if index + 1 >= len(tokens):
                        return None, False
                    value = tokens[index + 1]
                    consumed = 2
            elif option is not None:
                if attached is None:
                    if index + 1 >= len(tokens):
                        return None, False
                    index += 2
                else:
                    index += 1
                continue
        if value is not None:
            try:
                expanded = shlex.split(value)
            except ValueError:
                return None, False
            return tokens[:index] + expanded + tokens[index + consumed:], True
        if _is_assignment(token):
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            if index + 1 >= len(tokens):
                return None, False
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return tokens, False


def _executable_index(tokens: List[str]) -> int:
    index = 0
    while index < len(tokens):
        if _LEADING_REDIRECTION.match(tokens[index]):
            index += 1
            continue
        if _LEADING_REDIRECTION_OPERATOR.match(tokens[index]):
            index += 2
            continue
        break
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return index
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if _is_assignment(token):
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            index += 2
            continue
        option, attached = _env_short_value_option(token)
        if option is not None:
            index += 1 if attached is not None else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return index


def _split_compound_command(command: str) -> List[str]:
    """Split on shell operators outside quotes and backslash escapes."""
    subcommands: List[str] = []
    start = 0
    quote: Optional[str] = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif (quote is None and char == "&" and index > 0
              and command[index - 1] in "<>"):
            pass
        elif quote is None and char in ";&|\n":
            subcommand = command[start:index].strip()
            if subcommand:
                subcommands.append(subcommand)
            if char in "&|" and command[index:index + 2] == char * 2:
                index += 1
            start = index + 1
        index += 1

    subcommand = command[start:].strip()
    if subcommand:
        subcommands.append(subcommand)
    return subcommands


def _resolve_chdir(
    current_dir: str,
    value: str,
    expand_tilde: Optional[bool] = False,
) -> Optional[str]:
    """Resolve one ``git -C`` value without evaluating shell syntax."""
    if value.startswith("~") and expand_tilde is None:
        return None
    expanded = os.path.expanduser(value) if expand_tilde else value
    if expand_tilde and expanded.startswith("~"):
        return None
    if any(char in expanded for char in _UNRESOLVABLE_CHDIR_CHARS):
        return None
    if expanded.startswith(("<(", ">(")):
        return None
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(current_dir, expanded)


def _env_chdir(
    tokens: List[str],
    raw_tokens: Optional[List[str]],
) -> Optional[str]:
    """Resolve a supported ``env -C`` prefix before its command."""
    current: Optional[str] = os.getcwd()
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return current

    index += 1
    while index < len(tokens):
        token = tokens[index]
        if _is_assignment(token):
            index += 1
            continue
        if token == "--":
            break
        name, separator, attached = token.partition("=")
        value: Optional[str] = attached if separator else None
        value_index = index
        if name in ("-S", "--split-string"):
            break
        if name in ("-C", "--chdir"):
            if value is None:
                index += 1
                if index >= len(tokens):
                    return None
                value = tokens[index]
                value_index = index
            if current is not None:
                raw_value = raw_tokens[value_index] if raw_tokens else None
                expand_tilde = raw_value.startswith("~") if raw_value else None
                current = _resolve_chdir(current, value, expand_tilde)
            index += 1
            continue
        if token.startswith("-C") and token != "-C":
            value = token[2:]
            if current is not None:
                current = _resolve_chdir(current, value)
            index += 1
            continue
        option, attached = _env_short_value_option(token)
        if option is not None:
            value = attached
            if value is None:
                index += 1
                if index >= len(tokens):
                    return None
                value = tokens[index]
            if option == "C" and current is not None:
                current = _resolve_chdir(current, value)
            if option == "S":
                break
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return current


def parse_git_checkout(
    command: str,
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Split ``git [global-opts] checkout <args...>`` into (args, repo_dir).

    Returns ``(None, None)`` when the command is not a git checkout at all.

    Returns ``(args, None)`` when it IS a git checkout but the repository it
    would act on cannot be determined -- an unexpanded ``-C`` value such as
    ``-C "$REPO"``, or ``--git-dir`` / ``--work-tree``. Checks that read only
    the command still apply; nothing may probe a directory on that basis,
    least of all the caller's own, which is a different repository.
    """
    tokens = _tokenize(command)
    raw_tokens = _raw_tokens(command)
    if raw_tokens is not None and len(raw_tokens) != len(tokens):
        raw_tokens = None
    chdir = _env_chdir(tokens, raw_tokens)
    tokens, env_split = _expand_env_split(tokens)
    if tokens is None:
        return None, None
    if env_split:
        raw_tokens = None
        chdir = _env_chdir(tokens, raw_tokens)
    if raw_tokens is not None and len(raw_tokens) != len(tokens):
        raw_tokens = None
    executable = _executable_index(tokens)
    if executable >= len(tokens) or os.path.basename(tokens[executable]) != "git":
        return None, None
    if any(
        _is_repository_routing_assignment(token)
        for token in tokens[:executable]
    ):
        chdir = None

    opaque = False
    i = executable + 1
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_TERMINAL_OPTS:
            return None, None
        if token.split("=", 1)[0] in _OPAQUE_TARGET_OPTS:
            opaque = True
        if token in _GIT_GLOBAL_VALUE_OPTS:
            # git applies repeated -C cumulatively, each relative to the last.
            if token == "-C" and i + 1 < len(tokens):
                value = tokens[i + 1]
                if chdir is not None:
                    raw_value = raw_tokens[i + 1] if raw_tokens else None
                    expand_tilde = (
                        raw_value.startswith("~") if raw_value else None
                    )
                    chdir = _resolve_chdir(chdir, value, expand_tilde)
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            if chdir is not None:
                chdir = _resolve_chdir(chdir, token[2:])
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        break

    if i >= len(tokens) or tokens[i] != "checkout":
        return None, None

    args = tokens[i + 1:]
    if opaque or chdir is None or not os.path.isdir(chdir):
        return args, None
    return args, chdir


def _option_flags(tokens: List[str]) -> Set[str]:
    """Return option flags without parsing attached option arguments as flags."""
    flags: Set[str] = set()
    for token in tokens:
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            flags.add("--force" if name in _FORCE_LONG_OPTIONS else name)
        elif token.startswith("-") and len(token) > 1:
            for char in token[1:]:
                flags.add("-" + char)
                if char in _CHECKOUT_SHORT_VALUE_OPTS:
                    break
    return flags


def check_git_checkout_command(command: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a git checkout command is safe to execute.
    Handles compound commands (e.g., "cd /path && git checkout branch").

    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check each subcommand in compound commands
    for subcmd in _split_compound_command(command):
        result = _check_single_git_checkout_command(subcmd)
        should_block, reason = result
        if should_block:
            return result

    return False, None


def _check_single_git_checkout_command(
    command: str,
) -> Tuple[bool, Optional[str]]:
    """
    Check a single (non-compound) git checkout command.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check if it's a git checkout command. This also recognises the
    # `git -C <dir> checkout ...` form, which a "starts with git checkout"
    # test misses entirely.
    args, repo_dir = parse_git_checkout(command)
    if args is None:
        return False, None

    # Everything before a `--` separator is options and refs; everything after
    # is pathspecs, even if it looks like an option.
    if "--" in args:
        separator = args.index("--")
        head, pathspecs = args[:separator], args[separator + 1:]
    else:
        separator = None
        head, pathspecs = args, []

    flags = _option_flags(head)

    if "-h" in flags or "--help" in flags:
        return False, None

    # ALWAYS block these dangerous patterns.
    #
    # -f is checked BEFORE the -b allowance below. Testing for "-b" first --
    # and as a substring of the whole command -- is what let `git checkout -f
    # -b <name>` and `git checkout . -b <name>` through, and disarmed the guard
    # for any command merely containing "-b", e.g. `git checkout --
    # src/my-button.ts`.
    if "-f" in flags or "--force" in flags:
        return True, _danger(
            "'git checkout -f' FORCES checkout and DISCARDS all uncommitted changes!")

    if separator is not None:
        if any(p in _UNBOUNDED_PATHSPECS for p in pathspecs):
            return True, _danger(
                "This will DISCARD ALL changes in current directory!")
        return True, _danger(
            "This will overwrite your local file with version from another "
            "branch/commit!"
        )

    positionals = [a for a in args if not a.startswith("-")]
    if any(p in _UNBOUNDED_PATHSPECS for p in positionals):
        return True, _danger(
            "'git checkout .' will DISCARD ALL changes in current directory!")

    # Creating a branch carries uncommitted work across rather than discarding
    # it, so it stays exempt from the uncommitted-changes prompt below.
    if "-b" in flags:
        return False, None

    # Everything above reads only the COMMAND. Everything below reads a
    # REPOSITORY, and repo_dir is None when we could not work out which one.
    # Probing the caller's own repo instead would report a verdict, and a list
    # of at-risk files, belonging to a repository the command never touches.
    if repo_dir is None:
        return True, _UNVERIFIED_REPOSITORY_MESSAGE

    try:
        # First, check if there are any uncommitted changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_dir
        )
        if status_result.returncode != 0:
            return True, _UNVERIFIED_REPOSITORY_MESSAGE

        has_changes = bool(status_result.stdout.strip())

        # Get more detailed status if there are changes
        if has_changes:
            # Get the list of modified files
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                cwd=repo_dir
            )

            unstaged_result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=repo_dir
            )

            # Count changes
            all_changes = status_result.stdout.strip().split('\n')
            modified_files = [f for f in all_changes if f.strip()]
            num_changes = len(modified_files)

            # Build warning message
            warning = (
                f"WARNING: You have {num_changes} uncommitted change(s) that "
                "may be lost!\n\n"
            )

            if modified_files:
                warning += "Modified files:\n"
                for change in modified_files[:10]:  # Show first 10
                    warning += f"  {change}\n"
                if num_changes > 10:
                    warning += f"  ... and {num_changes - 10} more\n"

            warning += "\nOptions:\n"
            warning += "1. Stash changes: git stash\n"
            warning += "2. Commit changes: git commit -am 'your message'\n"
            warning += "3. Discard changes: git restore <files>\n"
            warning += "4. Use 'git switch' instead for safer branch switching\n"

            # Special warning for checkout .
            if "checkout ." in command or "checkout -- ." in command:
                warning += (
                    "\n⚠️  DANGER: 'git checkout .' will DISCARD ALL "
                    "local changes!"
                )

            return True, warning

    except Exception as error:
        # If we can't determine status, err on the side of caution
        reason = (
            f"Could not verify repository status: {str(error)}\n"
            "Please manually check 'git status' before proceeding."
        )
        return True, reason

    # No uncommitted changes, safe to proceed
    return False, None


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

    should_block, reason = check_git_checkout_command(command)

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)
