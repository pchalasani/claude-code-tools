#!/usr/bin/env bash
# Stop hook - require voice feedback before stopping (session-aware)
# Works with post_bash_hook.sh which creates the flag when say is called

# Read input to get session_id
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

# Session-specific flag file (created by post_bash_hook.sh when say is invoked)
FLAG_FILE="/tmp/voice-feedback-${SESSION_ID}-done"
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
