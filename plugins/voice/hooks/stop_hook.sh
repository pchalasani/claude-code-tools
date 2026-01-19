#!/usr/bin/env bash
# Stop hook - require voice feedback before stopping (session-aware)
#
# State tracking:
#   -done    = voice completed successfully → approve
#   -failed  = TTS error (server down, etc.) → approve (don't retry broken TTS)
#   -running = say script started (contains PID)
#              - if PID dead and no -done/-failed → interrupted → block again
#   -blocked = we blocked once, used for grace period
#
# This distinguishes user interrupts (should retry) from TTS failures (should give up)

# Check if voice feedback is disabled in config
CONFIG_FILE="$HOME/.claude/voice.local.md"
if [[ ! -f "$CONFIG_FILE" ]]; then
    # Create default config file
    cat > "$CONFIG_FILE" << 'CONFIGEOF'
---
voice: azelma
enabled: true
---

# Voice Feedback Configuration

Use `/voice:speak stop` to disable, `/voice:speak <name>` to change voice.
CONFIGEOF
fi

ENABLED=$(sed -n '/^---$/,/^---$/p' "$CONFIG_FILE" | grep "^enabled:" | sed 's/enabled:[[:space:]]*//')
if [[ "$ENABLED" == "false" ]]; then
    # Voice feedback is disabled, approve immediately
    echo '{"decision": "approve"}'
    exit 0
fi

# Read input to get session_id
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

# Session-specific flag files
DONE_FILE="/tmp/voice-${SESSION_ID}-done"       # Created by say on success
FAILED_FILE="/tmp/voice-${SESSION_ID}-failed"   # Created by say on error (TTS down, etc.)
RUNNING_FILE="/tmp/voice-${SESSION_ID}-running" # Created by say at start, contains PID
BLOCK_FLAG="/tmp/voice-${SESSION_ID}-blocked"   # Tracks when we blocked
SAY_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/say"
SAY_CMD="${SAY_SCRIPT} --session ${SESSION_ID}"

# Grace period: short window after blocking for agent to call say
GRACE_SECONDS=5

# Helper to output block response
block_response() {
    touch "$BLOCK_FLAG"
    cat << EOF
{
  "decision": "block",
  "reason": "Provide a 1-2 sentence voice summary before stopping. Call: ${SAY_CMD} \"your summary\""
}
EOF
}

# Helper to check if running file has dead PID (indicates interrupt)
is_interrupted() {
    if [[ ! -f "$RUNNING_FILE" ]]; then
        return 1  # No running file = not interrupted
    fi
    local pid
    pid=$(cat "$RUNNING_FILE" 2>/dev/null)
    if [[ -z "$pid" ]]; then
        return 0  # Empty file = stale/interrupted
    fi
    # Check if process is dead
    if ! kill -0 "$pid" 2>/dev/null; then
        return 0  # Process dead = interrupted
    fi
    return 1  # Process alive = still running
}

# Decision logic
if [[ -f "$DONE_FILE" ]]; then
    # Voice completed successfully - approve and clean up
    rm -f "$DONE_FILE" "$FAILED_FILE" "$RUNNING_FILE" "$BLOCK_FLAG"
    echo '{"decision": "approve"}'

elif [[ -f "$FAILED_FILE" ]]; then
    # Voice failed (TTS error) - approve but don't keep retrying
    rm -f "$FAILED_FILE" "$RUNNING_FILE" "$BLOCK_FLAG"
    echo '{"decision": "approve"}'

elif is_interrupted; then
    # Voice was interrupted by user - block again immediately
    rm -f "$RUNNING_FILE" "$BLOCK_FLAG"
    block_response

elif [[ -f "$BLOCK_FLAG" ]]; then
    # We already blocked - check grace period
    BLOCK_TIME=$(stat -f %m "$BLOCK_FLAG" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$((NOW - BLOCK_TIME))

    if [[ $AGE -lt $GRACE_SECONDS ]]; then
        # Within grace period - approve (give agent time to call say)
        echo '{"decision": "approve"}'
    else
        # Grace period expired, voice not called - block again
        rm -f "$BLOCK_FLAG"
        block_response
    fi
else
    # First time - block and ask for voice feedback
    block_response
fi
