# agent-tunnel (plugin)

Provides the `>share` control command — a `UserPromptSubmit` hook that
publishes the **current** Claude Code session so teammates can ask it
questions through the `agent-tunnel` Discord bot.

## What it does

Type one of these as a prompt inside any Claude Code session:

- `>share` — publish this session; mints/show a short **handle**
- `>share <label>` — publish with a chosen handle (e.g. `>share payments`)
- `>share status` — show this session's handle, if any
- `>share off` — revoke this session's handle

The hook reads this session's own `session_id` and `cwd` from the hook
payload and records `handle → {session_id, cwd}` in the shared registry at
`~/.local/state/agent-tunnel/registry.json`. The prompt is intercepted
(`decision: block`) so it never reaches the model — you just see the handle
printed, exactly like `>allow-git`.

Give the handle to colleagues; they post `<handle> <question>` in the
agent-tunnel Discord channel, and the `agent-tunnel serve` daemon answers
from a read-only fork of that session. See the `agent-tunnel` tool docs for
the daemon side and Discord setup.

Sharing a **Codex CLI** session? This same plugin works there too — codex
supports the same `UserPromptSubmit` hook, so once the plugin is installed in
codex (`codex plugin marketplace add <this repo>` + `codex plugin add
agent-tunnel@cctools-codex-plugins`), `>share` works in-session exactly as in
Claude Code (the hook auto-detects the codex session and records
`agent=codex`). No plugin? Publish from the terminal instead with
`agent-tunnel share --agent codex <name>` in the project directory.

## Why a hook (not a CLI command)

Only code running *inside* a session knows which session it is — and a single
project folder can hold many concurrent sessions with different context. The
hook payload carries this session's exact `session_id`, so `>share` always
publishes the right one.
