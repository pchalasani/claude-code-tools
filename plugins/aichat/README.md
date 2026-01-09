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

The resume commands guide you through continuing work in a new session using
`aichat resume <paste>`.

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

- Node.js 16+ - for action menus (resume, export, etc.)
- Rust/Cargo - for aichat search

If you don't have uv or cargo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                # uv
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh # Rust
```
