#!/usr/bin/env python3
import os
import shlex
import subprocess
import sys

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

from command_utils import extract_subcommands

# git's own global options that consume the NEXT token as their value. They sit
# between "git" and the subcommand, so the subcommand is not always argv[1].
_GIT_GLOBAL_VALUE_OPTS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env",
}

# Options that point git at a repository this module cannot compute. Honouring
# them properly also means handling GIT_DIR / GIT_WORK_TREE and their
# composition with -C; rather than guess, mark the target unknown and let the
# caller decline to probe anything.
_OPAQUE_TARGET_OPTS = ("--work-tree", "--git-dir")

# Pathspecs that mean "everything", as opposed to a named file.
_UNBOUNDED_PATHSPECS = {".", "./", "*", ":/"}

_DANGER_TEMPLATE = (
    "⚠️  DANGEROUS COMMAND DETECTED!\n\n{message}\n\n"
    "This command will destroy uncommitted work without warning.\n\n"
    "Safer alternatives:\n"
    "- Use 'git stash' to save changes temporarily\n"
    "- Use 'git diff' to see what would be lost\n"
    "- Use 'git restore' for clearer syntax"
)


def _danger(message):
    return _DANGER_TEMPLATE.format(message=message)


def _tokenize(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def parse_git_checkout(command):
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
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None, None

    chdir = os.getcwd()
    opaque = False
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.split("=", 1)[0] in _OPAQUE_TARGET_OPTS:
            opaque = True
        if token in _GIT_GLOBAL_VALUE_OPTS:
            # git applies repeated -C cumulatively, each relative to the last.
            if token == "-C" and i + 1 < len(tokens):
                value = tokens[i + 1]
                chdir = value if os.path.isabs(value) else os.path.join(chdir, value)
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        break

    if i >= len(tokens) or tokens[i] != "checkout":
        return None, None

    args = tokens[i + 1:]
    if opaque or not os.path.isdir(chdir):
        return args, None
    return args, chdir


def _option_flags(tokens):
    """Real option tokens, with short clusters split (-fb -> {-f, -b})."""
    flags = set()
    for token in tokens:
        if token.startswith("--"):
            flags.add(token.split("=", 1)[0])
        elif token.startswith("-") and len(token) > 1:
            flags.update("-" + char for char in token[1:])
    return flags


def check_git_checkout_command(command):
    """
    Check if a git checkout command is safe to execute.
    Handles compound commands (e.g., "cd /path && git checkout branch").

    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check each subcommand in compound commands
    for subcmd in extract_subcommands(command):
        result = _check_single_git_checkout_command(subcmd)
        should_block, reason = result
        if should_block:
            return result

    return False, None


def _check_single_git_checkout_command(command):
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
            "This will overwrite your local file with version from another branch/commit!")

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
        return False, None

    try:
        # First, check if there are any uncommitted changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_dir
        )

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
            warning = f"WARNING: You have {num_changes} uncommitted change(s) that may be lost!\n\n"

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
                warning += "\n⚠️  DANGER: 'git checkout .' will DISCARD ALL local changes!"

            return True, warning

    except Exception as e:
        # If we can't determine status, err on the side of caution
        reason = f"Could not verify repository status: {str(e)}\nPlease manually check 'git status' before proceeding."
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
