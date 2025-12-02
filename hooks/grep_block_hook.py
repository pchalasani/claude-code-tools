#!/usr/bin/env python3
import json
import sys


def check_grep_command():
    """
    Always block grep commands.
    Returns tuple: (should_block: bool, reason: str)
    """
    return True, "Use 'rg' (ripgrep) instead of grep for faster and better search results"


def main():
    """Main entry point for hook."""
    should_block, reason = check_grep_command()
    print(json.dumps({
        "decision": "block" if should_block else "approve",
        "reason": reason
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()