#!/usr/bin/env python3
import os
from pathlib import Path

from hook_utils import load_and_validate_input, approve, block

def check_and_remove_flag(path):
    """
    Atomically check and remove flag file.
    Returns: True if flag existed and was removed, False if it didn't exist.
    """
    try:
        os.remove(str(path))
        return True
    except FileNotFoundError:
        return False

def create_flag(path):
    """
    Atomically create flag file.
    Returns: True if created, False if already exists.
    """
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        return True
    except FileExistsError:
        return False

def check_git_commit_command(command):
    """
    Check if a command is a git commit and apply speed bump pattern.
    Uses atomic operations to prevent TOCTOU race conditions.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())

    # Check if this is a git commit command
    if not normalized_cmd.startswith('git commit'):
        return False, None

    # Define the flag file path (in current directory, consistent with other hooks)
    flag_file = Path('.claude_git_commit_warning.flag')

    # If flag file exists, allow the commit and clear the flag atomically
    if check_and_remove_flag(flag_file):
        return False, None

    # First attempt - block and create flag file atomically
    create_flag(flag_file)

    reason = """**Git commit blocked (first attempt).** Only retry if: (1) the user didn't require approval, OR (2) they've already approved. Otherwise, do NOT commit."""

    return True, reason


# If run as a standalone script
if __name__ == "__main__":
    data = load_and_validate_input()

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        approve()

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    should_block, reason = check_git_commit_command(command)

    if should_block:
        block(reason)
    else:
        approve()