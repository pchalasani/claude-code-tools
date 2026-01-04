Guide the user through installing the claude-code-tools suite. Be informative and
explain what each tool does before asking if they want it.

## Step 1: Check What's Already Installed

Run these checks:

```bash
command -v uv && uv --version
command -v cargo && cargo --version
command -v brew && brew --version
command -v node && node --version
command -v aichat && aichat --version
command -v aichat-search && aichat-search --version
claude plugin list 2>/dev/null | grep cctools || echo "No cctools plugins"
```

Report what's installed vs missing. Skip already-installed items in later steps.

## Step 2: Prerequisites

If **Node.js** is missing: "Node.js 16+ is required for the interactive action
menus (resume, export, trim options). Install from https://nodejs.org/ or your
package manager."

If **uv** is missing: "uv is a modern, fast Python package manager. It's needed
to install the main Python package."
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Step 3: Install CLI Tools

Explain: "The `claude-code-tools` Python package includes 4 CLI commands:

1. **aichat** - Session management for Claude Code and Codex CLI. Key features:
   - **Resume without compaction**: When context fills up, clone and trim your
     session or roll over to a fresh session with lineage pointers back to the
     parent. No lossy compaction.
   - **Fast full-text search**: Find any past session by keyword using a
     Rust/Tantivy-powered search TUI. Way better than Claude Code's built-in
     search which only searches session titles.
   - **Session lineage**: All trimmed/rolled-over sessions maintain links to
     parent sessions, so context is never truly lost.

2. **tmux-cli** - Terminal automation for AI agents ('Playwright for terminals').
   Lets Claude Code control other terminal panes: run interactive scripts, use
   debuggers like pdb, even launch and interact with other Claude instances.

3. **vault** - Encrypted backup for .env files across all your projects using
   SOPS/GPG. Run `vault sync` to backup or restore, `vault list` to see all
   project backups.

4. **env-safe** - Safely inspect .env files without exposing actual values.
   Shows which keys exist, which are empty, validates syntax. Works with the
   safety-hooks plugin that blocks direct .env access.

Do you want to install these CLI tools?"

If yes:
```bash
uv tool install claude-code-tools
```

## Step 4: Install aichat-search (Rust binary)

Explain: "The `aichat-search` binary powers the fast full-text search in
`aichat search`. It's built with Rust and Tantivy (like Elasticsearch but
faster). Without it, `aichat search` won't work, but other aichat commands
(resume, trim, rollover) still function.

Do you want to install aichat-search?"

If yes, ask which method:
- **Homebrew** (easiest): `brew install pchalasani/tap/aichat-search`
- **Cargo** (compiles from source, ~5 min): `cargo install aichat-search`
- **Pre-built binary**: Download from https://github.com/pchalasani/claude-code-tools/releases

## Step 5: Install Plugins

Explain: "Plugins add skills, hooks, and agents to Claude Code. Here's what each
provides:"

**aichat plugin:**
"Adds session management capabilities to Claude Code:
- **`>resume` hook**: Type `>resume` when context is filling up. It copies your
  session ID to clipboard and tells you to run `aichat resume <paste>` to
  continue with trim or rollover options.
- **`/recover-context` command**: Extract context from parent sessions when you're
  in a rolled-over session.
- **`session-searcher` agent**: A sub-agent that searches past sessions for you
  without bloating your main context.

Do you want to install the aichat plugin?"
```bash
claude plugin install "aichat@cctools-plugins"
```

**tmux-cli plugin:**
"Adds the `/tmux-cli` skill for terminal automation. Use it to:
- Test interactive scripts that need user input
- Debug with pdb/gdb in another pane while Claude watches
- Launch another Claude Code instance for specialized help
- Coordinate with browser automation for UI testing

Do you want to install the tmux-cli plugin?"
```bash
claude plugin install "tmux-cli@cctools-plugins"
```

**workflow plugin:**
"Adds productivity skills and agents:
- **`/code-walk-thru`**: Walk through files in your editor to explain code or
  show changes you made.
- **`/log-work`**: Log work progress to `WORKLOG/YYYYMMDD.md` files.
- **`/make-issue-spec`**: Create task specification documents.
- **`ui-tester` agent**: Browser-based UI testing via Chrome DevTools MCP.

Do you want to install the workflow plugin?"
```bash
claude plugin install "workflow@cctools-plugins"
```

**safety-hooks plugin:**
"Adds protective hooks that block or require approval for risky operations:
- Blocks `rm -rf` on critical paths
- Blocks `git add -A` and `git add .` (requires explicit file staging)
- Blocks reading/writing `.env` files (suggests `env-safe` instead)
- Blocks reading files >500 lines (prevents context bloat)
- Requires approval for `git commit`

Do you want to install the safety-hooks plugin?"
```bash
claude plugin install "safety-hooks@cctools-plugins"
```

## Step 6: Summary

Summarize what was installed and give quick-start tips:

- `aichat search "keyword"` - search past sessions
- `>resume` - trigger session handoff when context fills up
- `aichat resume` - resume most recent session for current project
- `tmux-cli --help` - terminal automation commands
- `vault sync` - backup/restore .env files
- `env-safe list` - see .env keys without exposing values

## Rules

- NEVER install anything without explicit user permission
- ALWAYS explain what each tool does before asking
- If something is already installed, say so and skip it
- If user declines, skip and continue to next item
