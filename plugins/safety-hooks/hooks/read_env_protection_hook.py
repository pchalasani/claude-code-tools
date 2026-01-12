#!/usr/bin/env python3
"""
Hook to protect .env files from being read via the Read tool.
"""
import json
import sys
import os


def check_env_file_path(file_path):
    """
    Check if a file path refers to a .env file.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    if not file_path:
        return False, None

    # Get the basename and normalize to lowercase for case-insensitive matching
    basename = os.path.basename(file_path).lower()

    # Check for .env files (including .env.local, .ENV, .Env, etc.)
    # Note: We don't block bare "env" files as they're often unrelated (e.g., virtualenv dirs)
    if basename == '.env' or basename.startswith('.env.'):
        reason_text = (
            "Blocked: Direct access to .env files is not allowed for security reasons.\n\n"
            "Reading .env files could expose sensitive API keys, passwords, and secrets.\n\n"
            "For safe inspection, use the `env-safe` command in Bash:\n"
            "  - `env-safe list` - List all environment variable keys\n"
            "  - `env-safe list --status` - Show keys with defined/empty status\n"
            "  - `env-safe check KEY_NAME` - Check if a specific key exists\n\n"
            "To view .env contents, please do so manually outside of Claude Code."
        )
        return True, reason_text

    return False, None


def main():
    data = json.load(sys.stdin)

    # Check if this is a Read tool call
    tool_name = data.get("tool_name")
    if tool_name != "Read":
        print(json.dumps({
            "hookSpecificOutput": {
                "permissionDecision": "allow"
            }
        }))
        sys.exit(0)

    # Get the file path being read
    file_path = data.get("tool_input", {}).get("file_path", "")

    should_block, reason = check_env_file_path(file_path)

    if should_block:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }, ensure_ascii=False))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "permissionDecision": "allow"
            }
        }))

    sys.exit(0)


if __name__ == "__main__":
    main()
