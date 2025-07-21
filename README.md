# claude-code-tools

A collection of command-line tools for working with Claude Code.

## Installation

Install all tools at once using uv:

```bash
# Install from local directory
uv tool install /path/to/claude-code-tools

# Or install from GitHub (once published)
uv tool install git+https://github.com/USERNAME/claude-code-tools
```

## Available Tools

### find-claude-session

Search Claude Code session files by keywords with an interactive UI.

**Features:**
- Search within current project or globally across all Claude projects
- Interactive session selection with rich terminal UI
- Session preview showing first user message
- Automatic directory switching for cross-project sessions
- Progress indicator for global searches

**Usage:**
```bash
# Search in current project
find-claude-session "keyword1,keyword2,keyword3"

# Search across all Claude projects
find-claude-session "keyword1,keyword2,keyword3" --global
find-claude-session "keyword1,keyword2,keyword3" -g
```

Searches through Claude Code session JSONL files and presents an interactive UI showing the top 10 matching sessions. Sessions are sorted by modification time (newest first).

**Interactive UI:**
- Displays session ID, project name, date, and preview
- Select a session by entering its number (1-10)
- Automatically resumes the selected session with `claude -r`
- For cross-project sessions, prompts to change directory first

**Examples:**
```bash
# Find sessions discussing Python errors
find-claude-session "python,error,traceback"

# Find sessions about langroid across all projects
find-claude-session "langroid" -g

# Find sessions about specific topics
find-claude-session "docker,compose,deployment"
```

**Note:** The tool works with or without the `rich` library. If `rich` is installed, you get the interactive UI. Otherwise, it falls back to a simple text-based selection.

**Persistent Directory Changes:**

By default, when resuming a session from a different project, the directory change won't persist after exiting Claude. To make directory changes permanent, use the shell function wrapper:

1. Add this to your shell config (.bashrc/.zshrc):
   ```bash
   fcs() { eval "$(find-claude-session --shell "$@" | sed '/^$/d')"; }
   ```

2. Or source the provided function:
   ```bash
   source /path/to/claude-code-tools/scripts/fcs-function.sh
   ```

3. Then use `fcs` instead of `find-claude-session`:
   ```bash
   fcs "keyword" -g
   ```

This ensures that when you resume a session from a different project, your shell will change to that project's directory and stay there after Claude exits.

### vault

Centralized encrypted backup for .env files using SOPS.

**Usage:**
```bash
# Smart sync (automatic direction)
vault sync

# Encrypt local .env to centralized vault
vault encrypt

# Decrypt to local .env  
vault decrypt

# List all encrypted backups
vault list

# Show detailed status
vault status

# Force specific direction
vault sync --push     # Force encrypt
vault sync --pull     # Force decrypt
```

**Location**: Encrypted backups stored in `~/.dotenvs/`

**Installation Notes:**

For global installation:
```bash
# Standard installation
uv tool install /path/to/claude-code-tools

# For development (editable mode - changes take effect immediately)
uv tool install -e /path/to/claude-code-tools

# To update after making changes (non-editable mode)
uv tool install --force --reinstall /path/to/claude-code-tools
```

For testing without installation:
```bash
# From within the claude-code-tools directory
uvx --from . find-claude-session "keyword"

# From any other directory
uvx --from /path/to/claude-code-tools find-claude-session "keyword"
```

## Adding New Tools

To add a new tool to this collection:

1. Create a new directory for your tool
2. Add your Python module in that directory
3. Update the `[project.scripts]` section in `pyproject.toml`:
   ```toml
   [project.scripts]
   find-claude-session = "find_claude_session.find_claude_session:main"
   your-new-tool = "your_tool_dir.your_module:main"
   ```

## Requirements

- Python 3.11+
- uv (for installation)

## License

MIT