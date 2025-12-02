#!/usr/bin/env python3

from hook_utils import block


def check_grep_command():
    """
    Always block grep commands.
    Returns tuple: (should_block: bool, reason: str)
    """
    return True, "Use 'rg' (ripgrep) instead of grep for faster and better search results"


def main():
    """Main entry point for hook."""
    should_block, reason = check_grep_command()
    if should_block:
        block(reason)


if __name__ == "__main__":
    main()