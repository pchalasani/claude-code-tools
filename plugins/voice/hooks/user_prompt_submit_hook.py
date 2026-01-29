#!/usr/bin/env python3
"""
UserPromptSubmit hook - inject voice summary reminder into each turn.

This hook adds a system message reminding Claude to end responses with a
voice-friendly summary marker (📢), making extraction easy and avoiding
the need for a headless Claude call to generate summaries.
"""

import json
import sys
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from voice_common import get_voice_config, build_full_reminder


def main():
    try:
        # Consume stdin (hook input), but we don't need the data
        json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "approve"}))
        return

    # Check if voice is enabled
    enabled, _voice, custom_prompt = get_voice_config()
    if not enabled:
        print(json.dumps({"decision": "approve"}))
        return

    # Build the full reminder with custom prompt if any
    reminder = build_full_reminder(custom_prompt)

    # Use additionalContext for truly silent injection (Claude sees it, user doesn't)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": reminder
        }
    }))


if __name__ == "__main__":
    main()
