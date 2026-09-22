# agent-tunnel: let teammates talk to your local Claude sessions

`agent-tunnel` exposes long-lived, context-rich local Claude Code sessions
("experts") to teammates over Discord or Mattermost, so they can ask questions
directly instead of relaying through you. You publish *a specific session* at
runtime with `>share`; colleagues address it by its **handle**; each
conversation is answered against a read-only **fork** of that session.

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
  watched channel opens a thread bound to that session (a Discord thread, or
  on Mattermost the reply thread under that root post); follow-ups stay in the
  thread (no handle needed). Different teammates / sessions never collide.
- **Forked, read-only.** Each thread answers against `--resume <session>
  --fork-session`: the fork copies context, the original is untouched.
  Remote turns run with `--allowedTools Read,Grep,Glob`, an explicit deny
  list, and `--permission-mode dontAsk`.
- **Follow-ups see the latest session (headless).** A thread's fork is a
  snapshot from when the thread opened. With `[tunnel] refresh_forks = true`
  (default), a follow-up checks whether the published session's transcript
  has grown since that fork was taken; if so it forks the published session
  afresh and prepends a bounded recap of the thread's earlier Q&A
  (`[limits] recap_max_chars`, default 8000). A new top-level
  `\<handle\> question` already starts from the latest state. The tmux
  backend is unchanged (it keeps resuming the thread's own fork).
- **No tunnel.** The Discord Gateway and the Mattermost event websocket are
  outbound connections; nothing on the machine is internet-reachable. The
  HTTP front-end below binds to loopback; whether and how to expose it (a
  Tailscale Funnel, a Cloudflare Tunnel) is the deployer's call.
- **One daemon, many front-ends.** A single `agent-tunnel serve` runs Discord
  and/or Mattermost and/or the HTTP front-end on one asyncio loop, sharing
  one relay (so the concurrency cap is global across platforms).
- **An HTTP front-end for programs.** `[http]` in the config (off unless
  `port` is set) serves `POST /ask` for callers such as a docs site's "Ask"
  button. The caller names a handle, a question and a caller-chosen thread
  id; same handle and thread share one fork, so follow-ups remember earlier
  turns, and an existing thread outlives its handle's revocation as chat
  threads do. Every route carries a shared secret (`X-Ask-Token`, read from
  `http.token_file`). It binds to loopback; a tunnel in front adds TLS. Its
  forks are read-only whatever the handle's access, since the caller picks
  the handle.
  Fields must be strings within their limits (over-limit is a 413); the
  per-user cooldown applies per `sender` (a 429 inside it). The
  response separates whether a turn ran from what it produced: a failed
  turn is a 502 carrying the relay's own error text, never an empty answer.
- **Swappable backends (default `headless`):** `headless` (`claude -p`, clean
  JSON I/O, more reliable, no tmux) and `tmux` (interactive forks you can watch
  live with `agent-tunnel watch`).

## Architecture

```
   inside any Claude session:  >share  ──► UserPromptSubmit hook
                                              writes registry.json
                                              { handle -> session_id, cwd }
                                                        │
Discord / Mattermost channel+threads (teammates)        │ reads
        │  outbound websocket (Gateway / MM websocket)   ▼
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
  (`derive_handle`, `sanitize_label`). Records carry `config_dir`; old records
  backfill it from `transcript_path` on read.
- `config.py` — TOML config (Discord, Mattermost, limits, backend). No
  project/session here. `discord.token_file` / `mattermost.token_file` read
  the bot token from a file (no export); `claude.auto_trust` /
  `trust_config_path` control folder pre-trust; `AGENT_TUNNEL_REGISTRY` env
  overrides the registry path (hook + daemon honor it). Use an absolute path
  or `~/...`: a relative value is anchored differently by the standalone hook
  (its own cwd) and the daemon (the config-file dir), so the two would point
  at different files.
- `store.py` — daemon state: `thread_key → ThreadRecord` (handle, expert
  session id, project dir, **config dir**, fork id, tmux window, platform,
  the expert transcript size the fork was taken from, and a trimmed Q&A
  history for the re-fork recap). `bind()` records a pending thread before
  the first answer; fork id filled on completion. Each mutation re-reads the
  file under a cross-process lock and merges, so a CLI command
  (`forget`/`rename`) and the live daemon don't clobber each other's writes.
- `locking.py` — best-effort `fcntl` advisory file lock (`<path>.lock`) shared
  by the store, the registry, and the standalone `>share` hook (same
  `registry.json.lock`), serializing concurrent read-modify-write across
  processes. No-ops where `fcntl` is unavailable.
- `session.py` — transcript-dir resolution (per config dir) and
  fork-transcript parsing (locate the question, detect turn completion,
  extract the answer).
- `tmux.py` — minimal ops on a DEDICATED private tmux server
  (`tmux -L <socket>`), isolated from the user's main server (own fd budget,
  out of their `tmux ls`): named windows (`<handle>-<short>`), bracketed
  paste, Enter-with-verify, idle detection, liveness/reaping.
- `trust.py` — pre-trust a folder in the right config's `.claude.json`
  (surgical, atomic) so the interactive fork doesn't hit the trust dialog.
- `paths.py` — per-thread filesystem layout for the attachment round-trip:
  inbound upload dir (under the state dir), outbox dir (`.agent-tunnel-out/`
  inside the project, with a `*` `.gitignore` guard), and the pure
  snapshot/diff + question-preamble helpers. Stdlib-only and unit-tested.
- `convert.py` — best-effort conversion of attached Office files into a
  `Read`-openable format, using whatever converter is on `PATH`
  (LibreOffice→PDF / pandoc→md / textutil→txt) or an owner-set command. A clean
  no-op when none is installed; never a hard dependency.
- `backends.py` — `HeadlessBackend` / `TmuxBackend` behind one interface; both
  read the thread's binding to decide what to fork, and pin the fork to the
  session's config dir via `CLAUDE_CONFIG_DIR`. Per turn they expose the
  upload dir via `--add-dir`, append the outbox instruction to the persona for
  write/bash handles, and diff the outbox to collect deliverables onto
  `Answer.attachments`. `HeadlessBackend.ask` decides per follow-up whether to
  re-fork (see Key design decisions); `build_recap` keeps the newest turns
  that fit `recap_max_chars`, cutting a single oversize turn.
- `relay.py` — the platform-neutral turn logic shared by both bots: `Relay`
  (per-thread locks, the global concurrency cap, cooldowns, attachment
  ingest, the backend call, chunking, long answers as `answer.md`,
  deliverables, `!done` teardown, idle reaping, `!list` text) driven through
  small `Destination` (where replies go) and `Upload` (a file to fetch)
  protocols. Prefixes each relayed message with its sender
  (`<name> (via <platform>) says: …`) so the fork knows who is asking; the
  persona (`DEFAULT_PERSONA`, with a `{platform}` placeholder) tells the fork
  it is in bot mode. The platform is recorded per thread by the front-end
  that opened it; `[tunnel] platform` (default `Discord`) is the fallback.
- `discord_bot.py` — Discord event routing only: handle→thread,
  `!list`/`!handles`, `!done`/`!close`/`!end`, `<handle>: <question>` thread
  names, user/role allowlists, optional DMs, 2000-char messages;
  `token_file` resolution.
- `http_frontend.py` — the `[http]` front-end: an `aiohttp` app with
  `GET /health` and `POST /ask`, a `CollectDest` that gathers a relay
  turn's chunks (or its `answer.md`) into one string and keeps the relay's
  problem lines apart as errors, and `http_ready` / `run_http` for `serve`.
- `mattermost_bot.py` — Mattermost event routing only, at parity with
  Discord: a small REST + WebSocket client on `aiohttp`, a pure `route_post`
  decision, ~16000-char messages, `allowed_user_ids`. No DM support.
- `serve.py` — `plan_frontends` picks what runs (Discord when its token
  resolves and it has channels or DMs enabled, Mattermost when
  `[mattermost] url` is set; an incomplete Mattermost is skipped with a
  warning, and it errors only if nothing can run); `serve_all` runs them on
  one loop with one shared `Relay`
  and reaper.
- `cli.py` — `serve | ask | published | forks | resume | rename | status | watch |
  doctor | forget | init | help`. `forks` lists fork sessions (handle, asker,
  last active, turn count, fork id) from the store; `resume <handle>` execs
  `claude --resume <fork-id>` in the fork's project + config dir (most recent
  by default, `--fork <id>` to pick another) — using recorded fork ids to
  dodge the duplicate-name problem in `/resume`. `watch` attaches to the
  private tmux server; `doctor` runs a readiness checklist (Mattermost token
  and channels too, when `[mattermost] url` is set); `published` tags
  each handle with its config dir; `help` prints extensive per-command help.

## `>share` semantics

- `>share` — publish this session; mint/show a handle (default: short slug of
  the session id; idempotent — re-running returns the same handle).
- `>share <label>` — publish with a chosen handle (validated, deduped).
- `>share --write <label>` / `>share --read <label>` — set per-handle access
  (write = read tools + Write/Edit/NotebookEdit, never Bash; read = read-only).
  Re-sharing without a flag preserves the current level; re-sharing **with** a
  level flag changes an already-running thread on its next message (same fork,
  context kept), because the daemon re-reads the handle's current registry
  access each turn in `_require_binding` and syncs it onto the ThreadRecord
  (`TunnelStore.set_access`) — but only when the registry record is still the
  same bound session (`session_id == expert_session_id`), so a handle-name
  collision (e.g. the `cli` direct-path sentinel) can't hijack a thread's
  access. The access level rides on the registry record → ThreadRecord →
  `build_claude_flags(access=…)`.
- `>share --dangerously-allow-bash <label>` — write tools **plus
  `Bash`/command execution**, so a fork can build real PDFs/docx (e.g. via
  pandoc) as deliverables. It removes the read-only sandbox — grant it only to
  fully trusted colleagues.
- `>share --dangerously-skip-permissions <label>` — the top level (`"all"`):
  the fork launches with `--dangerously-skip-permissions`, so it can use **any
  tool or MCP server** the session has (web search, the browser via
  chrome-devtools, shell, file edits) with **no permission prompts**. It only
  takes effect when the owner also sets `[claude] allow_skip_permissions =
  true` (a deliberate double opt-in); otherwise the daemon refuses the turn
  with a clear message. Access escalates linearly: read ⊂ write ⊂ bash ⊂ all.
- `>share status` — show this session's handle, if any.
- `>share off` — revoke (new threads can't open; existing forks keep working,
  since a fork holds its own copy of history).

## Routing (handle-opens-a-thread)

- Watched-channel message `\<handle\> [question]`: if the handle is live, open
  a public thread (named `\<handle\>: \<question\>`), bind
  `thread → {handle, session_id, cwd, config_dir}`, and answer the question
  (or post a ready notice).
- `!list` / `!handles` (channel, thread, or DM): post the active handles +
  their project names.
- Thread message: a follow-up routed to the bound fork — unless it opens with
  a mention of someone other than the bot (`@teammate`, a role, or
  `@everyone`/`@here`), which is read as human side-chat and silently ignored.
  A leading `@bot` is fine (stripped, then answered).
- `!done` / `!close` / `!end` in a thread (or DM): tear down that fork
  immediately (kill window, drop binding, confirm).
- DMs (Discord only; optional, off by default): `\<handle\> …` (re)binds the
  DM; bare text follows up.

Mattermost routes the same way, with Mattermost's thread model: a root post
`\<handle\> [question]` in a watched channel (26-char `channel_ids`) opens a
thread keyed `mm:<root post id>`, and the bot answers as replies under it.
Replies in a bound thread are follow-ups; a reply opening with
`@someone-else`, `@all`, `@channel` or `@here` is side-chat and ignored, and
a leading `@bot` is stripped. `!list`/`!handles` and `!done`/`!close`/`!end`
work as on Discord. Direct and group messages are ignored.

## File attachments (round-trip)

A thread can carry files both ways, so a colleague can hand the expert a
document and get a generated deliverable back.

**Inbound (any handle).** When a colleague attaches files to a message
(text optional), the bot downloads each into a per-thread dir *outside* any
repo — `<state>/uploads/<thread>/` — and prepends the absolute paths to the
question. The fork is launched with that dir on `--add-dir`, so the read-only
`Read` tool (which handles PDF/image/markdown natively) can open them. The
upload dir is always `--add-dir`'d (even empty) so files dropped into a *warm*
follow-up window stay readable. Per-file size and count caps
(`limits.max_attachment_mb`, `limits.max_attachments`) apply; oversized/excess
files are skipped with a heads-up.

`Read` can't open binary Office files (`.docx`/`.pptx`/`.xlsx`), so those are
**best-effort converted** to a readable format using whatever converter is on
the host's `PATH` — fidelity-ordered **LibreOffice→PDF** (preferred; `Read`
renders PDF pages visually), else **pandoc→Markdown** (media extracted), else
macOS **textutil→text**. No converter is a hard dependency: with none present
the colleague is told to send a PDF, and PDF/images/text always work unaided.
Controlled by `[attachments] convert = "auto" | "off"` (plus a `convert_command`
escape hatch). See `convert.py`.

**Outbound (write/bash handles only).** A write/bash fork is told — via a
per-thread line appended to the persona — to save anything it wants to deliver
into its outbox: `<project>/.agent-tunnel-out/<thread>/`. The bot snapshots
that dir before the turn and diffs it after, posting whatever was created or
modified as chat attachments. Diffing the directory (rather than parsing the
transcript for `Write` tool calls) means a **Bash-generated** PDF is delivered
just like a `Write`-tool markdown file. A `.gitignore` holding `*` is dropped
at `.agent-tunnel-out/` so deliverables never dirty the owner's `git status`.
Read handles can't write, so they produce nothing outbound.

Both per-thread dirs are removed on `!done`/`forget` (best-effort).

## Config-dir awareness (multiple `CLAUDE_CONFIG_DIR`s)

A session created under one config dir (e.g. work `~/.claude-rja`) can't be
resumed/forked by a daemon running under another (e.g. personal `~/.claude`):
its transcript and folder-trust live under the originating dir. So the config
dir is propagated end to end:

- `>share` derives it path-agnostically from the hook's `transcript_path`
  (`<config-dir>/projects/...`) and records it on the registry record.
- The daemon pins each fork to that dir via `CLAUDE_CONFIG_DIR`, searches that
  dir's `projects/` for the transcript, and pre-trusts that dir's
  `.claude.json`. Work sessions thus fork under the work config/account,
  personal under personal — automatically.

## Cleanup

- Primary: colleagues close threads with `!done` (immediate teardown).
- Backstop: a reaper kills forks idle longer than `pane_idle_ttl_min`
  (default 180 min; `0` disables) so abandoned threads can't exhaust the
  private server's own fd budget.
- Owner: `agent-tunnel forget`, `tmux -L <socket> kill-server`, or stop serve.

## Backends (server modes)

- `headless` (default): `claude -p` per question with clean JSON in/out — no
  terminal scraping, no submit-timing heuristics, no tmux. Answer is the JSON
  `result`; "done" is the process exit (definitive), and errors surface via
  `is_error` + exit code. Launch: `agent-tunnel serve`.
- `tmux`: one interactive `claude` per thread in a window of the private tmux
  server, watchable live (`agent-tunnel watch`). Launch: `agent-tunnel serve
  --backend tmux`. Submission is hybrid to dodge Claude's slow-to-accept input
  right after a cold launch: a new fork (or a follow-up whose window was
  reaped) is launched with the question as claude's positional prompt
  (`claude --resume <id> [--fork-session] … -- "<q>"`), so claude auto-submits
  it once ready — no simulated keystrokes; a warm follow-up window is reused via
  bracketed paste + verified Enter. The answer is read from the fork's JSONL
  transcript (located by a per-turn `[ref:…]` marker) either way.
- Both run on your logged-in `claude` (no API key needed); do NOT auto-add
  `--bare` (it skips user config and breaks subscription auth). Each *new*
  thread replays the expert session's full context (~its token count per cold
  question) — fine for Q&A.

## Security model

- Read-only tools by default, hard-enforced by the CLI permission layer.
  `>share --write` adds file edits (still no Bash); `>share
  --dangerously-allow-bash` additionally permits command execution and so
  drops the sandbox — reserve it for fully trusted colleagues.
- Access = channel membership + optional allowlists (Discord user/role ids,
  Mattermost user ids). Anyone who can ask can surface anything in the session
  context or readable project tree — publish accordingly; the persona
  discourages leaking secrets but is a soft layer only.
- Inbound attachments land in a contained per-thread dir exposed via
  `--add-dir`; uploaded filenames are sanitized to a basename (no path
  traversal). Outbound delivery is limited to files the fork places in its
  outbox, so unrelated project files are never auto-posted.
- ToS: consumer plans prohibit making your *account* available to others; an
  owner-operated relay is a gray area. Headless + API key is the unambiguous
  path.

## Validation status

- Unit tested (real files, no mocks): registry roundtrip/revoke, the `>share`
  hook via subprocess (publish/idempotent/distinct-session/label/collision/
  status/off, write + bash access), store bind+follow-up, session discovery &
  answer extraction, chunking, flag building (incl. bash tools, `--add-dir`,
  persona append), config, attachment layout/diff/preamble + the backend's
  upload/outbox setup and cleanup, filename sanitization; the re-fork
  decision (unchanged expert resumes, grown expert re-forks with a recap,
  `refresh_forks = false`, legacy records, recap budget, bounded history);
  Mattermost post parsing, `route_post` (open/list/unknown handle,
  follow-ups, side-chat, allowlist, ignored DMs/bots/other channels),
  readiness + `plan_frontends`, and a relay turn (chunking, `answer.md`,
  `!done`).
- Live end-to-end (headless): `>share` → registry → `ask --handle` forked the
  exact published session, inherited context, and follow-ups continued the
  same fork.
- Live end-to-end (tmux, over Discord): `>share <label>` → handle posted in a
  private Discord channel → bot opened a thread → cold launch-with-prompt
  auto-submitted, answer captured from the transcript and posted back;
  follow-ups and `!done` exercised. Confirmed against a private `-L` tmux
  server while the main server was fd-saturated.

## Roadmap

- v1.5: web chat page on the same core (behind Tailscale / Cloudflare Access).
- v2: remote MCP face (`ask_expert`) so colleagues' own Claude can consult a
  published session; needs a public endpoint + OAuth.
- Hardening: detect untrusted folders in the tmux backend (issue #80).
