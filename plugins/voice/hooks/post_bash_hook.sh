#!/usr/bin/env bash
# PostToolUse hook for Bash - creates flag when say script is invoked

# Read input
INPUT=$(cat)

# Extract command from tool_input
COMMAND=$(echo "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*: *"//' | sed 's/"$//')

# Check if command invokes the say script (match "say" or full path to say)
if [[ "$COMMAND" == *"/scripts/say "* ]] || [[ "$COMMAND" == *"/scripts/say\""* ]]; then
    # Extract session_id
    SESSION_ID=$(echo "$INPUT" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

    if [[ -n "$SESSION_ID" ]]; then
        # Create the flag file
        touch "/tmp/voice-${SESSION_ID}-done"
    fi
fi

# Always approve - this hook just creates flags
exit 0
