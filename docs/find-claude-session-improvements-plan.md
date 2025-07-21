# find-claude-session.py Implementation Plan

## Overview

This document outlines the step-by-step implementation plan for adding global search
and interactive UI features to the find-claude-session.py tool.

## Prerequisites

### Task 1: Analyze Current Implementation
- [ ] Locate the find-claude-session.py file
- [ ] Understand current search logic and file structure
- [ ] Identify session file locations and formats
- [ ] Determine current command-line argument parsing method
- [ ] Note any existing dependencies

### Task 2: Research Session Storage Structure
- [ ] Identify where Claude stores session files for current project
- [ ] Locate where Claude stores session files globally
- [ ] Understand session file naming conventions
- [ ] Examine session metadata structure

## Implementation Tasks

### Phase 1: Global Search Feature

#### Task 3: Add Command-Line Argument
- [ ] Add `-g/--global` flag to argument parser
- [ ] Update help text to describe the new option
- [ ] Ensure backward compatibility (default to current project)

#### Task 4: Implement Global Search Logic
- [ ] Create function to find all Claude project directories
- [ ] Implement logic to search across all projects when `-g` flag is set
- [ ] Maintain existing local search functionality
- [ ] Handle permissions and access errors gracefully

#### Task 5: Test Global Search
- [ ] Test with `-g` flag across multiple projects
- [ ] Test without flag (ensure local search still works)
- [ ] Test with various search patterns
- [ ] Verify performance with many projects

### Phase 2: Interactive UI

#### Task 6: Choose and Install UI Library
- [ ] Evaluate UI libraries (curses, rich, prompt_toolkit)
- [ ] Add chosen library to requirements/dependencies
- [ ] Create basic UI scaffold

#### Task 7: Implement Session Display
- [ ] Create function to format session information
- [ ] Implement sorting by recency (most recent first)
- [ ] Limit display to top 10 results
- [ ] Add index numbers (1-10) to each entry
- [ ] Display relevant metadata (project, date, summary)

#### Task 8: Implement Arrow Key Navigation
- [ ] Set up keyboard event handling
- [ ] Implement up/down arrow navigation
- [ ] Add visual highlighting for selected item
- [ ] Handle boundary conditions (top/bottom of list)
- [ ] Add proper cursor management

#### Task 9: Implement Numeric Selection
- [ ] Add numeric input handling (1-10)
- [ ] Validate numeric input
- [ ] Provide feedback for invalid input
- [ ] Implement immediate selection on valid number

#### Task 10: Implement Session Resume
- [ ] Extract selected session ID
- [ ] Construct `claude -r <session_id>` command
- [ ] Execute command using subprocess
- [ ] Handle execution errors gracefully
- [ ] Ensure proper terminal state restoration

### Phase 3: Edge Cases and Polish

#### Task 11: Handle Edge Cases
- [ ] No matching sessions found
- [ ] Only one match (auto-select option?)
- [ ] Terminal too small for UI
- [ ] Interrupted selection (Ctrl+C handling)
- [ ] Invalid session files

#### Task 12: Add User Experience Enhancements
- [ ] Add loading indicator during search
- [ ] Implement search result count display
- [ ] Add escape key to cancel
- [ ] Show keyboard shortcuts help
- [ ] Add color coding for better visibility

### Phase 4: Testing and Documentation

#### Task 13: Comprehensive Testing
- [ ] Test all navigation methods
- [ ] Test with various terminal sizes
- [ ] Test with different terminal emulators
- [ ] Test error scenarios
- [ ] Performance test with many sessions

#### Task 14: Update Documentation
- [ ] Update tool's help text
- [ ] Add usage examples
- [ ] Document new features in README
- [ ] Create troubleshooting guide

## Technical Decisions to Make

1. **UI Library Selection**
   - Option A: `curses` - Built-in, lightweight, full control
   - Option B: `rich` - Modern, feature-rich, easier to use
   - Option C: `prompt_toolkit` - Powerful, good for interactive apps

2. **Session Metadata Display**
   - What information to show for each session
   - How to truncate long descriptions
   - Date/time format

3. **Global Search Implementation**
   - How to efficiently find all Claude project directories
   - Caching strategy for better performance
   - Handling of inaccessible directories

## Success Metrics

- [ ] Global search finds sessions from all projects
- [ ] UI displays sessions within 1 second
- [ ] Navigation is responsive and intuitive
- [ ] Session resume works reliably
- [ ] No regression in existing functionality

## Risk Mitigation

- **Risk**: Different Claude versions may store sessions differently
  - **Mitigation**: Build flexible session discovery logic

- **Risk**: UI library compatibility issues
  - **Mitigation**: Test on multiple platforms early

- **Risk**: Performance issues with many projects
  - **Mitigation**: Implement caching or limit search depth