# aichat

Provides session search capability for CLI coding agents using the `aichat search`
command.

## What it does

This plugin enables searching through previous code-agent session JSONL files (from
Claude Code or Codex CLI) to find details of specific work done in past sessions.

- **Claude Code**: Uses the `session-searcher` subagent (auto-invoked, returns concise
  summaries without polluting main context)
- **Codex CLI and other agents**: Uses the `session-search` skill (instructions
  injected into context)

## Usage

Just ask naturally:
- "What did we work on yesterday?"
- "Find sessions where we discussed authentication"
- "What design decisions did we make for the API?"

Claude will automatically invoke the `session-searcher` subagent.

### Manual CLI usage

The `aichat search` command with `--json` flag returns JSONL-formatted results:

Example - find and examine the top matching session:
```bash
# Get the file path of the top matching session
aichat search --json "authentication bug fix" | head -1 | jq -r '.file_path'

# Then read/search through that session JSONL to understand what was done
```

Run `aichat search --help` to see all available options.

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

**Easiest:** Install the `cctools` plugin first and run `/cctools:setup` - it will
guide you through installing everything including this plugin.

**Manual installation:**

```bash
uv tool install claude-code-tools   # Python package
cargo install aichat-search         # Rust search TUI (or: brew install pchalasani/tap/aichat-search)
```

Prerequisites:

- Node.js 16+ - for action menus (resume, export, etc.)
- uv - Python package manager
- Rust/Cargo OR Homebrew - for aichat-search

If you don't have uv or cargo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                # uv
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh # Rust
```
