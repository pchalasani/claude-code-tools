#!/usr/bin/env python3
"""
Unified Bash hook that combines all bash command safety checks.
This ensures that if ANY check wants to block, the command is blocked.
"""
import sys
import os

# Add hooks directory to Python path so we can import the other modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hook_utils import load_and_validate_input, approve, block

# Lazy import cache - modules loaded only when needed
_check_functions = None


def _get_check_functions():
    """Lazy load check functions to reduce startup overhead."""
    global _check_functions
    if _check_functions is None:
        from git_add_block_hook import check_git_add_command
        from git_checkout_safety_hook import check_git_checkout_command
        from git_commit_block_hook import check_git_commit_command
        from rm_block_hook import check_rm_command
        from env_file_protection_hook import check_env_file_access
        _check_functions = [
            check_rm_command,
            check_git_add_command,
            check_git_checkout_command,
            check_git_commit_command,
            check_env_file_access,
        ]
    return _check_functions


def main():
    data = load_and_validate_input()

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        approve()

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    # Run all checks - collect all blocking reasons (lazy loaded)
    checks = _get_check_functions()
    blocking_reasons = []

    for check_func in checks:
        should_block, reason = check_func(command)
        if should_block:
            blocking_reasons.append(reason)

    # If any check wants to block, block the command
    if blocking_reasons:
        # If multiple checks want to block, combine the reasons
        if len(blocking_reasons) == 1:
            combined_reason = blocking_reasons[0]
        else:
            combined_reason = "Multiple safety checks failed:\n\n"
            for i, reason in enumerate(blocking_reasons, 1):
                combined_reason += f"{i}. {reason}\n\n"

        block(combined_reason)
    else:
        approve()


if __name__ == "__main__":
    main()