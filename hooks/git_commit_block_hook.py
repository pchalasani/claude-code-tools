#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def check_git_commit_command(command):
    """
    Check if a command is a git commit and apply speed bump pattern.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())
    
    # Check if this is a git commit command
    if not normalized_cmd.startswith('git commit'):
        return False, None
    
    # Define the flag file path (in current directory, consistent with other hooks)
    flag_file = Path('.claude_git_commit_warning.flag')
    
    # If flag file exists, allow the commit and clear the flag
    if flag_file.exists():
        flag_file.unlink()  # Delete the flag file
        return False, None
    
    # First attempt - block and create flag file
    flag_file.touch()  # Create the flag file
    
    reason = """**Git commit blocked (first attempt).** Only retry if: (1) the user didn't require approval, OR (2) they've already approved. Otherwise, do NOT commit."""
    
    return True, reason


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
    
    should_block, reason = check_git_commit_command(command)
    
    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))
    
    sys.exit(0)