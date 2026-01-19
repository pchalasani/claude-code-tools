#!/usr/bin/env bash
# Stop hook - require voice feedback before stopping (session-aware)
# The say script creates the flag file when called with --session

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
FLAG_FILE="/tmp/voice-${SESSION_ID}-done"      # Created by say script when called with --session
BLOCK_FLAG="/tmp/voice-${SESSION_ID}-blocked"  # Tracks if we already blocked once
SAY_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/say"
SAY_CMD="${SAY_SCRIPT} --session ${SESSION_ID}"

# Cooldown: don't block if we blocked within the last 30 seconds (handles API error retries)
COOLDOWN_SECONDS=30

if [[ -f "$FLAG_FILE" ]]; then
    # Voice feedback was provided for this session, approve and clean up
    rm -f "$FLAG_FILE" "$BLOCK_FLAG"
    echo '{"decision": "approve"}'
elif [[ -f "$BLOCK_FLAG" ]]; then
    # Check if block flag is recent (within cooldown period)
    BLOCK_TIME=$(stat -f %m "$BLOCK_FLAG" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$((NOW - BLOCK_TIME))

    if [[ $AGE -lt $COOLDOWN_SECONDS ]]; then
        # Recently blocked, approve to prevent loop (don't delete flag yet)
        echo '{"decision": "approve"}'
    else
        # Old block flag, clean up and allow a fresh block
        rm -f "$BLOCK_FLAG"
        touch "$BLOCK_FLAG"
        cat << EOF
{
  "decision": "block",
  "reason": "Provide a 1-2 sentence voice summary before stopping. Call: ${SAY_CMD} \"your summary\""
}
EOF
    fi
else
    # First time - block and ask for voice feedback
    touch "$BLOCK_FLAG"
    cat << EOF
{
  "decision": "block",
  "reason": "Provide a 1-2 sentence voice summary before stopping. Call: ${SAY_CMD} \"your summary\""
}
EOF
fi
