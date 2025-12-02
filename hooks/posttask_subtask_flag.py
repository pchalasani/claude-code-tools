#!/usr/bin/env python3
import os

from hook_utils import approve

FLAG_FILE = '.claude_in_subtask.flag'


def remove_subtask_flag(flag_path=None):
    """
    Remove the subtask flag file if it exists.
    Uses atomic operation to prevent TOCTOU race conditions.
    Returns: True if flag was removed, False if it didn't exist.
    """
    path = flag_path or FLAG_FILE
    try:
        # Atomic remove: no check, just try to remove
        os.remove(path)
        return True
    except FileNotFoundError:
        # Flag doesn't exist, which is fine
        return False


def main():
    """Main entry point for hook."""
    remove_subtask_flag()
    approve()


if __name__ == "__main__":
    main()