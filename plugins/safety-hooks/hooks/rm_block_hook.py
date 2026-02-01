#!/usr/bin/env python3
import os
import re
import sys

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

from command_utils import extract_subcommands

def check_rm_command(command):
    """
    Check if a command contains rm that should be blocked.
    Handles compound commands (e.g., "cd /path && rm foo").
    Returns tuple: (should_block: bool, reason: str or None)
    """
    for subcmd in extract_subcommands(command):
        should_block, reason = _check_single_rm(subcmd)
        if should_block:
            return True, reason
    return False, None


def _check_single_rm(command):
    """Check a single command (no shell chaining) for rm."""
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())

    # Check if it's an rm command
    # This catches: rm, /bin/rm, /usr/bin/rm, etc.
    if (normalized_cmd.startswith("rm ") or normalized_cmd == "rm" or
        re.search(r'^(/\S*/)?rm\b', normalized_cmd)):
        reason_text = (
            "Instead of using 'rm':\n "
            "- MOVE files using `mv` to the TRASH directory in the CURRENT folder (create it if needed), \n"
            "- Add an entry in a markdown file called 'TRASH-FILES.md' in the current directory, "
            "  where you show a one-liner with the file name, where it moved, and the reason to trash it, e.g.:\n\n"
            "```\n"
            "test_script.py - moved to TRASH/ - temporary test script\n"
            "data/junk.txt - moved to TRASH/ - data file we don't need\n"
            "```"
        )
        return True, reason_text

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
    
    should_block, reason = check_rm_command(command)
    
    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "approve"}))
    
    sys.exit(0)
