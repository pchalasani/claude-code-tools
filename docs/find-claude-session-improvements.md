# find_claude_session.py Tool Improvements - Requirements Document

## Overview

This document outlines the requirements for enhancing the `find_claude_session.py` tool
with two major improvements: global search capability and an interactive UI for session
selection.

## Current State

The `find_claude_session.py` tool currently searches for Claude sessions within the
current project directory.

## Requested Improvements

### 1. Global Search Option (-g/--global)

**Requirement**: Add a command-line option that enables searching across all project
session files, not limited to the current project.

**Details**:
- Add `-g` or `--global` flag to the command-line interface
- When this flag is present, the tool should search all Claude session files across
  all projects
- When absent, maintain the current behavior of searching only within the current
  project
- The global search should locate session files from all project directories that
  contain Claude sessions

### 2. Interactive UI for Session Selection

**Requirement**: Implement an interactive user interface that displays matching sessions
and allows for easy selection.

**Details**:

#### Display Requirements:
- Show the top 10 matching sessions
- Order sessions by recency (most recent first)
- Display relevant session information (e.g., session ID, project name, date/time,
  summary or first message)

#### Interaction Methods:
1. **Keyboard Navigation**:
   - Use arrow keys (up/down) to navigate through the session list
   - Highlight the currently selected session
   - Press Enter to select the highlighted session

2. **Numeric Selection**:
   - Display an index number (1-10) next to each session
   - Allow direct selection by typing the index number

#### Selection Action:
- When a session is selected (either by Enter key or numeric input), automatically
  execute: `claude -r <selected_session_id>`
- This resumes the selected Claude session

## User Experience Flow

1. User runs `find_claude_session.py` with optional search terms and/or `-g` flag
2. Tool searches for matching sessions (current project or globally)
3. Interactive UI displays up to 10 most recent matching sessions
4. User navigates using arrow keys or types a number (1-10)
5. Upon selection, the tool executes `claude -r <session_id>` to resume the session

## Technical Considerations

- The interactive UI should handle terminal resizing gracefully
- The UI should be responsive and provide visual feedback for the selected item
- Consider using a library like `curses` or `rich` for the interactive interface
- Ensure compatibility with common terminal emulators
- Handle edge cases: no matching sessions, single match, terminal too small

## Success Criteria

- The `-g/--global` flag successfully searches all project directories
- The interactive UI displays sessions in order of recency
- Both navigation methods (arrow keys and numeric) work correctly
- Selected session resumes properly with `claude -r <session_id>`
- The tool maintains backwards compatibility when used without the new features