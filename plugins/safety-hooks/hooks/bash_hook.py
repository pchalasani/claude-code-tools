#!/usr/bin/env python3
"""
Unified Bash hook that combines all bash command safety checks.
Supports three decision types: allow, ask (user prompt), block (deny).
"""
import json
import sys
import os

# Add hooks directory to Python path so we can import the other modules
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    if PLUGIN_ROOT not in sys.path:
        sys.path.insert(0, PLUGIN_ROOT)
else:
    # Fallback for running outside plugin context
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import check functions from other hooks
from git_add_block_hook import check_git_add_command
from git_checkout_safety_hook import check_git_checkout_command
from git_commit_block_hook import check_git_commit_command
from rm_block_hook import check_rm_command
from env_file_protection_hook import check_env_file_access
from command_utils import expand_command_aliases


def normalize_check_result(result):
    """
    Normalize check results to (decision, reason) format.
    Handles both old format (bool, reason) and new format (decision_str, reason).
    """
    decision, reason = result
    if isinstance(decision, bool):
        # Old format: (should_block: bool, reason)
        return ("block" if decision else "allow", reason)
    # New format: (decision: str, reason)
    return (decision, reason)


def main():
    data = json.load(sys.stdin)

    session_id = data.get("session_id", "")

    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    # Expand any shell aliases before checking
    # This handles cases like 'gco -f' -> 'git checkout -f'
    command = expand_command_aliases(command)

    # Run all checks
    checks = [
        check_rm_command,
        check_git_add_command,
        check_git_checkout_command,
        check_git_commit_command,
        check_env_file_access,
    ]

    block_reasons = []
    ask_reasons = []

    for check_func in checks:
        # Only the commit check is session-scoped.
        if check_func is check_git_commit_command:
            result = check_func(command, session_id=session_id)
        else:
            result = check_func(command)
        decision, reason = normalize_check_result(result)
        if decision == "block":
            block_reasons.append(reason)
        elif decision == "ask":
            ask_reasons.append(reason)

    # Priority: block > ask > allow
    if block_reasons:
        if len(block_reasons) == 1:
            combined_reason = block_reasons[0]
        else:
            combined_reason = "Multiple safety checks failed:\n\n"
            for i, reason in enumerate(block_reasons, 1):
                combined_reason += f"{i}. {reason}\n\n"

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": combined_reason
            }
        }, ensure_ascii=False))
    elif ask_reasons:
        combined_reason = ask_reasons[0] if len(ask_reasons) == 1 else \
            "Approval required: " + "; ".join(ask_reasons)

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": combined_reason
            }
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)


if __name__ == "__main__":
    main()