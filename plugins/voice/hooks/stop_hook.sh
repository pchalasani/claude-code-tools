#!/usr/bin/env bash
# Stop hook - require voice feedback before stopping (session-aware)
# Works with post_bash_hook.sh which creates the flag when say is called

# Check if voice feedback is disabled in config
CONFIG_FILE="$HOME/.claude/voice.local.md"
if [[ ! -f "$CONFIG_FILE" ]]; then
    # Create default config file
    cat > "$CONFIG_FILE" << 'CONFIGEOF'
---
voice: azure
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

# Session-specific flag file (created by post_bash_hook.sh when say is invoked)
FLAG_FILE="/tmp/voice-${SESSION_ID}-done"
SAY_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/say"

if [[ -f "$FLAG_FILE" ]]; then
    # Voice feedback was provided for this session, approve and clean up
    rm -f "$FLAG_FILE"
    echo '{"decision": "approve"}'
else
    # Block until voice feedback is provided
    cat << EOF
{
  "decision": "block",
  "reason": "Provide a 1-2 sentence voice summary before stopping. Call: ${SAY_SCRIPT} \"your summary\""
}
EOF
fi
