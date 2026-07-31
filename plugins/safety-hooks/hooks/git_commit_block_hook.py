#!/usr/bin/env python3
"""
Git commit hook that asks for user permission before allowing commits.
Uses the "ask" decision type to prompt user in the UI.
"""
import json
import os
import shlex
import sys

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

from command_utils import extract_subcommands

# git's own global options that consume the NEXT token as their value. They sit
# between "git" and the subcommand, which is why the subcommand is not always
# argv[1] and a "starts with git commit" test is not enough.
_GIT_GLOBAL_VALUE_OPTS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env",
}


# Subcommands that write a commit object. `commit-tree` is included because
# the previous `startswith('git commit')` test matched it, and prompting for it
# is consistent with this hook's purpose.
COMMIT_SUBCOMMANDS = ('commit', 'commit-tree')


def git_subcommand(command):
    """
    The git subcommand a command invokes, or None if it is not a git command.

    git's global options sit between "git" and the subcommand, so the
    subcommand is not always argv[1]: `git -C <dir> commit` invokes `commit`.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if not tokens or os.path.basename(tokens[0]) != "git":
        return None

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        break

    return tokens[i] if i < len(tokens) else None


def check_git_commit_command(command, session_id: str = ""):
    """Check if a command contains a git commit and request
    user permission.

    Handles compound commands
    (e.g., "cd /path && git commit -m 'msg'").

    Returns tuple: (decision: str, reason: str or None)

    decision is one of: "allow", "ask", "block"
    """
    # Check each subcommand in compound commands
    for subcmd in extract_subcommands(command):
        normalized = ' '.join(subcmd.strip().split())
        if git_subcommand(normalized) in COMMIT_SUBCOMMANDS:
            # Check if commits are allowed via session-scoped flag
            if session_id and os.path.exists(
                f'/tmp/claude/allow-git-commit.{session_id}'
            ):
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

    decision, reason = check_git_commit_command(command)

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
