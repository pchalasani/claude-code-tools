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

Search Claude Code session files by keywords.

**Usage:**
```bash
find-claude-session "keyword1,keyword2,keyword3"
```

Searches through Claude Code session JSONL files in the project directory corresponding to your current working directory. Returns session IDs that contain ALL specified keywords, sorted by modification time (newest first).

**Output Format:**
```
session-id | YYYY-MM-DD HH:MM:SS | N lines
```

**Examples:**
```bash
# Find sessions discussing Python errors
find-claude-session "python,error,traceback"

# Find sessions about specific topics
find-claude-session "docker,compose,deployment"
```

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