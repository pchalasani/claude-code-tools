# cctools

Setup wizard for the claude-code-tools suite.

## What it does

This plugin provides the `/cctools:setup` command that walks you through
installing all CLI tools and plugins. Claude explains what each tool does
and asks permission before installing.

## Usage

After installing this plugin, run:

```
/cctools:setup
```

Or just ask Claude: "set up my claude-code-tools"

## What Gets Installed

**CLI tools** (Python package):
- `aichat` - session search, resume, trim, rollover (continue work without compaction)
- `tmux-cli` - terminal automation ("Playwright for terminals")
- `vault` - encrypted .env backup/sync
- `env-safe` - safely inspect .env files

**Rust binary** (optional):
- `aichat-search` - fast full-text session search TUI

**Plugins** (choose which you want):
- `aichat` - `>resume` hook, session-searcher agent, /recover-context
- `tmux-cli` - /tmux-cli skill for terminal automation
- `workflow` - /code-walk-thru, /log-work, /make-issue-spec, ui-tester agent
- `safety-hooks` - blocks dangerous rm, git add -A, .env access
