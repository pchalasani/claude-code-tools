---
name: setup
description: Guided installation of all claude-code-tools CLI commands and plugins.
  Walks through each tool, explains what it does, and asks permission before installing.
---

# cctools:setup

This skill guides you through installing the complete claude-code-tools suite.
You'll be asked about each component - install only what you need.

## What Can Be Installed

### CLI Tools (Python package: `claude-code-tools`)

| Command | What it does |
|---------|--------------|
| `aichat` | Session search, resume, trim, rollover - continue work without compaction |
| `tmux-cli` | Terminal automation for AI agents ("Playwright for terminals") |
| `vault` | Encrypted .env backup and sync across projects |
| `env-safe` | Safely inspect .env files without exposing values |

### Rust Binary (optional)

| Binary | What it does |
|--------|--------------|
| `aichat-search` | Fast full-text search TUI for sessions (powers `aichat search`) |

### Plugins (choose which you want)

| Plugin | What it adds |
|--------|--------------|
| `aichat` | `>resume` hook, `/session-search`, `/recover-context`, session-searcher agent |
| `tmux-cli` | `/tmux-cli` skill for terminal automation |
| `workflow` | `/code-walk-thru`, `/log-work`, `/make-issue-spec`, ui-tester agent |
| `safety-hooks` | Hooks blocking dangerous rm, git add -A, .env access, large file reads |

## Instructions

Follow these steps. ALWAYS explain what each tool does and ask permission before
installing anything.

### Step 1: Check Current State

Run these checks and summarize for the user:

```bash
# Prerequisites
command -v uv && uv --version
command -v cargo && cargo --version
command -v brew && brew --version
command -v node && node --version

# CLI tools
command -v aichat && aichat --version
command -v tmux-cli && tmux-cli --help | head -1
command -v vault && vault --help | head -1
command -v env-safe && env-safe --help | head -1

# Rust binary
command -v aichat-search && aichat-search --version

# Installed plugins
claude plugin list 2>/dev/null | grep cctools || echo "No cctools plugins found"
```

Tell the user what's already installed and what's missing.

### Step 2: Install Prerequisites

For each missing prerequisite, explain and ask permission:

**uv** (Python package manager):
- What: Modern, fast Python package manager from Astral
- Why needed: Installs the claude-code-tools Python package
- Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- After: User may need to restart shell or `source ~/.local/bin/env`

**cargo** (Rust toolchain) - only if user wants aichat-search:
- What: Rust compiler and package manager
- Why needed: Compiles aichat-search from source (alternative: homebrew)
- Install: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- After: User needs to restart shell or `source "$HOME/.cargo/env"`

**Node.js 16+**:
- What: JavaScript runtime
- Why needed: Powers the action menus in aichat (resume, export, etc.)
- Install: Suggest `brew install node` or https://nodejs.org/

### Step 3: Install CLI Tools

Ask: "Do you want to install the CLI tools (aichat, tmux-cli, vault, env-safe)?"

Explain: These are bundled together in one Python package. You get all 4 commands.

If yes:
```bash
uv tool install claude-code-tools
```

Verify:
```bash
aichat --help | head -3
```

### Step 4: Install aichat-search (Optional)

Ask: "Do you want fast full-text session search? This requires the aichat-search
Rust binary."

Explain: Without this, `aichat search` won't work, but other aichat commands
(resume, trim, rollover) still function.

If yes, offer three options:

**Option A - Homebrew** (easiest if brew is installed):
```bash
brew install pchalasani/tap/aichat-search
```

**Option B - Cargo** (compiles from source, ~5 min):
```bash
cargo install aichat-search
```

**Option C - Pre-built binary**:
Direct user to: https://github.com/pchalasani/claude-code-tools/releases
(look for `rust-v*` releases)

Verify:
```bash
aichat-search --version
```

### Step 5: Install Plugins

For each plugin, explain what it provides and ask if they want it:

**aichat plugin:**
- Provides: `>resume` hook (type in Claude Code to trigger handoff),
  `/session-search` and `/recover-context` skills, `session-searcher` agent
- Best for: Continuing work across sessions, searching past work
- Install: `claude plugin install "aichat@cctools-plugins"`

**tmux-cli plugin:**
- Provides: `/tmux-cli` skill for terminal automation
- Best for: Interacting with other terminal panes, debugging with pdb,
  launching other AI agents
- Install: `claude plugin install "tmux-cli@cctools-plugins"`

**workflow plugin:**
- Provides: `/code-walk-thru`, `/log-work`, `/make-issue-spec` skills,
  `ui-tester` agent
- Best for: Code reviews, work logging, task specs, UI testing
- Install: `claude plugin install "workflow@cctools-plugins"`

**safety-hooks plugin:**
- Provides: Hooks blocking dangerous operations
- Blocks: `rm -rf`, `git add -A`, `.env` access, reading huge files
- Best for: Preventing accidental destructive commands
- Install: `claude plugin install "safety-hooks@cctools-plugins"`

### Step 6: Summary

After installation, summarize:

1. What was installed
2. Quick tips for getting started:
   - `aichat search "keyword"` - search past sessions
   - `>resume` - trigger session handoff when context fills up
   - `tmux-cli --help` - see terminal automation options
   - `vault sync` - backup/restore .env files
   - `env-safe list` - inspect .env without exposing values

## Constraints

- NEVER install anything without explicit user permission
- ALWAYS explain what each tool does before asking to install
- If user declines a component, skip it and continue with the rest
- If any installation fails, show the error and suggest alternatives
- Remind user to restart shell if uv or cargo was just installed
