#!/usr/bin/env python3
import re

def check_rm_command(command):
    """
    Check if a command contains rm that should be blocked.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())
    
    # Check if it's an rm command
    # This catches: rm, /bin/rm, /usr/bin/rm, sudo rm, etc.
    # Also handles: rm after separators (;, &, |) and sudo prefix
    if (normalized_cmd.startswith("rm ") or normalized_cmd == "rm" or
        re.search(r'(^|[;&|]\s*)(sudo\s+)?(/\S*/)?rm\b', normalized_cmd)):
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
    from hook_utils import load_and_validate_input, approve, block

    data = load_and_validate_input()

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        approve()

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    should_block, reason = check_rm_command(command)

    if should_block:
        block(reason)
    else:
        approve()
