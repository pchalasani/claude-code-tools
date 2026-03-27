# msg Phase A: Headed Refinements

## Status
- v1 committed on `feat/tmux-channels` (commit 158dd29)
- 28 tests passing
- Basic send/reply/inbox working end-to-end
- Watcher auto-starts, delivers /inbox slash commands

## What Phase A adds
1. Prompt-empty detection before pane injection
2. Revised watcher logic (busy/idle-empty/idle-typing)
3. Consolidated msg-hook CLI for Stop + UserPromptSubmit
4. UserPromptSubmit hook for Claude Code
5. Stop + UserPromptSubmit hooks for Codex CLI
6. Hook/watcher claim coordination tightening

## Tasks

### Task 1: Prompt-empty detection
File: `claude_code_tools/msg/prompt_detect.py`

- Capture last few lines of tmux pane via
  `tmux capture-pane -p -t <pane>`
- Match against known prompt patterns:

  - Claude Code: line with `❯` and nothing after
  - Codex CLI: line with `›` and nothing after

- Return enum: `empty`, `has_text`, `unknown`
- Patterns stored in a configurable dict
- Unit tests with mock pane output

### Task 2: Revised watcher logic
File: `claude_code_tools/msg/watcher.py` (modify)

For each pending delivery to a headed agent:

```
1. Is agent busy? (quick wait_idle with short timeout)
   YES → release claim, Stop hook handles it

2. Is prompt empty? (prompt_detect)
   YES → type slash command, mark notified
   NO (has_text) → release claim, UserPromptSubmit
                    hook handles it
   UNKNOWN → release claim, retry next loop
```

Remove the long wait_idle blocking. Instead, do a
quick check and release if not ready.

### Task 3: Consolidated msg-hook CLI
File: `claude_code_tools/msg/hooks.py` (new)

Click CLI with two subcommands:

```bash
msg-hook stop           # Stop hook logic
msg-hook prompt-submit  # UserPromptSubmit hook logic
```

Both subcommands:

1. Read JSON from stdin (hook payload)
2. Find this agent by $TMUX_PANE
3. Check DB for unread messages (state != 'read')
4. If messages exist:
   - Claim deliveries using claim protocol
   - Build notification text:
     "[MSG] N unread message(s) from X, Y.
      Run msg inbox when ready."
   - Output JSON with additionalContext
   - Mark claimed deliveries as notified
5. If no messages:
   - Output: {"decision": "approve"}

State transition rules (monotonic):
- Only transition pending → claimed → notified
- Never regress from notified or read
- If already claimed by watcher, skip (don't double-claim)

Entry point in pyproject.toml:
```
msg-hook = "claude_code_tools.msg.hooks:main"
```

### Task 4: Update Claude hooks.json
File: `plugins/msg/hooks/hooks.json` (modify)

Add UserPromptSubmit hook. Replace old stop_hook.py
with msg-hook command.

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "msg-hook stop",
        "timeout": 5
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "msg-hook prompt-submit",
        "timeout": 5
      }]
    }]
  }
}
```

Delete old `plugins/msg/hooks/stop_hook.py` (replaced
by msg-hook CLI).

### Task 5: Codex hooks config
File: `.codex/hooks.json` (new)

Same structure as Claude hooks.json. Users need to
enable with: `codex -c features.codex_hooks=true`

Consider adding a setup command:
`msg setup-codex-hooks` that writes the config.

### Task 6: Tests
- Unit tests for prompt_detect.py
- Unit tests for hooks.py (mock stdin/stdout)
- Update existing watcher tests for new logic
- Integration test: full round-trip with hooks

## Files Summary

### New
- `claude_code_tools/msg/prompt_detect.py`
- `claude_code_tools/msg/hooks.py`

### Modified
- `claude_code_tools/msg/watcher.py` (revised logic)
- `plugins/msg/hooks/hooks.json` (add UserPromptSubmit)
- `pyproject.toml` (msg-hook entry point)

### Deleted
- `plugins/msg/hooks/stop_hook.py` (replaced by msg-hook)

### New config (for users)
- `.codex/hooks.json` (Codex hooks config)

## Dependencies
- Codex CLI v0.114.0+ (for hooks support)
- Everything else already exists

## Notes for Phase B (headless, future)
- Make pane_id/tmux_session nullable in schema
- Add mode + session_file columns
- Headless spawn with fork-on-new-sender
- Structured output parsing (--json / --output-format)
- Per-thread fork session tracking
- Detailed plan to be written when Phase A is done
