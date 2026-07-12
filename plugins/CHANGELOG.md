# Plugin Changelog

## 2026-07-12

### dynamic-workflow

- feat: add a Codex plugin for durable JavaScript agent workflows
  - direct `codex exec --json` workers with no MCP dependency
  - bounded fan-out, structured output, retries, and ordered results
  - durable cache, pause, resume, cancel, status, logs, and detached runs
  - restricted workflow context and read-only worker sandbox by default
  - explicit persisted authorization gate for write-capable sandboxes
  - supervisor deadlines and forced cancellation for runaway JavaScript
  - process-group cleanup and in-flight worker draining before terminal state
  - persisted engine ownership and split-brain-safe orphan recovery
  - process-start identity checks before signaling persisted process IDs
  - active worker cleanup after an unexpected engine crash
  - durable worker registration before prompt delivery
  - recoverable nonterminal state when cleanup cannot be confirmed
  - process-group draining after normal, failed, or canceled worker exits
  - conservative agent, retry, pipeline, prompt, and result limits
  - non-retryable context diagnostics and executed-source snapshots

## 2026-02-15

### safety-hooks

- feat: add `>allow-git` trigger to toggle staging/commit
  approval per session
  - `>allow-git` allows both staging modified files and commits
  - `>allow-git staging` / `>allow-git commit` for granular control
  - `>allow-git off` restores approval prompts
  - `>allow-git status` shows current state
  - Session-scoped via `session_id` from hook JSON input
  - Dangerous operations (`git add -A`, `git add .`,
    `git checkout --force`) remain always blocked
  - Implemented as a `UserPromptSubmit` hook (no race conditions)

## 2026-02-11

### aichat, voice, safety-hooks

- fix: use `if/then/else/fi` bash wrapper for all hook commands
  - Only missing hook files fail open (approve); runtime errors
    still propagate (fail closed)
  - Prevents silent safety bypass when python3 crashes
  - Addresses Codex review on PR #55

## 2026-02-04

### safety-hooks

- feat: enhanced rm_block_hook security - detect rm in pipes, backgrounds, and subshells
  - Enhanced `extract_subcommands()`: split on all operators (&&, ||, ;, |, &)
  - New `extract_subshell_commands()`: detects commands in $() and backticks
  - New `extract_all_commands()`: recursive extraction including nested subshells
  - 57 new tests covering all bypass attempts
- Credit to @fizz for the original fix for pipe/background operator bypass

## 2026-01-24

### voice

- feat: add `/voice:speak prompt <text>` to set custom voice summary instruction
- fix: add 100-word hard limit on summaries to prevent verbose output
- fix: restore TTS-friendly instruction (avoid file paths, UUIDs, hashes in spoken output)

## 2026-01-22

### voice

- feat: replace blocking stop hook with auto-summarizing approach
  - No more "Stop hook error" display - hook approves immediately
  - Uses headless Claude (Opus) to generate voice summaries
  - Concurrent text + audio via systemMessage and background TTS
- refactor: simplify voice summary prompt to intuitive framing
  - Removed 7 prescriptive rules, replaced with natural instruction
  - Better tone matching - mirrors user's casual/colorful language
  - Enforces 1-2 sentence max, never longer than original message
- fix: defensive bash wrapper - approves instead of blocking if hook file missing

### aichat

- fix: add defensive coding guardrails to UserPromptSubmit hook
  - Try/except wrapper - any exception silently passes through
  - Strict type checking - prompt must be non-empty string
  - Stricter pattern matching - exact match or followed by space
  - Case-insensitive comparison
- fix: defensive bash wrapper - approves instead of blocking if hook file missing

### tmux-cli

- docs: document `execute` as CLI command (was incorrectly listed as Python API only)
  - `tmux-cli execute "cmd" --pane=2` returns JSON with output and exit_code

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
