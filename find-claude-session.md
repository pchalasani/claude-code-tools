# find-claude-session Tool Requirements

## Overview

Create a system-wide command-line utility called `find-claude-session` that searches through Claude Code session files to find sessions containing specific keywords.

## Core Requirements

### Functionality
1. **Command Usage**: 
   ```bash
   find-claude-session "keyword1,keyword2,keyword3..."
   ```
   - Accept comma-separated keywords as a single argument
   - Search for sessions that contain ALL specified keywords (AND operation, not OR)
   - Keywords should be case-insensitive

2. **Search Behavior**:
   - Work from any directory on the system
   - Automatically determine the Claude project directory based on current working directory
   - Search through all JSONL files in the appropriate Claude project directory
   - Return only session IDs (not full file paths or content)

3. **Directory Mapping**:
   - Current directory: `/Users/pchalasani/Work/Cool`
   - Maps to Claude directory: `~/.claude/projects/-Users-pchalasani-Work-Cool/`
   - Rule: Replace all `/` with `-` in the path

4. **Output**:
   - List matching session IDs in reverse chronological order (newest first)
   - Use file modification time for sorting
   - Output one session ID per line
   - No extra formatting or information

## Technical Implementation

### Technology Stack
- **Language**: Python (chosen for cross-platform compatibility and simple file I/O)
- **Package Manager**: uv/uvx (modern Python tooling, no need to publish to PyPI)
- **Python Version**: 3.11+

### Project Structure
```
claude-code-tools/           # Repository for multiple Claude-related tools
├── pyproject.toml          # Project configuration at root level
├── README.md               # Documentation for all tools
└── find-claude-session/    # Directory for this specific tool
    └── find_claude_session.py
```

### Key Implementation Details

1. **File Format**: Claude Code stores sessions as JSONL files (JSON Lines format)
   - Each line is a separate JSON object
   - Need to search across all lines in each file

2. **Search Algorithm**:
   - Read JSONL files line by line (memory efficient)
   - Check if ALL keywords exist anywhere in the file
   - Case-insensitive matching
   - Early exit when all keywords found

3. **Installation Method**:
   - Use `uv tool install` for system-wide installation
   - Support `uvx` for running without installation
   - Configure as CLI script in `pyproject.toml`

### Build System Configuration
Use the latest uv build system in `pyproject.toml`:
```toml
[build-system]
requires = ["uv_build>=0.7.19,<0.8.0"]
build-backend = "uv_build"
```

## Error Handling

1. **Missing Claude Directory**: 
   - Check if `~/.claude/projects/PROJECT_PATH/` exists
   - Display helpful error message if not found

2. **No Matches Found**:
   - Display message to stderr
   - Exit with code 0

3. **Invalid Keywords**:
   - Handle empty keyword strings
   - Error if no valid keywords provided

## Future Extensibility

The tool should be part of a larger `claude-code-tools` repository that can contain multiple Claude Code-related utilities. The structure should support:
- Adding new tools as separate directories
- Shared utilities if needed
- Single installation command for all tools

## Example Usage Scenarios

```bash
# Find sessions about a specific error
cd ~/projects/my-app
find-claude-session "TypeError,undefined,function"

# Find sessions about a feature implementation
find-claude-session "authentication,JWT,middleware"

# Find sessions with specific library discussions
find-claude-session "langroid,MCP,sampling"
```

## Success Criteria

1. Works from any directory without configuration
2. Fast search through potentially large JSONL files
3. Accurate keyword matching (ALL keywords must be present)
4. Clean, simple output suitable for piping to other commands
5. Easy installation with modern Python tooling (uv)
6. No dependencies on PyPI publication