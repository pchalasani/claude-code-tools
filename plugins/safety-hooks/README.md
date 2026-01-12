Provides safety hooks to block (or require user approval) various commands.

## Hook Decision Types

The hooks in this collection use three decision types:

- **block** (deny): Hard block, command cannot proceed
- **ask**: Prompts user for approval in the UI before proceeding
- **allow** (approve): Command proceeds without intervention

## Safety Hooks

### 1. bash_hook.py (PreToolUse for Bash)

A unified Bash hook that combines multiple safety checks. It runs all sub-checks
and applies priority: block > ask > allow.

Uses `hookSpecificOutput` format with `permissionDecision` field to communicate
decisions to Claude Code.

**Sub-hooks it imports:**

#### 1a. rm_block_hook.py

- **Decision**: block
- **Trigger**: Any `rm` command (including `/bin/rm`, `/usr/bin/rm`)
- **Behavior**: Blocks deletion and suggests moving files to a TRASH/ directory
  instead, with logging in TRASH-FILES.md
- **Purpose**: Prevent accidental/permanent file deletion

#### 1b. git_add_block_hook.py

- **Decisions**: block, ask, or allow depending on context
- **Hard blocks** (blanket commands that add files indiscriminately, without
  explicit selection):

  - `git add -A`, `git add -a`, `git add --all` - stages everything including
    untracked files across the entire repo
  - `git add .` - stages entire current directory blindly
  - `git add ../` (parent directory patterns) - stages outside current scope
  - `git add *` (wildcard patterns) - shell expansion can match unexpected files
  - `git commit -a` without `-m` flag - would open an editor (not interactive)

- **Ask (user approval required)**:

  - Staging **modified** files (files already tracked that have changes)
  - Staging a directory that contains modified files

- **Allow without prompting**:

  - Staging **new/untracked** files (no approval needed)
  - `--dry-run` or `-n` flag (used internally)
  - Directories containing only new files

- **How it works**: Uses `git add --dry-run` to detect what would be staged,
  then checks `git status --porcelain` to distinguish new vs modified files.
- **Purpose**: Prevent accidental staging while allowing smooth workflow for
  new files

#### 1c. git_checkout_safety_hook.py

- **Decision**: block
- **Hard blocks** (these commands discard uncommitted work permanently with no
  recovery option):

  - `git checkout -f` / `--force` - forces checkout, discarding all local
    changes without warning
  - `git checkout .` - reverts ALL files in current directory to last commit
  - `git checkout <branch> -- .` - overwrites all files with version from
    another branch
  - `git checkout <branch> -- <file>` - overwrites specific file from another
    branch

- **Conditional blocks**: If there are uncommitted changes, blocks and shows
  list of modified files with safer alternatives (stash, commit, restore,
  switch)
- **Allows**: `git checkout -b` (new branch), `--help`, `-h`
- **Purpose**: Protect uncommitted work from being discarded

#### 1d. git_commit_block_hook.py

- **Decision**: ask
- **Trigger**: Any `git commit` command
- **Behavior**: Prompts user for approval in the UI with message "Git commit
  requires your approval."
- **Purpose**: Ensure user is aware of and approves commits
- **Note**: Uses `hookSpecificOutput` with `permissionDecision: "ask"` to
  trigger UI prompt

#### 1e. env_file_protection_hook.py

- **Decision**: block
- **Trigger**: Any command that reads, writes, searches, or edits `.env` files
- **Blocked operations**:

  - Reading: cat, less, more, head, tail
  - Editing: nano, vim, vi, emacs, code, subl, atom, gedit
  - Writing: redirects (>, >>), echo, printf, sed -i, tee, cp, mv, touch
  - Searching: grep, rg, ag, ack, find

- **Alternative**: Suggests using `env-safe` command for safe inspection
- **Purpose**: Prevent exposure of secrets and sensitive environment variables

### 2. read_env_protection_hook.py (PreToolUse for Read)

- **Decision**: block
- **Trigger**: Read tool calls targeting `.env` files
- **Blocked patterns**:

  - `.env` - exact match
  - `.env.*` - variants like `.env.local`, `.env.production`, etc.

- **Alternative**: Suggests using `env-safe` command for safe inspection
- **Purpose**: Closes the gap where the Read tool could bypass Bash-level .env
  protections

### 3. file_length_limit_hook.py (PreToolUse for Edit and Write)

- **Decision**: block (with speed bump pattern)
- **Trigger**: Edit or Write operations on source code files that would exceed
  line limit
- **Default limit**: 10000 lines
- **Supported extensions**: .py, .tsx, .ts, .jsx, .js, .rs, .c, .cpp, .go,
  .java, .kt, .swift, .rb, .php, .cs, .scala, .m, .mm, .r, .jl, and more
- **Behavior**: Uses flag file speed bump - blocks first attempt with warning,
  allows on second attempt if user approves
- **Purpose**: Encourage modular, maintainable code

## Summary Table

| Hook | Decision Type | Description |
|------|---------------|-------------|
| bash_hook.py | block/ask/allow | Unified hook combining all bash safety checks |
| rm_block_hook.py | block | Blocks rm, suggests TRASH |
| git_add_block_hook.py | block/ask/allow | Blocks dangerous patterns; asks for modified files; allows new files |
| git_checkout_safety_hook.py | block | Protects uncommitted changes |
| git_commit_block_hook.py | ask | Prompts user for commit approval |
| env_file_protection_hook.py | block | Protects .env files from Bash commands |
| read_env_protection_hook.py | block | Protects .env files from Read tool |
| file_length_limit_hook.py | block (speed bump) | Limits source file size |
