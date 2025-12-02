#!/usr/bin/env python3
import json
import sys

FLAG_FILE = '.claude_in_subtask.flag'


def create_subtask_flag(flag_path=None):
    """
    Create a flag file indicating we're entering a subtask.
    Returns: True if flag was created successfully.
    """
    path = flag_path or FLAG_FILE
    with open(path, 'w') as f:
        f.write('1')
    return True


def main():
    """Main entry point for hook."""
    create_subtask_flag()
    print(json.dumps({"decision": "approve"}))
    sys.exit(0)


if __name__ == "__main__":
    main()