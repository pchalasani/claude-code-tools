#!/usr/bin/env python3
import os

from hook_utils import approve

FLAG_FILE = '.claude_in_subtask.flag'


def create_subtask_flag(flag_path=None):
    """
    Create a flag file indicating we're entering a subtask.
    Uses atomic O_CREAT | O_EXCL to prevent race conditions.
    Returns: True if flag was created successfully, False if already exists.
    """
    path = flag_path or FLAG_FILE
    try:
        # Atomic create: O_CREAT | O_EXCL ensures we either create or fail if exists
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write('1')
        return True
    except FileExistsError:
        # Flag already exists, which is fine
        return False


def main():
    """Main entry point for hook."""
    create_subtask_flag()
    approve()


if __name__ == "__main__":
    main()