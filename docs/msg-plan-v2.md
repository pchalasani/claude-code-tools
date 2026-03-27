# msg v2: Comprehensive Implementation Plan

## Context

v1 is committed on `feat/tmux-channels`. It has:

- SQLite store with delivery state machine
- CLI: register, send, reply, inbox, threads, status, watch
- Async watcher with wait_idle + tmux-cli notification
- Plugin with /register, /inbox slash commands
- Stop hook for Claude Code
- 28 passing tests

## What v2 adds

1. Headed vs headless agent modes
2. Prompt-empty detection before pane injection
3. UserPromptSubmit hook (Claude + Codex)
4. Codex hooks support (Stop + UserPromptSubmit)
5. Headless agent spawn + reply capture
6. Revised watcher logic

## Components (13 total)

### C1: Message Store (exists)
Write message to SQLite, create delivery records.
No changes needed.

### C2: Agent Registry (modify)
Add `mode` field to agents table:

```sql
ALTER TABLE agents ADD COLUMN mode TEXT
    NOT NULL DEFAULT 'headed';
    -- 'headed' = interactive tmux session
    -- 'headless' = resume via CLI on demand
```

For headless agents, `pane_id` and `display_addr` are
NULL. `session_file` stores the path to the agent's
conversation session file for resuming.

```sql
ALTER TABLE agents ADD COLUMN session_file TEXT;
```

Registration:

```bash
# Headed (existing)
msg register architect

# Headless
msg register reviewer --headless --agent=claude
msg register reviewer --headless --agent=codex \
    --session=<session-id>
```

### C3: Prompt-Empty Detection (new)
Module: `claude_code_tools/msg/prompt_detect.py`

Captures last few lines of a tmux pane via
`tmux capture-pane`. Checks if the input prompt is
empty by matching known patterns:

- Claude Code: line matches `❯` with nothing after
- Codex CLI: line matches `›` with nothing after

Returns: `empty`, `has_text`, or `unknown`.

Patterns are configurable (stored in a config dict,
extensible for future agents).

### C4: Idle Detection (exists)
Uses `tmux-cli wait_idle` with a short timeout (2-3s).
Returns immediately if already idle.
No changes needed — already in watcher.

### C5: Pane Injection (exists, modify)
Types slash command into tmux pane.

Change: only called when C3 returns `empty`.
Update notification text:

- Claude: `/msg:inbox`
- Codex: `/prompts:msg:inbox`

### C6: Stop Hook (exists for Claude, add for Codex)

**Claude Code** (`plugins/msg/hooks/hooks.json`):
Already implemented. Uses same claim protocol as
watcher.

**Codex CLI** (`.codex/hooks.json`):
New. Same logic, different config format.

Codex hooks require feature flag:
`features.codex_hooks=true`

Codex hooks config format (`.codex/hooks.json`):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "msg-hook stop",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Note: Codex hooks use same JSON structure as Claude
hooks (confirmed in Codex v0.114.0+). The hook
command receives JSON on stdin, outputs JSON on stdout.

### C7: UserPromptSubmit Hook (new, both agents)

Fires when user submits a prompt. Checks DB for
unread messages. If any, injects a notification into
the agent's context via `additionalContext`.

**Claude Code** — add to `plugins/msg/hooks/hooks.json`:

```json
{
  "UserPromptSubmit": [
    {
      "matcher": "*",
      "hooks": [
        {
          "type": "command",
          "command": "msg-hook prompt-submit",
          "timeout": 5
        }
      ]
    }
  ]
}
```

**Codex CLI** — add to `.codex/hooks.json` (same
format).

The hook script:

```python
# On UserPromptSubmit:
# 1. Find this agent by TMUX_PANE
# 2. Check DB for unread messages
# 3. If any: inject additionalContext with summary
#    "[MSG] You have N unread messages from X, Y.
#     Run msg inbox when ready."
# 4. Uses same claim protocol as watcher/stop hook
```

### C8: Headless Spawn (new)
Module: `claude_code_tools/msg/headless.py`

Spawns a headless agent to process a message:

```python
async def spawn_headless(
    agent: Agent,
    message: str,
) -> str:
    """Spawn a headless agent, return its response."""
    if agent.agent_kind == AgentKind.CLAUDE:
        cmd = ["claude", "-p", message]
        if agent.session_file:
            cmd = [
                "claude", "-p",
                "--resume", agent.session_file,
                message,
            ]
    elif agent.agent_kind == AgentKind.CODEX:
        cmd = ["codex", "exec", message]
        if agent.session_file:
            cmd = [
                "codex", "exec",
                "--resume", agent.session_file,
                message,
            ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode()
```

### C9: Reply Capture (headless) (new)
After C8 returns, write the output as a reply message
in SQLite. Create delivery records for the original
sender.

Integrated into the watcher's delivery loop for
headless recipients.

### C10: Inbox Reader (exists)
`msg inbox` — no changes needed. Works with or
without tmux.

### C11: Reply Sender (exists)
`msg reply` — no changes needed. Works with or
without tmux.

### C12: Watcher Daemon (modify)
Revised logic per pending delivery:

```python
async def deliver(recipient, deliveries):
    if recipient.mode == "headless":
        # Spawn headless agent with message
        response = await spawn_headless(
            recipient, message_body,
        )
        # Write response as reply
        write_reply(recipient, sender, response)
        # Mark original delivery as read
        mark_read(deliveries)
        # Deliver reply to sender (recurse/queue)
        return

    # Headed mode
    if not await is_idle(recipient.pane):
        # Busy — release claim, Stop hook handles it
        release_claims(deliveries)
        return

    prompt_state = detect_prompt(recipient.pane)

    if prompt_state == "empty":
        # Safe to type slash command
        slash = "/msg:inbox" if claude else
                "/prompts:msg:inbox"
        await tmux_send(recipient.pane, slash)
        mark_notified(deliveries)

    else:
        # User typing or unknown — release claim
        # UserPromptSubmit hook handles it
        release_claims(deliveries)
```

### C13: Auto-Start Watcher (exists)
No changes needed.

## Hook Command Consolidation

Instead of separate Python scripts for each hook,
create a single `msg-hook` CLI command:

```bash
msg-hook stop          # Stop hook logic
msg-hook prompt-submit # UserPromptSubmit hook logic
```

This simplifies hook configuration — both Claude and
Codex point to the same `msg-hook` command.

Entry point in pyproject.toml:

```
msg-hook = "claude_code_tools.msg.hooks:main"
```

## Implementation Order

### Phase 1: Prompt detection + watcher refinement
1. Implement C3 (prompt-empty detection)
2. Update C12 (watcher) with new idle/prompt/busy logic
3. Test: headed agent, all three states

### Phase 2: Hooks
4. Create `msg-hook` CLI with stop + prompt-submit
5. Update Claude hooks.json with UserPromptSubmit
6. Create Codex .codex/hooks.json config
7. Test: hooks fire correctly for both agents

### Phase 3: Headless mode
8. Add `mode` and `session_file` to agent registry (C2)
9. Implement C8 (headless spawn)
10. Implement C9 (reply capture)
11. Update C12 (watcher) for headless delivery
12. Test: headed→headless, headless→headed

### Phase 4: Polish
13. Update spec, skill docs, slash commands
14. Integration tests for all scenarios
15. Update plugin for Codex hooks config

## File Changes

### New files

```
claude_code_tools/msg/prompt_detect.py  -- C3
claude_code_tools/msg/headless.py       -- C8, C9
claude_code_tools/msg/hooks.py          -- C6, C7
                                           (consolidated)
```

### Modified files

```
claude_code_tools/msg/store.py    -- C2 schema migration
claude_code_tools/msg/watcher.py  -- C12 revised logic
claude_code_tools/msg/cli.py      -- --headless flag
plugins/msg/hooks/hooks.json      -- add UserPromptSubmit
pyproject.toml                    -- msg-hook entry point
```

### New config files

```
.codex/hooks.json   -- Codex hooks config (Stop +
                       UserPromptSubmit)
```

## Open Questions

1. Should headless agents auto-create a session file
   on first spawn, or require one to exist?
   Recommendation: auto-create on first spawn.

2. Timeout for headless agent response?
   Recommendation: configurable, default 120s.

3. Should headless spawn pass full thread history or
   just the latest message?
   Recommendation: just the latest message for v2.
   Thread history via session resume handles context.

4. For Codex hooks, do we need to ship a setup command
   like `msg setup-codex-hooks` that writes the
   .codex/hooks.json?
   Recommendation: yes, simplifies onboarding.
