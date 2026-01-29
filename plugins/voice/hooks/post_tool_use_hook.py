#!/usr/bin/env python3
"""
PostToolUse hook - inject brief voice reminder after tool calls.

This keeps the voice summary instruction fresh in Claude's context
after long chains of tool calls, where it might otherwise forget
the initial UserPromptSubmit instructions.
"""

import json
import sys
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from voice_common import get_voice_config, build_short_reminder


def main():
    try:
        # Consume stdin (hook input), but we don't need the data
        json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "approve"}))
        return

    # Check if voice is enabled
    enabled, _voice, _custom_prompt = get_voice_config()
    if not enabled:
        print(json.dumps({"decision": "approve"}))
        return

    # Build the short reminder
    reminder = build_short_reminder()

    # Use additionalContext for silent injection
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reminder
        }
    }))


if __name__ == "__main__":
    main()
