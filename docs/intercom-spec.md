# msg: Inter-Agent Communication System

## Spec / Implementation Plan

### Overview

`msg` enables structured, asynchronous communication between
coding agent sessions (Claude Code, Codex CLI) running in tmux
panes. Agents send and receive messages through a shared SQLite
database, with a watcher daemon handling delivery notifications
via tmux-cli.

### Design Principles

- All messages flow through SQLite (single source of truth)
- tmux-cli is used only for typing lightweight notifications
  into agent panes (to wake them up or alert them)
- Full message content is visible in agent sessions as CLI
  output (via `msg inbox`)
- Thread-based model supports 1:1 and group conversations
- One unified flow (no separate "chat" vs "email" modes)
- Works equally for Claude Code and Codex CLI
- Agents send replies explicitly via `msg reply` (no screen
  scraping or capture)

### Components

1. **SQLite DB** (`~/.msg/msg.db`, WAL mode)

   - Agent registry (session ID, name, pane, agent kind)
   - Threads (id, title, participants)
   - Messages (id, thread_id, from, body, timestamp)
   - Delivery state machine per recipient per message
   - Watcher health/heartbeat

2. **`msg` CLI** (Python, Click-based)

   - `msg register <name>` — register this session
   - `msg thread create <title> --with B,C` — new thread
   - `msg send --thread=T1 "message"` — send to thread
   - `msg reply --thread=T1 "message"` — reply in thread
   - `msg inbox` — show unread messages
   - `msg inbox --thread=T1` — messages in a thread
   - `msg list` — list registered agents
   - `msg threads` — list active threads
   - `msg status` — check watcher health, stale agents
   - `msg watch` — start watcher daemon

3. **Watcher daemon** (`msg watch`)

   - Single async process monitoring the DB
   - Groups undelivered notifications by recipient
   - Consolidates: "2 new msgs in 'Auth review'
     (from architect, reviewer) -- run: msg inbox"
   - For each recipient: async wait_idle, then type
     notification into pane via tmux-cli
   - Updates delivery state in DB
   - Handles all threads and agents concurrently
     (asyncio.gather per recipient)
   - Writes heartbeat to DB so `msg status` can
     report watcher health
   - Warns on `msg send` if no active watcher

4. **Stop hook** (Claude Code only, supplements watcher)

   - Fires when Claude stops working
   - Checks DB for undelivered messages
   - Uses same claim protocol as watcher (claim →
     notify → update) to prevent double-notification
   - Types notification into own pane if messages pending
   - Faster delivery for Claude sessions between tasks
   - Not available for Codex — watcher is the only
     delivery path for Codex sessions

5. **Slash command** (`/register`)

   - User types `/register architect` inside a session
   - Expands to: `msg register architect --pane=$TMUX_PANE
     --agent=<auto-detected>`
   - Zero-friction registration from within any session

6. **Skill / instructions file**

   - Teaches agents how to use `msg` commands
   - Included in both Claude Code and Codex CLI configs

### Agent Identity

- **Session ID**: immutable UUID, assigned at registration.
  Used as FK throughout the schema. Survives name changes.
- **Name**: human-friendly alias, provided by user
  (e.g., "architect", "tester"). Mutable, unique within
  a tmux session scope.
- **Pane ID**: tmux pane_id (`%12`) — stable, survives
  pane renumbering.
- **Tmux session**: session name for scoping.
- **Tmux socket path**: for disambiguation across tmux
  servers.
- **Display address**: `session:window.pane` (for UI only).
- **Agent kind**: `claude` or `codex` (auto-detected).

### Registration UX

From inside an agent session (slash command):
```
/register architect
```

From inside an agent session (CLI, auto-detects pane):
```bash
msg register architect
```

From a plain shell (explicit pane):
```bash
msg register architect --pane=1.1 --agent=claude
msg register tester --pane=1.2 --agent=codex
```

Auto-register on first `msg` use if not yet registered
(assigns default name like `claude-1.1`):
```bash
msg send --to tester "review auth"
# → "Not registered. Auto-registering as 'claude-1.1'."
# → "Use 'msg register <name>' to set a friendly name."
```

### DB Schema

```sql
-- WAL mode enabled at DB creation
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE agents (
    session_id TEXT PRIMARY KEY,     -- immutable UUID
    name TEXT NOT NULL,              -- human-friendly alias
    pane_id TEXT NOT NULL,           -- tmux %id (stable)
    tmux_session TEXT NOT NULL,      -- tmux session name
    tmux_socket TEXT,                -- socket path
    display_addr TEXT,               -- session:window.pane
    agent_kind TEXT NOT NULL,        -- claude | codex
    pid INTEGER,
    cwd TEXT,
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(name, tmux_session,       -- names unique per
           tmux_socket)              -- tmux session+socket
);

CREATE TABLE threads (
    id TEXT PRIMARY KEY,             -- uuid
    title TEXT NOT NULL,
    created_by TEXT NOT NULL
        REFERENCES agents(session_id),
    created_at TEXT NOT NULL
);

CREATE TABLE thread_participants (
    thread_id TEXT NOT NULL
        REFERENCES threads(id),
    agent_id TEXT NOT NULL
        REFERENCES agents(session_id),
    PRIMARY KEY (thread_id, agent_id)
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,             -- uuid
    thread_id TEXT NOT NULL
        REFERENCES threads(id),
    from_agent TEXT NOT NULL
        REFERENCES agents(session_id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,             -- uuid
    message_id TEXT NOT NULL
        REFERENCES messages(id),
    recipient_id TEXT NOT NULL
        REFERENCES agents(session_id),
    state TEXT NOT NULL
        DEFAULT 'pending',
        -- pending: message written, not yet notified
        -- claimed: watcher picked it up
        -- notified: notification typed into pane
        -- read: agent ran msg inbox
        -- failed: delivery failed after retries
    claimed_by TEXT,                 -- watcher instance id
    claim_expires_at TEXT,
    notify_attempts INTEGER
        NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    notified_at TEXT,
    read_at TEXT,
    UNIQUE(message_id, recipient_id)
);

CREATE TABLE watcher_heartbeat (
    watcher_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    pid INTEGER NOT NULL
);
```

### Message Flow (detailed)

#### Sending

1. Agent A: `msg send --thread=T1 "What auth pattern?"`
2. CLI writes message row to `messages` table
3. CLI writes delivery rows (state=`pending`) for each
   participant except sender
4. CLI prints echo:
   `Sent to thread 'Auth review': What auth pattern?`
5. CLI checks watcher health — warns if no active watcher
6. CLI exits

#### Notification Delivery (watcher)

1. Watcher queries: deliveries where state=`pending`,
   grouped by recipient
2. For each recipient (concurrently via asyncio):

   a. Claim deliveries: set state=`claimed`,
      claimed_by=watcher_id, claim_expires_at=now+60s
   b. Consolidate into one notification summary
   c. `await wait_idle(recipient.pane)`
   d. Type notification into pane via tmux-cli:
      `[MSG] 2 new messages in 'Auth review'`
      `(from architect, reviewer) -- run: msg inbox`
   e. Set state=`notified`, notified_at=now
   f. On failure: increment notify_attempts, set
      last_error, reset state to `pending` (or `failed`
      after 3 attempts)

3. Release expired claims (other watchers can retry)
4. Write heartbeat to watcher_heartbeat table
5. Sleep 1s, repeat

#### Reading

1. Agent B: `msg inbox`
2. CLI queries messages where recipient is this agent
   AND state != `read` (regardless of notification state)
3. Prints formatted messages:
   ```
   Thread: Auth review
     architect (2m ago): What auth pattern should we use?

   Thread: Deploy plan
     devops (5m ago): Staging is ready
   ```
4. Updates delivery state to `read`, read_at=now

#### Replying

1. Agent B: `msg reply --thread=T1 "Use JWT..."`
2. Same as sending: writes to DB, creates delivery rows
3. Prints echo:
   `Reply in thread 'Auth review': Use JWT...`
4. Watcher picks up and notifies other participants

### Stale Agent Handling

- `last_seen` updated on every `msg` CLI call
- Liveness also validated by checking tmux pane existence
  (is the pane_id still alive?) and pid
- `msg list` shows stale agents as "(stale)"
- `msg register` with existing name in same tmux session
  re-registers (updates pane address, keeps session_id)

### Loop Prevention

- Notifications typed into panes are prefixed with `[MSG]`
- Skill instructions tell agents: "When you see a [MSG]
  notification, run `msg inbox` to read messages. Do not
  auto-reply to the notification text itself."

### Implementation Order

#### Phase 1: Schema + Identity
1. Define models (dataclasses) for agents, threads,
   messages, deliveries
2. SQLite DB setup with WAL mode (store.py)
3. Agent registration with immutable session_id,
   auto-detection of pane/session from env vars
4. Unit tests for store layer

#### Phase 2: Core CLI
5. `msg register` + `msg list`
6. `msg thread create`
7. `msg send` + `msg reply`
8. `msg inbox`
9. `msg threads`
10. Unit tests for CLI commands

#### Phase 3: Delivery
11. Watcher daemon — async, concurrent per recipient,
    claim-based delivery, heartbeat
12. `msg status` — watcher health, stale agents
13. Stop hook for Claude Code
14. Integration test: two-agent conversation

#### Phase 4: Plugin + UX
15. `/register` slash command
16. Skill/instructions file for agents
17. Plugin structure (plugin.json, hooks.json)
18. Auto-register on first use fallback

### File Structure

```
claude_code_tools/msg/
    __init__.py
    store.py          -- SQLite operations
    cli.py            -- Click CLI commands
    watcher.py        -- async delivery daemon
    models.py         -- dataclasses for agents,
                         threads, messages, deliveries

plugins/msg/
    .claude-plugin/
        plugin.json
    commands/
        register.md   -- /register slash command
    skills/
        msg/
            SKILL.md  -- agent instructions
    hooks/
        hooks.json
        stop_hook.py  -- inbox check on stop
```

### Dependencies

- sqlite3 (stdlib)
- click (already a dependency)
- asyncio (stdlib)
- tmux-cli (already exists in this repo)

### Resolved Design Decisions

1. **Immutable session ID as FK** — agent names are
   mutable aliases; all foreign keys use session_id.

2. **Delivery state machine** — not booleans. States:
   pending → claimed → notified → read (or failed).
   Supports watcher claims, retries, and crash recovery.

3. **Inbox reads all unread** — `msg inbox` shows
   messages where state != `read`, regardless of
   notification state. Works even if watcher is down.

4. **No reply_visibility for v1** — all thread
   participants see all replies. Simplify first.

5. **Notifications typed into pane** — safe for Claude
   Code and Codex CLI because their input prompt always
   accepts text, even while busy.

6. **Tmux socket path in schema** — disambiguates
   across tmux servers.

7. **Watcher heartbeat** — `msg send` warns if no
   active watcher. `msg status` shows watcher health.
