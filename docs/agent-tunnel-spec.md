# agent-tunnel: let teammates talk to your local Claude sessions

`agent-tunnel` exposes long-lived, context-rich local Claude Code sessions
("experts") to teammates over Discord, so they can ask questions directly
instead of relaying through you. You publish *a specific session* at runtime
with `>share`; colleagues address it by its **handle**; each conversation is
answered against a read-only **fork** of that session.

## Motivation

A local Claude Code session accumulates large, valuable context — effectively
a custom expert agent for one task. Today the owner is a human relay: a
colleague asks, the owner pastes it into the session, copies the answer back.
agent-tunnel removes the relay while keeping each session private, clean, and
safe.

## Key design decisions

- **Publish per session, from inside it.** A single folder may hold many
  concurrent sessions with different context, so "which session" can only be
  decided from within. A `UserPromptSubmit` hook on `>share` (mirroring this
  repo's `>allow-git`) reads *this* session's own `session_id` + `cwd` from
  the hook payload and registers a short **handle**. No "newest in folder"
  guessing.
- **Handle is the unit of sharing.** You hand a handle to colleagues; they
  address it in the channel. Many sessions can be live at once, each its own
  handle.
- **Handle-opens-a-thread.** The first message `\<handle\> [question]` in the
  watched channel opens a Discord thread bound to that session; follow-ups
  stay in the thread (no handle needed). Different teammates / sessions never
  collide.
- **Forked, read-only.** Each thread answers against `--resume <session>
  --fork-session`: the fork copies context, the original is untouched.
  Remote turns run with `--allowedTools Read,Grep,Glob`, an explicit deny
  list, and `--permission-mode dontAsk`.
- **No tunnel.** The Discord Gateway is an outbound websocket; nothing on the
  machine is internet-reachable.
- **Swappable backends:** `tmux` (interactive forks, subscription metering)
  and `headless` (`claude -p`, Agent SDK credit metering after 2026-06-15).

## Architecture

```
   inside any Claude session:  >share  ──► UserPromptSubmit hook
                                              writes registry.json
                                              { handle -> session_id, cwd }
                                                        │
Discord channel/threads (teammates)                     │ reads
        │  outbound websocket (Discord Gateway)          ▼
        ▼                                       agent-tunnel serve (daemon)
  handle opens a thread ──► bind thread→handle ──► fork that session
                                                   (read-only) ──► answer
```

Two processes, one file: the `>share` hook (running inside each session)
**writes** the registry; the `serve` daemon **reads** it. Publish from any
session anytime; revoke with `>share off`.

## Components

`>share` hook — `plugins/agent-tunnel/`:

- `hooks/share_hook.py` — standalone stdlib `UserPromptSubmit` hook. On
  `>share [label|status|off]` it reads `session_id`/`cwd`/`transcript_path`
  from stdin, writes the registry (fcntl-locked, atomic), and returns
  `{"decision":"block","reason": "<handle> …"}` so the prompt is intercepted
  and the handle is printed in the terminal.
- `hooks/hooks.json`, `.claude-plugin/plugin.json`, `README.md`.

Daemon + core — `claude_code_tools/agent_tunnel/`:

- `registry.py` — shared handle registry (read by daemon, written by hook).
  Defines the JSON schema duplicated in the hook; `Registry` + helpers
  (`derive_handle`, `sanitize_label`).
- `config.py` — TOML config (Discord + limits + backend). No project/session
  here; `AGENT_TUNNEL_REGISTRY` env overrides the registry path (hook + daemon
  honor it).
- `store.py` — daemon state: `thread_key → ThreadRecord` (handle, expert
  session id, project dir, fork id, tmux window). `bind()` records a pending
  thread before the first answer; fork id filled on completion.
- `session.py` — transcript-dir resolution and fork-transcript parsing
  (locate the question, detect turn completion, extract the answer).
- `tmux.py` — minimal ops on a dedicated tmux session: named windows,
  bracketed paste, Enter-with-verify, idle detection, liveness/reaping.
- `backends.py` — `HeadlessBackend` / `TmuxBackend` behind one interface;
  both read the thread's binding to decide what to fork.
- `discord_bot.py` — handle→thread routing, allowlists, cooldown,
  concurrency, 2000-char chunking, long answers as `answer.md`.
- `cli.py` — `serve | ask | published | status | forget | init`.

## `>share` semantics

- `>share` — publish this session; mint/show a handle (default: short slug of
  the session id; idempotent — re-running returns the same handle).
- `>share <label>` — publish with a chosen handle (validated, deduped).
- `>share status` — show this session's handle, if any.
- `>share off` — revoke (new threads can't open; existing forks keep working,
  since a fork holds its own copy of history).

## Routing (handle-opens-a-thread)

- Watched-channel message `\<handle\> [question]`: if the handle is live, open
  a public thread, bind `thread → {handle, session_id, cwd}`, and answer the
  question (or post a ready notice).
- Thread message: a follow-up routed to the bound fork.
- DMs (optional, off by default): `\<handle\> …` (re)binds the DM; bare text
  follows up.

## Backends and billing

- `tmux` (default): one interactive `claude` per thread in a window of a
  dedicated tmux session; question pasted in, answer read from the fork's
  transcript. Metered as interactive subscription usage; idle windows reaped.
- `headless`: `claude -p` per question (JSON in/out). From 2026-06-15,
  subscription `claude -p` draws from a separate Agent SDK credit pool, then
  API rates. Do NOT auto-add `--bare` (it can break subscription auth).
- Each *new* thread replays the expert session's full context (~its token
  count per cold question). Fine for Q&A; for volume use headless + an
  `ANTHROPIC_API_KEY`.

## Security model

- Read-only tools, hard-enforced by the CLI permission layer.
- Access = Discord channel membership + optional user/role allowlists. Anyone
  who can ask can surface anything in the session context or readable project
  tree — publish accordingly; the persona discourages leaking secrets but is a
  soft layer only.
- ToS: consumer plans prohibit making your *account* available to others; an
  owner-operated relay is a gray area. Headless + API key is the unambiguous
  path.

## Validation status

- Unit tested (real files, no mocks): registry roundtrip/revoke, the `>share`
  hook via subprocess (publish/idempotent/distinct-session/label/collision/
  status/off), store bind+follow-up, session discovery & answer extraction,
  chunking, flag building, config.
- Live end-to-end (headless): `>share` → registry → `ask --handle` forked the
  exact published session, inherited context, and follow-ups continued the
  same fork.
- Live (tmux, pre-refactor): fork launch, bracketed paste, Enter, fork-file
  detection, idle, answer extraction validated in a real pane. Same code path
  post-refactor; not re-run live.
- Not yet live-tested: the Discord transport itself (needs a bot token).

## Roadmap

- v1.5: web chat page on the same core (behind Tailscale / Cloudflare Access).
- v2: remote MCP face (`ask_expert`) so colleagues' own Claude can consult a
  published session; needs a public endpoint + OAuth.
- Hardening: detect untrusted folders in the tmux backend (issue #80).
