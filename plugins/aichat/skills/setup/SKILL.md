---
name: setup
description: Install the aichat tools (claude-code-tools Python package and aichat-search
  Rust binary) with guided dependency installation.
---

# aichat:setup

This skill guides installation of the `aichat` tools that power session search,
resume, and other features of this plugin.

## What Gets Installed

1. **Prerequisites** (if missing):
   - `uv` - Python package manager
   - `cargo` (Rust) OR `brew` (Homebrew) - for installing aichat-search
   - Node.js 16+ - for action menus

2. **Tools**:
   - `claude-code-tools` - Python package (via uv)
   - `aichat-search` - Rust binary for fast full-text session search

## Instructions

Follow these steps carefully. ALWAYS ask for user permission before installing
anything.

### Step 1: Check What's Already Installed

Run these checks and report the results to the user:

```bash
# Check uv
command -v uv && uv --version

# Check cargo
command -v cargo && cargo --version

# Check brew (macOS/Linux)
command -v brew && brew --version

# Check Node.js
command -v node && node --version

# Check if claude-code-tools is installed
command -v aichat && aichat --help | head -5

# Check if aichat-search is installed
command -v aichat-search && aichat-search --version
```

Report to the user what's installed and what's missing.

### Step 2: Install Prerequisites (with permission)

For each missing prerequisite, explain what it is and ask permission to install:

**If uv is missing:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
After installation, user may need to restart their shell or run:
```bash
source ~/.local/bin/env  # or similar, depending on shell
```

**If cargo is missing (and user prefers Rust):**
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```
After installation, user needs to restart their shell or run:
```bash
source "$HOME/.cargo/env"
```

**If Node.js is missing:**
Suggest the user install Node.js 16+ via their preferred method:
- macOS: `brew install node`
- Or download from https://nodejs.org/

### Step 3: Install claude-code-tools Python Package

Once uv is available:

```bash
uv tool install claude-code-tools
```

This installs the `aichat`, `tmux-cli`, `vault`, and `env-safe` commands.

### Step 4: Install aichat-search Rust Binary

The user has three options. Ask which they prefer:

**Option A: Homebrew (macOS/Linux, easiest if brew is installed):**
```bash
brew install pchalasani/tap/aichat-search
```

**Option B: Cargo (compiles from source, ~5 minutes):**
```bash
cargo install aichat-search
```

**Option C: Pre-built binary:**
Direct user to download from:
https://github.com/pchalasani/claude-code-tools/releases (look for `rust-v*` releases)

### Step 5: Verify Installation

Run these commands to verify everything works:

```bash
# Verify aichat command
aichat --help

# Verify aichat-search (should show version)
aichat-search --version

# Quick test of search (may show "no sessions" if new install)
aichat search --help
```

### Step 6: Report Success

Tell the user:

1. What was installed
2. That the `aichat` plugin features (session search, resume, etc.) are now ready
3. They can run `aichat search` to search past sessions
4. They can type `>resume` in Claude Code to trigger session handoff when
   context fills up

## Error Handling

- If any installation fails, show the error and suggest manual installation
- If user declines to install a prerequisite, explain which features won't work
- If aichat-search fails to install via cargo, suggest homebrew or pre-built binary

## Constraints

- NEVER install anything without explicit user permission
- ALWAYS explain what each tool does before asking to install
- If user is on Windows, note that some features may have limited support
- Don't assume shell environment is reloaded - remind user to restart shell
  if needed after installing uv or cargo
