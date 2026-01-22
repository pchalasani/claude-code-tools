# Plugin Changelog

## 2026-01-22

### voice

- feat: replace blocking stop hook with auto-summarizing approach
  - No more "Stop hook error" display - hook approves immediately
  - Uses headless Claude (Opus) to generate voice summaries
  - Clear prompt structure separates past conversation (tone) from last message
  - Avoids meta-narration ("I explained X") - states content directly
  - Concurrent text + audio via systemMessage and background TTS

### aichat

- fix: add defensive coding guardrails to UserPromptSubmit hook
  - Try/except wrapper - any exception silently passes through
  - Strict type checking - prompt must be non-empty string
  - Stricter pattern matching - exact match or followed by space
  - Case-insensitive comparison

## 2026-01-19

### voice

- feat: smarter interrupt handling with state tracking
  - User interrupts now trigger immediate re-prompting (not 30s cooldown)
  - Tracks `-running` (with PID), `-done`, and `-failed` states
  - Distinguishes user interrupts (should retry) from TTS failures (give up)
- fix: use session-aware flag files (SESSION_ID) to avoid cross-session conflicts
- fix: queue audio playback with mkdir lock to prevent overlapping voices
- fix: robust PID-based locking prevents hangs after interrupted playback
- refactor: stop hook directly instructs agent with explicit script path instead
  of relying on PostToolUse hooks (avoids race conditions)
- refactor: say script handles session tracking directly via `--session` flag

### tmux-cli

- feat: add `execute()` method for reliable exit code extraction (#42)
  - Returns `{"output": str, "exit_code": int}` for shell commands
  - Uses unique markers to capture command boundaries and exit status
  - 30-second default timeout (configurable), returns `exit_code=-1` on timeout
- feat: progressive expansion polling for scrollback capture
  - Starts with 100 lines, expands to [500, 2000, unlimited] if markers scroll off
- fix: marker parsing now finds echoed output instead of typed command text
- docs: add `execute()` documentation to tmux-cli-instructions.md

Credit to @ryancnelson for the original execute() proposal and implementation approach.

## 2026-01-18

### voice

- feat: add streaming audio playback for lower latency
- fix: add 30-second cooldown to stop hook to prevent API error loops caused by
  thinking block retry issues
- fix: prevent infinite loop in stop hook by tracking block attempts
