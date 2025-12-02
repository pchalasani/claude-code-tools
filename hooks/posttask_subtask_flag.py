#!/usr/bin/env python3
import os
import json
import sys

FLAG_FILE = '.claude_in_subtask.flag'


def remove_subtask_flag(flag_path=None):
    """
    Remove the subtask flag file if it exists.
    Returns: True if flag was removed, False if it didn't exist.
    """
    path = flag_path or FLAG_FILE
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def main():
    """Main entry point for hook."""
    remove_subtask_flag()
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)


if __name__ == "__main__":
    main()