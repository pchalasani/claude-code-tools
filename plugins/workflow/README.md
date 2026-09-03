# workflow

A collection of skills and agents to enhance developer workflow with Claude Code.

## Skills

### 1. code-walk-thru

Walk through code files in your editor to explain how code works or show changes
you've made.

**How it works:**

- Claude opens files in your editor (VSCode, Cursor, etc.) at specific line numbers
- Walks through files one by one, waiting for you to confirm before moving on
- Great for reviewing Claude Code's code changes, or understanding a code-base.

**Example commands:**

```bash
# VSCode
code --goto src/main.py:42

# Cursor
cursor --goto src/main.py:42
```

### 2. log-work

Log work progress to `WORKLOG/YYYYMMDD.md` files.

**How it works:**

- Creates/appends to a daily worklog file
- Each entry has a timestamp and concise topic
- Includes session ID, files created/read, and short description
- Follows progressive disclosure - references detailed docs instead of duplicating

**Example entry:**

```markdown
# 13:45 Added feature xyz

- Session: abc-123
- Created: src/feature.py
- Read: docs/spec.md
- Added new authentication middleware
```

### 3. make-issue-spec

Create task specification documents at `issues/YYYYMMDD-topic.md`.

**How it works:**

- Creates a markdown document describing a specific task
- Includes concise implementation plan
- Claude asks clarifying questions for underspecified parts
- Stages the file in git if permissions allow

## Agents

### ui-tester

A specialized agent for browser-based UI testing and validation using Chrome
DevTools MCP Server.

**When to use:**

- Verify that a new feature renders correctly in the browser
- Check responsive design at different viewport sizes
- Validate CSS changes look correct
- Inspect for console errors or network issues

**How it works:**

- Runs in isolation to prevent context pollution in the main agent
- Uses Chrome DevTools MCP Server for all browser interactions
- Takes screenshots, inspects DOM elements, checks console errors
- Returns structured reports with findings organized by severity

**Capabilities:**

- Navigate to URLs and local dev servers
- Inspect DOM elements and CSS properties
- Capture screenshots at various viewport sizes
- Check for console errors and network issues
- Validate responsive behavior and accessibility

## Hooks

### Codex review-loop nudge (`hooks/pr_review_nudge.py`)

A nudge, never a gate: it reminds the agent to run the GitHub Codex
review loop on its own pull requests — monitor the review, address or
defer each finding, and **request a fresh review after every push** —
without ever blocking work.

- **After `gh pr create` or a `git push` to a branch with an open PR**
  (PostToolUse on Bash): injects a one-line reminder.
- **When the agent tries to end its turn** (Stop): asks GitHub for open
  PRs by you whose head branch is checked out in a local worktree of
  this repo. For each one whose current head has no completed Codex
  review, or which has unresolved review threads, it interrupts the stop
  **once** with the details; the next stop in the same state passes
  silently. The agent then decides — fix, defer to an issue, or dismiss
  with a reply. It checks GitHub rather than pattern-matching commands,
  so a PR created via `gh api`, by a subagent, or by hand is covered.

Speed and safety: a handful of `gh` calls with short timeouts, results
cached for a minute, and anything that fails — not a git repo, no GitHub
remote, `gh` missing or offline — makes it silent rather than slow.
State lives in `~/.local/state/codex-review-nudge/` (override with
`CODEX_REVIEW_NUDGE_STATE`).

**Codex CLI** speaks the same hook payloads. Register the script by
absolute path in `~/.codex/hooks.json` (Codex hash-trusts the entry
script, so keep it a single file, and re-trust via `/hooks` after edits):

```json
{"hooks": {
  "PostToolUse": [{"matcher": "^(Bash|shell)$", "hooks": [{"type": "command",
    "command": "python3 /ABS/PATH/plugins/workflow/hooks/pr_review_nudge.py",
    "timeout": 15}]}],
  "Stop": [{"hooks": [{"type": "command",
    "command": "python3 /ABS/PATH/plugins/workflow/hooks/pr_review_nudge.py",
    "timeout": 20}]}]
}}
```

## Installation

No additional dependencies required for skills. The ui-tester agent requires the
Chrome DevTools MCP Server to be configured.
