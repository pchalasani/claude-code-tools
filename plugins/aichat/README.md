# aichat

Tools for searching, resuming, and recovering context from CLI coding agent
sessions (Claude Code, Codex CLI).

## Components

### Agent: `session-searcher`

Searches previous sessions for specific work, decisions, or code patterns.
Auto-invoked by Claude when you ask about past sessions. Returns concise
summaries without polluting main context.

**Triggers:** Ask naturally:

- "What did we work on yesterday?"
- "Find sessions where we discussed authentication"
- "What design decisions did we make for the API?"

**How it works:**

1. Runs `aichat search --json` to find matching sessions
2. Reads up to 3 session files for details
3. Returns formatted markdown summary

### Skills

#### `recover-context`

Extracts context from a parent session when resuming work. Uses session lineage
(shown in first user message) to find the most recent parent session.

**What it extracts:**

- Last task being worked on
- Current state (completed, in-progress, blocked)
- Pending items or next steps
- Associated docs (issue specs, work logs, design docs)

#### `session-search`

For CLI agents WITHOUT subagent support (e.g., Codex CLI). Provides the same
session search capability as the `session-searcher` agent, but as inline
instructions.

> **Claude Code users:** Don't use this directly. The `session-searcher` agent
> is more efficient.

### Command: `/recover-context`

Slash command that invokes the `recover-context` skill. Use when you've resumed
a session and want to quickly recover what you were working on.

### Hooks (Quick Commands)

Type these directly in Claude to trigger special actions:

| Command | What it does |
|---------|--------------|
| `>resume` | Copy session ID to clipboard + show resume instructions |
| `>continue` | Same as `>resume` |
| `>handoff` | Same as `>resume` |
| `>session` | Copy session ID to clipboard (simple confirmation) |
| `>session-id` | Same as `>session` |
| `>trim` | Preview an in-place trim of the current session (`>trim yes` applies) |

The resume commands guide you through continuing work in a new session using
`aichat resume <paste>`.

#### `>trim`: trim the current session in place

`>trim` slims down the *current* session without quitting Claude: it
truncates bloated tool results (and optionally long assistant messages) in
the session's JSONL file, keeping the same session ID.

**The typical workflow** is four steps:

```text
>trim        # preview savings — changes nothing
>trim yes    # apply — rewrites this session's file in place
# then quit (Ctrl+D twice) and resume:  claude -r <id>
```

The preview looks like:

```text
>trim
  Trim preview - threshold 500 chars, all tools:
    ~42,486 tokens would be saved (169,945 chars)
    29 tool results + 0 assistant messages trimmed
    File size: 1.1 MB -> 985.8 KB
  Apply:  >trim yes    Cancel:  >trim cancel
```

`>trim yes` applies it; `>trim cancel` abandons it; `>trim help` prints
usage without touching anything. A pending preview expires after ~10
minutes.

**Important:** trimming never shrinks the *live* context. Claude Code
loads the transcript once, at session start, and never re-reads it
mid-session, so the savings only take effect on the **next resume** — at
any utilization level. Run `>trim` *before* you hit the wall: once you
see `Context limit reached · /compact or /clear to continue`, the live
context is full and `>trim` cannot rescue it in place — quit and resume.

Options are shape-based and order-free (mix in any order):

- `-N` / `+N` -- assistant messages: keep the last N / trim the first N
  long ones
- bare number -- character threshold (default 500)
- words -- comma-separated tool names to trim (default: all tools)

Example: `>trim -20 800 bash,read` (keep last 20 long assistant
messages, 800-char threshold, only Bash/Read results).

A timestamped backup (`<id>.pre-trim-<ts>.jsonl.bak`) is kept next to the
session file, and truncation placeholders reference it.

Under the hood this runs `aichat trim-in-place`, usable directly on any
Claude session:

```bash
aichat trim-in-place <id> --dry-run     # preview tokens saved
aichat trim-in-place <id> -a -20 -l 800 # keep last 20 asst msgs, 800 cutoff
aichat trim-in-place <id> -t bash,read  # only Bash/Read results
```

Flags: `-l/--len` (threshold, default 500), `-a/--trim-assistant`
(positive N / negative N), `-t/--tools` (comma-separated),
`-n/--dry-run`, `--json`, `--claude-home`. Requires the `aichat` CLI on
PATH with the `trim-in-place` subcommand — install or update it with
`uv tool install --force claude-code-tools`. Claude sessions only.

## CLI Usage

### Searching sessions

```bash
# Interactive TUI search
aichat search "authentication bug fix"

# JSON output for scripting
aichat search --json -n 10 "authentication"

# Filter by project
aichat search --json -g "my-project" "query"

# See all options
aichat search --help
```

### Resuming sessions

```bash
# Resume a session (paste session ID from >resume command)
aichat resume <session-id>
```

### JSON output fields

When using `--json`, each result line contains:

- `session_id` - unique session identifier
- `agent` - claude or codex
- `project`, `branch`, `cwd` - project context
- `lines` - number of lines in session
- `created`, `modified` - timestamps
- `first_msg`, `last_msg` - first and last user messages
- `file_path` - path to session file
- `snippet` - matching text snippet

## Installation

This plugin requires the `claude-code-tools` and `aichat-search` packages:

```bash
uv tool install claude-code-tools   # Python package
cargo install aichat-search         # Rust search TUI
```

Prerequisites:

- Node.js 18+ - for action menus (resume, export, etc.)
- Rust/Cargo - for aichat search

If you don't have uv or cargo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                # uv
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh # Rust
```
