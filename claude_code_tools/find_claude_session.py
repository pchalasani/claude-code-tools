#!/usr/bin/env python3
"""
find-claude-session: Search Claude Code session files by keywords

Usage:
    find-claude-session "keyword1,keyword2,keyword3..." [-g/--global]
    
This tool searches for Claude Code session JSONL files that contain ALL specified keywords,
and returns matching session IDs in reverse chronological order.

With -g/--global flag, searches across all Claude projects, not just the current one.

For the directory change to persist, use the shell function:
    fcs() { eval $(find-claude-session --shell "$@"); }
"""

import argparse
import json
import os
import re
import shlex
import sys
import time
import termios
import tty
from datetime import datetime
from pathlib import Path
from typing import List, Set, Tuple, Optional

from claude_code_tools.session_menu import (
    show_action_menu as menu_show_action_menu,
    show_resume_submenu as menu_show_resume_submenu,
    prompt_suppress_options as menu_prompt_suppress_options,
)
from claude_code_tools.node_menu_ui import run_node_menu_ui, run_find_options_ui
from claude_code_tools.trim_session import (
    trim_and_create_session,
    is_trimmed_session,
    get_session_derivation_type,
)


def get_session_start_timestamp(jsonl_file: Path) -> Optional[float]:
    """Extract session start timestamp from first entry with timestamp field.

    Args:
        jsonl_file: Path to Claude session JSONL file

    Returns:
        Unix timestamp as float, or None if not found
    """
    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 10:  # Only check first 10 lines
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("timestamp"):
                        ts = data["timestamp"]
                        # Parse ISO format (e.g., "2025-10-22T16:05:28.707Z")
                        if ts.endswith("Z"):
                            ts = ts[:-1] + "+00:00"
                        return datetime.fromisoformat(ts).timestamp()
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except (OSError, IOError):
        pass
    return None


def _read_key() -> str:
    """Read a single keypress (Enter/Esc)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def prompt_post_action() -> str:
    """After non-launch actions: Enter exits, Esc returns to menu."""
    print("\n[Action complete] Press Enter to exit, or Esc to return to menu", file=sys.stderr)
    ch = _read_key()
    if ch == "\x1b":
        return "back"
    return "exit"

# Try to import TUI - it's optional
try:
    from claude_code_tools.session_tui import run_session_tui
    TUI_AVAILABLE = True
except ImportError:
    TUI_AVAILABLE = False
from claude_code_tools.smart_trim_core import identify_trimmable_lines_cli
from claude_code_tools.smart_trim import trim_lines
from claude_code_tools.session_utils import (
    get_claude_home,
    encode_claude_project_path,
    is_valid_session,
    is_malformed_session,
    extract_cwd_from_session,
    format_session_id_display,
    filter_sessions_by_time,
)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None


def get_claude_project_dir(claude_home: Optional[str] = None) -> Path:
    """Convert current working directory to Claude project directory path."""
    cwd = os.getcwd()

    # Use provided claude_home, CLAUDE_CONFIG_DIR env var, or default to ~/.claude
    base_dir = get_claude_home(claude_home)

    # Encode path to match Claude's directory naming convention
    project_path = encode_claude_project_path(cwd)
    claude_dir = base_dir / "projects" / project_path
    return claude_dir


def get_all_claude_projects(claude_home: Optional[str] = None) -> List[Tuple[Path, str]]:
    """Get all Claude project directories with their original paths."""
    # Use provided claude_home, CLAUDE_CONFIG_DIR env var, or default to ~/.claude
    base_dir = get_claude_home(claude_home)

    projects_dir = base_dir / "projects"
    
    if not projects_dir.exists():
        return []
    
    projects = []
    for project_dir in projects_dir.iterdir():
        if project_dir.is_dir():
            # Convert back from Claude's naming to original path
            # Claude's pattern: -Users-username-path-to-project
            # where only path separators (/) are replaced with -
            dir_name = project_dir.name
            
            # Split by - but need to be smart about it
            # Pattern is like: -Users-pchalasani-Git-project-name
            # We need to identify which hyphens are path separators vs part of names
            
            # Most reliable approach: use known path patterns
            if dir_name.startswith("-Users-"):
                # macOS path
                parts = dir_name[1:].split("-")
                # Reconstruct, assuming first few parts are the path
                # Pattern: Users/username/...
                if len(parts) >= 2:
                    # Try to reconstruct the path
                    # We know it starts with /Users/username
                    original_path = "/" + parts[0] + "/" + parts[1]
                    
                    # For the rest, we need to be careful
                    # Common patterns: /Users/username/Git/project-name
                    remaining = "-".join(parts[2:])
                    
                    # Check for common directories
                    if remaining.startswith("Git-"):
                        original_path += "/Git/" + remaining[4:]
                    elif remaining:
                        # Just append the rest as is
                        original_path += "/" + remaining
                else:
                    original_path = "/" + dir_name[1:].replace("-", "/")
            elif dir_name.startswith("-home-"):
                # Linux path
                original_path = "/" + dir_name[1:].replace("-", "/")
            else:
                # Unknown pattern, best guess
                original_path = "/" + dir_name.replace("-", "/")
            
            projects.append((project_dir, original_path))
    
    return projects


def extract_project_name(original_path: str) -> str:
    """Extract a readable project name from the original path."""
    # Get the last component of the path as the project name
    parts = original_path.rstrip("/").split("/")
    return parts[-1] if parts else "unknown"


    """
    Check if all keywords are present in the JSONL file, count lines, and extract git branch.

    Args:
        filepath: Path to the JSONL file
        keywords: List of keywords to search for (case-insensitive). Empty list matches all files.

    Returns:
        Tuple of (matches: bool, line_count: int, git_branch: Optional[str])
        - matches: True if ALL keywords are found in the file (or True if no keywords)
        - line_count: Total number of lines in the file
        - git_branch: Git branch name from the first message that has it, or None
    """
    # If no keywords, match all files
    if not keywords:
        msg_count = 0
        git_branch = None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        # Only count user/assistant messages
                        if data.get('type') in ('user', 'assistant'):
                            msg_count += 1
                        # Extract git branch from JSON if not already found
                        if git_branch is None:
                            if 'gitBranch' in data and data['gitBranch']:
                                git_branch = data['gitBranch']
                    except (json.JSONDecodeError, KeyError):
                        pass
        except Exception:
            return False, 0, None
        return True, msg_count, git_branch

    # Convert keywords to lowercase for case-insensitive search
    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    msg_count = 0
    git_branch = None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line_lower = line.lower()

                try:
                    data = json.loads(line.strip())
                    # Only count user/assistant messages
                    if data.get('type') in ('user', 'assistant'):
                        msg_count += 1
                    # Extract git branch from JSON if not already found
                    if git_branch is None:
                        if 'gitBranch' in data and data['gitBranch']:
                            git_branch = data['gitBranch']
                except (json.JSONDecodeError, KeyError):
                    pass

                # Check which keywords are in this line (search all lines)
                for keyword in keywords_lower:
                    if keyword in line_lower:
                        found_keywords.add(keyword)
    except Exception:
        # Skip files that can't be read
        return False, 0, None

    matches = len(found_keywords) == len(keywords_lower)
    return matches, msg_count, git_branch


def is_system_message(text: str) -> bool:
    """Check if text is system-generated (XML tags, env context, etc)"""
    if not text or len(text.strip()) < 5:
        return True
    text = text.strip()
    # Check for XML-like tags (user_instructions, environment_context, etc)
    if text.startswith("<") and ">" in text[:100]:
        return True
    return False


def is_sidechain_session(filepath: Path) -> bool:
    """
    Check if a session file is a sidechain (sub-agent) session.

    Sidechain sessions are created when launching sub-agents via the Task tool
    and cannot be resumed directly.

    Args:
        filepath: Path to session JSONL file.

    Returns:
        True if session is a sidechain, False otherwise.
    """
    if not filepath.exists():
        return False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            # Check first few lines for isSidechain field
            for i, line in enumerate(f):
                if i >= 10:  # Only check first 10 lines
                    break
                try:
                    data = json.loads(line.strip())
                    if "isSidechain" in data:
                        return data["isSidechain"] is True
                except (json.JSONDecodeError, KeyError):
                    continue
    except (OSError, IOError):
        pass

    return False


def search_keywords_in_file(filepath: Path, keywords: List[str]) -> tuple[bool, int, Optional[str]]:
    """
    Check if all keywords are present in the JSONL file, count lines, and extract git branch.

    Args:
        filepath: Path to the JSONL file
        keywords: List of keywords to search for (case-insensitive). Empty list matches all files.

    Returns:
        Tuple of (matches: bool, line_count: int, git_branch: Optional[str])
        - matches: True if ALL keywords are found in the file (or True if no keywords)
        - line_count: Total number of lines in the file
        - git_branch: Git branch name from the first message that has it, or None
    """
    # If no keywords, match all files
    if not keywords:
        msg_count = 0
        git_branch = None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        # Only count user/assistant messages
                        if data.get('type') in ('user', 'assistant'):
                            msg_count += 1
                        # Extract git branch from JSON if not already found
                        if git_branch is None:
                            if 'gitBranch' in data and data['gitBranch']:
                                git_branch = data['gitBranch']
                    except (json.JSONDecodeError, KeyError):
                        pass
        except Exception:
            return False, 0, None
        return True, msg_count, git_branch

    # Convert keywords to lowercase for case-insensitive search
    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    msg_count = 0
    git_branch = None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line_lower = line.lower()

                try:
                    data = json.loads(line.strip())
                    # Only count user/assistant messages
                    if data.get('type') in ('user', 'assistant'):
                        msg_count += 1
                    # Extract git branch from JSON if not already found
                    if git_branch is None:
                        if 'gitBranch' in data and data['gitBranch']:
                            git_branch = data['gitBranch']
                except (json.JSONDecodeError, KeyError):
                    pass

                # Check which keywords are in this line (search all lines)
                for keyword in keywords_lower:
                    if keyword in line_lower:
                        found_keywords.add(keyword)
    except Exception:
        # Skip files that can't be read
        return False, 0, None

    matches = len(found_keywords) == len(keywords_lower)
    return matches, msg_count, git_branch


def get_session_preview(filepath: Path) -> str:
    """Get a preview of the session from the LAST message of any type."""
    last_message = None  # Tuple of (type_prefix, content)

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    msg_type = data.get('type')
                    # Accept user or assistant messages
                    if msg_type in ('user', 'assistant'):
                        message = data.get('message', {})
                        content = message.get('content', '')
                        text = None

                        if isinstance(content, str):
                            text = content.strip()
                        elif isinstance(content, list):
                            # Handle structured content
                            for item in content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    text = item.get('text', '').strip()
                                    break

                        # Filter out system messages and keep updating to get LAST
                        if text and not is_system_message(text):
                            cleaned = text.replace('\n', ' ')[:400]
                            type_prefix = f"[{msg_type}]"
                            # Prefer substantial messages (>20 chars)
                            if len(cleaned) > 20:
                                last_message = (type_prefix, cleaned)
                            elif last_message is None:
                                last_message = (type_prefix, cleaned)

                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass

    if last_message:
        return f"{last_message[0]} {last_message[1]}"
    return "No preview available"


def find_sessions(
    keywords: List[str],
    global_search: bool = False,
    claude_home: Optional[str] = None,
    original_only: bool = False,
    no_sub: bool = False,
    no_trim: bool = False,
    no_cont: bool = False,
) -> List[Tuple[str, float, float, int, str, str, str, Optional[str], bool]]:
    """
    Find all Claude Code sessions containing the specified keywords.

    Args:
        keywords: List of keywords to search for
        global_search: If True, search all projects; if False, search current project only
        claude_home: Optional custom Claude home directory (defaults to ~/.claude)
        original_only: If True, show only original sessions (excludes trimmed, rollover, and sub-agent sessions)
        no_sub: If True, exclude sub-agent sessions
        no_trim: If True, exclude trimmed sessions
        no_cont: If True, exclude rollover sessions (internally "continued")

    Returns:
        List of tuples (session_id, modification_time, creation_time, line_count, project_name, preview, project_path, git_branch, is_trimmed) sorted by modification time
    """
    matching_sessions = []
    
    if global_search:
        # Search all projects
        projects = get_all_claude_projects(claude_home)
        
        if RICH_AVAILABLE and console:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task(f"Searching {len(projects)} projects...", total=len(projects))
                
                for project_dir, original_path in projects:
                    project_name = extract_project_name(original_path)
                    progress.update(task, description=f"Searching {project_name}...")
                    
                    # Search all JSONL files in this project directory
                    for jsonl_file in project_dir.glob("*.jsonl"):
                        matches, line_count, git_branch = search_keywords_in_file(jsonl_file, keywords)
                        if matches:
                            # Skip malformed sessions (missing metadata, cannot resume)
                            if is_malformed_session(jsonl_file):
                                continue

                            # Check if session is trimmed/continued
                            is_trimmed = is_trimmed_session(jsonl_file)
                            derivation_type = get_session_derivation_type(jsonl_file) if is_trimmed else None

                            # Check if session is sidechain (sub-agent)
                            is_sidechain = is_sidechain_session(jsonl_file)

                            # Apply filters (original_only overrides individual filters)
                            if original_only:
                                # Original only: exclude trimmed, continued, and sub-agents
                                if is_trimmed or is_sidechain:
                                    continue
                            else:
                                # Individual filters
                                if no_sub and is_sidechain:
                                    continue
                                if no_trim and derivation_type == "trimmed":
                                    continue
                                if no_cont and derivation_type == "continued":
                                    continue

                            session_id = jsonl_file.stem
                            stat = jsonl_file.stat()
                            mod_time = stat.st_mtime
                            # Get session start timestamp from JSON metadata, fall back to file stats
                            create_time = get_session_start_timestamp(jsonl_file)
                            if create_time is None:
                                create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                            preview = get_session_preview(jsonl_file)
                            # Extract actual cwd from session file - MUST NOT use reconstructed path as fallback
                            actual_cwd = extract_cwd_from_session(jsonl_file)
                            if not actual_cwd:
                                # Skip sessions without cwd metadata (shouldn't happen for valid Claude sessions)
                                continue
                            matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, actual_cwd, git_branch, derivation_type, is_sidechain))

                    progress.advance(task)
        else:
            # Fallback without rich
            for project_dir, original_path in projects:
                project_name = extract_project_name(original_path)

                for jsonl_file in project_dir.glob("*.jsonl"):
                    matches, line_count, git_branch = search_keywords_in_file(jsonl_file, keywords)
                    if matches:
                        # Skip malformed sessions (missing metadata, cannot resume)
                        if is_malformed_session(jsonl_file):
                            continue

                        # Check if session is trimmed/continued
                        is_trimmed = is_trimmed_session(jsonl_file)
                        derivation_type = get_session_derivation_type(jsonl_file) if is_trimmed else None

                        # Check if session is sidechain (sub-agent)
                        is_sidechain = is_sidechain_session(jsonl_file)

                        # Apply filters (original_only overrides individual filters)
                        if original_only:
                            # Original only: exclude trimmed, continued, and sub-agents
                            if is_trimmed or is_sidechain:
                                continue
                        else:
                            # Individual filters
                            if no_sub and is_sidechain:
                                continue
                            if no_trim and derivation_type == "trimmed":
                                continue
                            if no_cont and derivation_type == "continued":
                                continue

                        session_id = jsonl_file.stem
                        stat = jsonl_file.stat()
                        mod_time = stat.st_mtime
                        # Get session start timestamp from JSON metadata, fall back to file stats
                        create_time = get_session_start_timestamp(jsonl_file)
                        if create_time is None:
                            create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                        preview = get_session_preview(jsonl_file)
                        # Extract actual cwd from session file - MUST NOT use reconstructed path as fallback
                        actual_cwd = extract_cwd_from_session(jsonl_file)
                        if not actual_cwd:
                            # Skip sessions without cwd metadata (shouldn't happen for valid Claude sessions)
                            continue
                        matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, actual_cwd, git_branch, derivation_type, is_sidechain))
    else:
        # Search current project only
        claude_dir = get_claude_project_dir(claude_home)
        
        if not claude_dir.exists():
            return []
        
        project_name = extract_project_name(os.getcwd())
        
        # Search all JSONL files in the directory
        for jsonl_file in claude_dir.glob("*.jsonl"):
            matches, line_count, git_branch = search_keywords_in_file(jsonl_file, keywords)
            if matches:
                # Skip malformed sessions (missing metadata, cannot resume)
                if is_malformed_session(jsonl_file):
                    continue

                # Check if session is trimmed/continued
                is_trimmed = is_trimmed_session(jsonl_file)
                derivation_type = get_session_derivation_type(jsonl_file) if is_trimmed else None

                # Check if session is sidechain (sub-agent)
                is_sidechain = is_sidechain_session(jsonl_file)

                # Apply filters (original_only overrides individual filters)
                if original_only:
                    # Original only: exclude trimmed, continued, and sub-agents
                    if is_trimmed or is_sidechain:
                        continue
                else:
                    # Individual filters
                    if no_sub and is_sidechain:
                        continue
                    if no_trim and derivation_type == "trimmed":
                        continue
                    if no_cont and derivation_type == "continued":
                        continue

                session_id = jsonl_file.stem
                stat = jsonl_file.stat()
                mod_time = stat.st_mtime
                # Get session start timestamp from JSON metadata, fall back to file stats
                create_time = get_session_start_timestamp(jsonl_file)
                if create_time is None:
                    create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                preview = get_session_preview(jsonl_file)
                # Extract actual cwd from session file for consistency
                actual_cwd = extract_cwd_from_session(jsonl_file) or os.getcwd()
                matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, actual_cwd, git_branch, derivation_type, is_sidechain))
    
    # Sort by modification time (newest first)
    matching_sessions.sort(key=lambda x: x[1], reverse=True)
    
    return matching_sessions


def display_interactive_ui(sessions: List[Tuple[str, float, float, int, str, str, str, Optional[str], bool]], keywords: List[str], stderr_mode: bool = False, num_matches: int = 10) -> Optional[Tuple[str, str]]:
    """Display interactive UI for session selection."""
    if not RICH_AVAILABLE:
        return None

    # Use stderr console if in stderr mode
    ui_console = Console(file=sys.stderr) if stderr_mode else console
    if not ui_console:
        return None

    # Limit to specified number of sessions
    display_sessions = sessions[:num_matches]

    if not display_sessions:
        ui_console.print("[red]No sessions found[/red]")
        return None

    # Create table
    title = f"Sessions matching: {', '.join(keywords)}" if keywords else "All sessions"
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan"
    )
    
    table.add_column("#", style="bold yellow", width=3)
    table.add_column("Session ID", style="dim")
    table.add_column("Project", style="green")
    table.add_column("Branch", style="magenta")
    table.add_column("Date-Range", style="blue")
    table.add_column("Lines", style="cyan", justify="right")
    table.add_column("Last User Message", style="white", max_width=60, overflow="fold")
    
    for idx, (session_id, mod_time, create_time, line_count, project_name, preview, _, git_branch, derivation_type, is_sidechain) in enumerate(display_sessions, 1):
        # Format: "10/04 - 10/09 13:45"
        create_date = datetime.fromtimestamp(create_time).strftime('%m/%d')
        mod_date = datetime.fromtimestamp(mod_time).strftime('%m/%d %H:%M')
        date_display = f"{create_date} - {mod_date}"
        branch_display = git_branch if git_branch else "N/A"

        # Format session ID with annotations using centralized helper
        session_id_display = format_session_id_display(
            session_id,
            is_trimmed=(derivation_type == "trimmed"),
            is_continued=(derivation_type == "continued"),
            is_sidechain=is_sidechain,
            truncate_length=8,
        )

        table.add_row(
            str(idx),
            session_id_display,
            project_name,
            branch_display,
            date_display,
            str(line_count),
            preview
        )
    
    ui_console.print(table)

    # Show footnotes if any sessions are trimmed, continued, or sidechain
    has_trimmed = any(s[8] == "trimmed" for s in display_sessions)  # derivation_type is index 8
    has_continued = any(s[8] == "continued" for s in display_sessions)
    has_sidechain = any(s[9] for s in display_sessions)  # is_sidechain is index 9
    if has_trimmed or has_continued or has_sidechain:
        footnotes = []
        if has_trimmed:
            footnotes.append("(t) = Trimmed session")
        if has_continued:
            footnotes.append("(c) = Continued session")
        if has_sidechain:
            footnotes.append("(sub) = Sub-agent session (not directly resumable)")
        ui_console.print("[dim]" + " | ".join(footnotes) + "[/dim]")

    # Auto-select if only one result
    if len(display_sessions) == 1:
        ui_console.print(f"\n[yellow]Auto-selecting only match: {display_sessions[0][0][:16]}...[/yellow]")
        return display_sessions[0]

    ui_console.print("\n[bold]Select a session:[/bold]")
    ui_console.print(f"  • Enter number (1-{len(display_sessions)}) to select")
    ui_console.print("  • Press Enter to cancel\n")

    while True:
        try:
            # In stderr mode, we need to ensure nothing goes to stdout
            if stderr_mode:
                # Temporarily redirect stdout to devnull
                old_stdout = sys.stdout
                sys.stdout = open(os.devnull, 'w')

            choice = Prompt.ask(
                "Your choice",
                default="",
                show_default=False,
                console=ui_console
            )

            # Handle empty input - cancel
            if not choice or not choice.strip():
                # Restore stdout first
                if stderr_mode:
                    sys.stdout.close()
                    sys.stdout = old_stdout
                ui_console.print("[yellow]Cancelled[/yellow]")
                return None

            # Restore stdout
            if stderr_mode:
                sys.stdout.close()
                sys.stdout = old_stdout

            idx = int(choice) - 1
            if 0 <= idx < len(display_sessions):
                session_info = display_sessions[idx]
                return session_info  # Return full session tuple
            else:
                ui_console.print("[red]Invalid choice. Please try again.[/red]")

        except KeyboardInterrupt:
            # Restore stdout if needed
            if stderr_mode and sys.stdout != old_stdout:
                sys.stdout.close()
                sys.stdout = old_stdout
            ui_console.print("\n[yellow]Cancelled[/yellow]")
            return None
        except EOFError:
            # Restore stdout if needed
            if stderr_mode and sys.stdout != old_stdout:
                sys.stdout.close()
                sys.stdout = old_stdout
            ui_console.print("\n[yellow]Cancelled (EOF)[/yellow]")
            return None
        except ValueError:
            ui_console.print("[red]Invalid choice. Please try again.[/red]")


def show_resume_submenu() -> Optional[str]:
    """Show resume options submenu."""
    return menu_show_resume_submenu(stderr_mode=False)


def prompt_suppress_options() -> Optional[Tuple[Optional[str], int, Optional[int]]]:
    """
    Prompt user for suppress-tool-results options.

    Returns:
        Tuple of (tools, threshold, trim_assistant_messages) or None if cancelled
    """
    return menu_prompt_suppress_options(stderr_mode=False)


def extract_first_user_message_claude(session_file: Path) -> str:
    """Extract first user message from Claude session file."""
    with open(session_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "user":
                content = data.get("message", {}).get("content")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            return item.get("text", "")

    return "Suppressed session"


def handle_suppress_resume_claude(
    session_id: str,
    project_path: str,
    tools: Optional[str],
    threshold: int,
    trim_assistant_messages: Optional[int] = None,
    claude_home: Optional[str] = None,
) -> str | None:
    """
    Suppress tool results and resume Claude Code session.

    Returns:
        'back' if user wants to go back to menu, None otherwise.
    """
    session_file = Path(get_session_file_path(session_id, project_path, claude_home))

    print(f"\n🔧 Trimming session...")

    # Parse tools into set if provided
    target_tools = None
    if tools:
        target_tools = {tool.strip().lower() for tool in tools.split(",")}

    try:
        # Use helper function to trim and create new session
        result = trim_and_create_session(
            "claude",
            session_file,
            target_tools,
            threshold,
            trim_assistant_messages=trim_assistant_messages
        )
    except Exception as e:
        print(f"❌ Error trimming session: {e}")
        return None

    # Check if nothing to trim (savings below threshold)
    from claude_code_tools.node_menu_ui import run_trim_confirm_ui
    if result.get("nothing_to_trim"):
        print(f"   Savings too small ({result['tokens_saved']} tokens)")
        action = run_trim_confirm_ui(
            nothing_to_trim=True,
            original_session_id=session_id,
        )
        if action == 'resume':
            resume_session(session_id, project_path, claude_home=claude_home)
            return None
        return 'back'

    new_session_id = result["session_id"]
    new_session_file = Path(result["output_file"])

    # Calculate total lines trimmed for confirmation UI
    total_trimmed = result['num_tools_trimmed'] + result['num_assistant_trimmed']

    # Show confirmation UI
    action = run_trim_confirm_ui(
        new_session_id=new_session_id,
        lines_trimmed=total_trimmed,
        tokens_saved=result['tokens_saved'],
        output_file=str(new_session_file),
    )

    if action == 'resume':
        # Resume the new session
        resume_session(new_session_id, project_path, claude_home=claude_home)
        return None
    elif action == 'delete':
        # Delete the new session file and go back to menu
        new_session_file.unlink(missing_ok=True)
        print(f"\n🗑️  Deleted session file: {new_session_file.name}")
        return 'back'
    else:
        # Cancel (escape) - keep file, go back to menu
        print(f"\n📁 Session file kept: {new_session_file}")
        print(f"   Session ID: {new_session_id}")
        return 'back'


def handle_smart_trim_resume_claude(
    session_id: str,
    project_path: str,
    claude_home: Optional[str] = None,
    custom_instructions: Optional[str] = None,
) -> str | None:
    """
    Smart trim session using parallel agents and resume Claude Code session.

    Args:
        session_id: Session ID to trim
        project_path: Project path
        claude_home: Optional custom Claude home directory
        custom_instructions: Optional custom instructions for the trim agents

    Returns:
        'back' if user wants to go back to menu, None otherwise.
    """
    import uuid

    session_file = Path(get_session_file_path(session_id, project_path, claude_home))

    print(f"\n🤖 Smart trimming session using Claude CLI...")
    if custom_instructions:
        print(f"   Instructions: {custom_instructions[:60]}...")
    print(f"   This may take a minute as Claude analyzes the session...")

    try:
        # Identify trimmable lines using CLI with parallel sub-agents
        trimmable = identify_trimmable_lines_cli(
            session_file,
            exclude_types=[],
            preserve_recent=10,
            custom_instructions=custom_instructions,
        )

        if not trimmable:
            # Show confirmation for "nothing to trim" case
            from claude_code_tools.node_menu_ui import run_trim_confirm_ui
            action = run_trim_confirm_ui(
                nothing_to_trim=True,
                original_session_id=session_id,
            )

            if action == 'resume':
                resume_session(session_id, project_path, claude_home=claude_home)
                return None
            # 'back' or 'cancel' - return signal to go back to menu
            return 'back'

        print(f"   Found {len(trimmable)} lines to trim")

        # Extract line indices and descriptions from verbose tuples
        # trimmable is list of (line_idx, rationale, description) tuples
        line_indices = [item[0] for item in trimmable]
        descriptions = {
            item[0]: item[2] for item in trimmable if len(item) > 2 and item[2]
        }

        # Generate new session ID
        new_session_id = str(uuid.uuid4())

        # Create output path
        output_file = session_file.parent / f"{new_session_id}.jsonl"

        # Perform trimming
        stats = trim_lines(
            session_file, line_indices, output_file, descriptions=descriptions
        )

        # Check if savings are worth it (minimum 300 tokens)
        MIN_TOKEN_SAVINGS = 300
        if stats['tokens_saved'] < MIN_TOKEN_SAVINGS:
            # Not worth trimming - delete output and show nothing to trim
            output_file.unlink(missing_ok=True)
            print(f"   Savings too small ({stats['tokens_saved']} tokens < {MIN_TOKEN_SAVINGS})")
            print(f"   (Diagnostic file: .claude/trim-result-{session_id}.jsonl)")
            from claude_code_tools.node_menu_ui import run_trim_confirm_ui
            action = run_trim_confirm_ui(
                nothing_to_trim=True,
                original_session_id=session_id,
            )
            if action == 'resume':
                resume_session(session_id, project_path, claude_home=claude_home)
                return None
            return 'back'

        # Show confirmation UI
        from claude_code_tools.node_menu_ui import run_trim_confirm_ui
        action = run_trim_confirm_ui(
            new_session_id=new_session_id,
            lines_trimmed=stats['num_lines_trimmed'],
            tokens_saved=stats['tokens_saved'],
            output_file=str(output_file),
        )

        if action == 'resume':
            # Resume the new session
            resume_session(new_session_id, project_path, claude_home=claude_home)
            return None
        elif action == 'delete':
            # Delete the new session file and go back to menu
            output_file.unlink(missing_ok=True)
            print(f"\n🗑️  Deleted session file: {output_file.name}")
            return 'back'
        else:
            # Cancel (escape) - keep file, go back to menu
            print(f"\n📁 Session file kept: {output_file}")
            print(f"   Session ID: {new_session_id}")
            return 'back'

    except Exception as e:
        print(f"❌ Error during smart trim: {e}")
        import traceback
        traceback.print_exc()
        return None


def show_action_menu(session_info: Tuple[str, float, float, int, str, str, str, Optional[str]]) -> Optional[str]:
    """
    Show action menu for selected session.

    Returns: action choice ('resume', 'path', 'copy') or None if cancelled
    """
    session_id, _, _, _, project_name, _, project_path, git_branch, _, is_sidechain = session_info

    return menu_show_action_menu(
        session_id=session_id,
        agent="claude",
        project_name=project_name,
        git_branch=git_branch,
        is_sidechain=is_sidechain,
        stderr_mode=False,
    )


def get_session_file_path(
    session_id: str, project_path: str, claude_home: Optional[str] = None
) -> str:
    """Get the full file path for a session.

    Supports both full and partial session IDs. If an exact match isn't found,
    searches for sessions containing the given ID string.

    Args:
        session_id: Full or partial session UUID
        project_path: Project working directory path
        claude_home: Optional custom Claude home directory

    Returns:
        Full path to the session file

    Raises:
        FileNotFoundError: If session cannot be found
        ValueError: If partial ID matches multiple sessions
    """
    base_dir = get_claude_home(claude_home)
    encoded_path = encode_claude_project_path(project_path)
    claude_project_dir = base_dir / "projects" / encoded_path

    # Try exact match first (fast path)
    exact_path = claude_project_dir / f"{session_id}.jsonl"
    if exact_path.exists():
        return str(exact_path)

    # If project directory doesn't exist, return constructed path
    # (maintains backward compatibility - caller can handle non-existent path)
    if not claude_project_dir.exists():
        return str(exact_path)

    # Search for partial matches in the project directory
    matches = []
    for jsonl_file in claude_project_dir.glob("*.jsonl"):
        if session_id in jsonl_file.stem:
            matches.append(jsonl_file)

    if len(matches) == 0:
        # No partial matches found, return constructed path
        return str(exact_path)
    elif len(matches) == 1:
        return str(matches[0])
    else:
        match_list = "\n  ".join(str(m.name) for m in matches)
        raise ValueError(
            f"Multiple sessions match '{session_id}':\n  {match_list}\n"
            f"Please use a more specific session ID."
        )


def handle_export_session(session_file_path: str, dest_override: str | None = None, silent: bool = False) -> None:
    """Export session to text file."""
    from claude_code_tools.export_claude_session import export_session_to_markdown as do_export
    from datetime import datetime

    try:
        # Generate default export path using session's project directory
        session_file = Path(session_file_path)
        session_id = session_file.stem
        today = datetime.now().strftime("%Y%m%d")

        # Infer project directory from session metadata
        project_dir = extract_cwd_from_session(session_file)
        if project_dir:
            output_dir = Path(project_dir) / "exported-sessions"
        else:
            output_dir = Path.cwd() / "exported-sessions"

        default_path = output_dir / f"{today}-claude-session-{session_id}.txt"

        if dest_override is None:
            print(f"\nDefault export path: {default_path}")
            dest = input("Path (or Enter for default): ").strip()
            if not dest:
                dest_path = default_path
            else:
                dest_path = Path(dest).expanduser()

                # Force .txt extension
                if dest_path.suffix != ".txt":
                    dest_path = dest_path.with_suffix(".txt")
        else:
            dest_path = Path(dest_override).expanduser()
            if dest_path.suffix != ".txt":
                dest_path = dest_path.with_suffix(".txt")

        # Create parent directory if needed
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Export to text file
        print(f"\n📄 Exporting session...")
        with open(dest_path, 'w') as f:
            stats = do_export(Path(session_file_path), f, verbose=False)

        if not silent:
            print(f"✅ Export complete!")
            print(f"   User messages: {stats['user_messages']}")
            print(f"   Assistant messages: {stats['assistant_messages']}")
            print(f"   Tool calls: {stats['tool_calls']}")
            print(f"   Tool results: {stats['tool_results']}")
            print(f"   Skipped items: {stats['skipped']}")
            print(f"\n📄 Exported to: {dest_path}")

    except Exception as e:
        print(f"\nError exporting session: {e}")


def copy_session_file(session_file_path: str, dest_override: str | None = None, silent: bool = False) -> None:
    """Copy session file to user-specified file or directory."""
    try:
        if dest_override is None:
            dest = input("\nEnter destination file or directory path: ").strip()
            if not dest:
                print("Cancelled.")
                return
        else:
            dest = dest_override

        dest_path = Path(dest).expanduser()
        source = Path(session_file_path)

        # Determine if destination is a directory or file
        if dest_path.exists():
            if dest_path.is_dir():
                # Copy into directory with original filename
                dest_file = dest_path / source.name
            else:
                # Copy to specified file
                dest_file = dest_path
        else:
            # Destination doesn't exist - check if it looks like a directory
            if dest.endswith('/') or dest.endswith(os.sep):
                # Treat as directory - create it
                create = input(f"Directory {dest_path} does not exist. Create it? [y/N]: ").strip().lower()
                if create in ('y', 'yes'):
                    dest_path.mkdir(parents=True, exist_ok=True)
                    dest_file = dest_path / source.name
                else:
                    print("Cancelled.")
                    return
            else:
                # Treat as file - create parent directory if needed
                parent = dest_path.parent
                if not parent.exists():
                    create = input(f"Parent directory {parent} does not exist. Create it? [y/N]: ").strip().lower()
                    if create in ('y', 'yes'):
                        parent.mkdir(parents=True, exist_ok=True)
                    else:
                        print("Cancelled.")
                        return
                dest_file = dest_path

        import shutil
        shutil.copy2(source, dest_file)
        if not silent:
            print(f"\nCopied to: {dest_file}")

    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nError copying file: {e}")


def clone_session(session_id: str, project_path: str, shell_mode: bool = False, claude_home: Optional[str] = None):
    """Clone a Claude session to a new file with new UUID and resume it."""
    import shutil
    import uuid

    # Get the original session file path
    source_path = Path(get_session_file_path(session_id, project_path, claude_home))

    if not source_path.exists():
        print(f"\nError: Session file not found: {source_path}")
        return

    # Generate new UUID for cloned session
    new_session_id = str(uuid.uuid4())

    # Create destination path with new UUID in same directory
    dest_path = source_path.parent / f"{new_session_id}.jsonl"

    try:
        # Copy the file
        shutil.copy2(source_path, dest_path)

        # Update sessionId in all lines to match the new filename UUID
        from claude_code_tools.trim_session import update_session_id_in_file
        update_session_id_in_file(dest_path, new_session_id, agent="claude")

        if not shell_mode:
            print(f"\nCloned session:")
            print(f"  Original: {session_id}")
            print(f"  New:      {new_session_id}")
            print(f"\nResuming cloned session...")

        # Resume the new cloned session
        resume_session(new_session_id, project_path, shell_mode=shell_mode, claude_home=claude_home)

    except Exception as e:
        print(f"\nError cloning session: {e}")
        return


def resume_session(session_id: str, project_path: str, shell_mode: bool = False, claude_home: Optional[str] = None):
    """Resume a Claude session using claude -r command."""
    current_dir = os.getcwd()

    # In shell mode, output commands for the shell to evaluate
    if shell_mode:
        if project_path != current_dir:
            print(f'cd {shlex.quote(project_path)}')
        # Set CLAUDE_CONFIG_DIR environment variable if custom path specified
        # (either via CLI arg or already set via env var)
        if claude_home or os.environ.get('CLAUDE_CONFIG_DIR'):
            expanded_home = str(get_claude_home(claude_home).absolute())
            print(f'CLAUDE_CONFIG_DIR={shlex.quote(expanded_home)} claude -r {shlex.quote(session_id)}')
        else:
            print(f'claude -r {shlex.quote(session_id)}')
        return
    
    # Check if we need to change directory
    change_dir = False
    if project_path != current_dir:
        if RICH_AVAILABLE and console:
            console.print(f"\n[yellow]This session is from a different project:[/yellow]")
            console.print(f"  Current directory: {current_dir}")
            console.print(f"  Session directory: {project_path}")
            
            if Confirm.ask("\nChange to the session's directory?", default=True):
                change_dir = True
            else:
                console.print("[yellow]Staying in current directory. Session resume may fail.[/yellow]")
        else:
            print(f"\nThis session is from a different project:")
            print(f"  Current directory: {current_dir}")
            print(f"  Session directory: {project_path}")
            
            response = input("\nChange to the session's directory? [Y/n]: ").strip().lower()
            if response != 'n':
                change_dir = True
            else:
                print("Staying in current directory. Session resume may fail.")
    
    if RICH_AVAILABLE and console:
        console.print(f"\n[green]Resuming session:[/green] {session_id}")
        if change_dir:
            console.print("\n[yellow]Note:[/yellow] To persist directory changes, use this shell function:")
            console.print("[dim]fcs() { eval $(find-claude-session --shell \"$@\"); }[/dim]")
            console.print("Then use [bold]fcs[/bold] instead of [bold]find-claude-session[/bold]\n")
    else:
        print(f"\nResuming session: {session_id}")
        if change_dir:
            print("\nNote: To persist directory changes, use this shell function:")
            print("fcs() { eval $(find-claude-session --shell \"$@\"); }")
            print("Then use 'fcs' instead of 'find-claude-session'\n")
    
    try:
        # Change directory if needed (won't persist after exit)
        if change_dir and project_path != current_dir:
            os.chdir(project_path)

        # Set CLAUDE_CONFIG_DIR environment variable if custom path specified
        # (either via CLI arg or already set via env var)
        if claude_home or os.environ.get('CLAUDE_CONFIG_DIR'):
            # Get the resolved home directory (respects precedence)
            expanded_home = str(get_claude_home(claude_home).absolute())
            os.environ['CLAUDE_CONFIG_DIR'] = expanded_home

        # Execute claude
        os.execvp("claude", ["claude", "-r", session_id])
        
    except FileNotFoundError:
        if RICH_AVAILABLE and console:
            console.print("[red]Error:[/red] 'claude' command not found. Make sure Claude CLI is installed.")
        else:
            print("Error: 'claude' command not found. Make sure Claude CLI is installed.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if RICH_AVAILABLE and console:
            console.print(f"[red]Error:[/red] {e}")
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def create_action_handler(claude_home: Optional[str] = None, nonlaunch_flag: Optional[dict] = None):
    """
    Create an action handler function for TUI/UI integration.

    Args:
        claude_home: Optional Claude home directory

    Returns:
        Function that handles session actions
    """
    def handle_session_action(
        session: Tuple, action: str, kwargs: Optional[dict] = None
    ) -> None:
        """Handle actions for a selected session."""
        kwargs = kwargs or {}

        if isinstance(session, dict):
            session_id = session.get("session_id")
            project_path = session.get("cwd")
        else:
            session_id = session[0]
            project_path = session[6]

        if action == "resume":
            resume_session(session_id, project_path, shell_mode=False, claude_home=claude_home)
        elif action == "suppress_resume":
            tools = kwargs.get("tools")
            threshold = kwargs.get("threshold")
            trim_assistant = kwargs.get("trim_assistant")
            if tools is None and threshold is None and trim_assistant is None:
                options = prompt_suppress_options()
                if not options:
                    return
                tools, threshold, trim_assistant = options
            handle_suppress_resume_claude(
                session_id, project_path, tools, threshold or 500, trim_assistant, claude_home
            )
        elif action == "smart_trim_resume":
            handle_smart_trim_resume_claude(session_id, project_path, claude_home)
        elif action == "path":
            # handled in Node UI via RPC
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session_id
        elif action == "copy":
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session_id
        elif action == "clone":
            clone_session(session_id, project_path, shell_mode=False, claude_home=claude_home)
        elif action == "export":
            session_file_path = get_session_file_path(session_id, project_path, claude_home)
            handle_export_session(session_file_path)
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session_id
        elif action == "continue":
            from claude_code_tools.session_utils import continue_with_options
            session_file_path = get_session_file_path(session_id, project_path, claude_home)
            continue_with_options(
                session_file_path,
                "claude",
                claude_home=claude_home,
                codex_home=None
            )

    return handle_session_action


def main():
    parser = argparse.ArgumentParser(
        description="Search Claude Code session files by keywords",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    find-claude-session "langroid"
    find-claude-session "langroid,MCP"
    find-claude-session "error,TypeError,function" --global
    find-claude-session "bug fix" -g

To persist directory changes when resuming sessions:
    Add this to your shell config (.bashrc/.zshrc):
    fcs() { eval $(find-claude-session --shell "$@"); }
    
    Then use: fcs "keyword" -g
        """
    )
    parser.add_argument(
        "keywords",
        nargs='?',
        default="",
        help="Comma-separated keywords to search for (case-insensitive). If omitted, shows all sessions."
    )
    parser.add_argument(
        "-g", "--global",
        action="store_true",
        help="Search across all Claude projects, not just the current one"
    )
    parser.add_argument(
        "-n", "--num-matches",
        type=int,
        default=10,
        help="Number of matching sessions to display (default: 10)"
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Output shell commands for evaluation (for use with shell function)"
    )
    parser.add_argument(
        "--claude-home",
        type=str,
        help="Path to Claude home directory (default: ~/.claude)"
    )
    parser.add_argument(
        "--original",
        action="store_true",
        help="Show only original sessions (excludes trimmed, continued, and sub-agent sessions)"
    )
    parser.add_argument(
        "--no-sub",
        action="store_true",
        help="Exclude sub-agent sessions from results"
    )
    parser.add_argument(
        "--no-trim",
        action="store_true",
        help="Exclude trimmed sessions from results"
    )
    parser.add_argument(
        "--no-roll",
        dest="no_cont",  # Keep internal name for compatibility
        action="store_true",
        help="Exclude rollover sessions from results"
    )
    parser.add_argument(
        "--simple-ui",
        dest="simple_ui",
        action="store_true",
        help="Use simple Rich table UI instead of Node interactive UI"
    )
    parser.add_argument(
        "--min-lines",
        type=int,
        default=0,
        help="Only show sessions with at least this many lines (default: 0 = no minimum)"
    )
    parser.add_argument(
        "--before",
        type=str,
        help="Only show sessions modified before this time (inclusive). "
             "Formats: YYYYMMDD, YYYY-MM-DD, MM/DD/YY, with optional T or space + HH:MM:SS",
    )
    parser.add_argument(
        "--after",
        type=str,
        help="Only show sessions modified after this time (inclusive). "
             "Formats: YYYYMMDD, YYYY-MM-DD, MM/DD/YY, with optional T or space + HH:MM:SS",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Skip interactive options menu and run search directly with CLI args",
    )

    args = parser.parse_args()

    # Show interactive options UI unless --no-ui or --simple-ui
    if not args.no_ui and not args.simple_ui:
        initial_options = {
            "keywords": args.keywords or "",
            "global": getattr(args, 'global', False),
            "num_matches": args.num_matches,
            "original": args.original,
            "no_sub": args.no_sub,
            "no_trim": args.no_trim,
            "no_cont": args.no_cont,
            "min_lines": args.min_lines or "",
            "before": args.before or "",
            "after": args.after or "",
        }
        opts = run_find_options_ui(initial_options, variant="find-claude")
        if opts is None:
            sys.exit(0)
        # Update args with user's selections
        args.keywords = opts.get("keywords") or ""
        setattr(args, 'global', opts.get("global", False))
        args.num_matches = opts.get("num_matches", 10)
        args.original = opts.get("original", False)
        args.no_sub = opts.get("no_sub", False)
        args.no_trim = opts.get("no_trim", False)
        args.no_cont = opts.get("no_cont", False)
        args.min_lines = opts.get("min_lines") or 0
        args.before = opts.get("before")
        args.after = opts.get("after")

        # Build and display equivalent CLI command
        cmd_parts = ["aichat find-claude"]
        if args.keywords:
            cmd_parts.append(f'"{args.keywords}"')
        if getattr(args, 'global'):
            cmd_parts.append("-g")
        if args.num_matches != 10:
            cmd_parts.append(f"-n {args.num_matches}")
        if args.original:
            cmd_parts.append("--original")
        if args.no_sub:
            cmd_parts.append("--no-sub")
        if args.no_trim:
            cmd_parts.append("--no-trim")
        if args.no_cont:
            cmd_parts.append("--no-roll")
        if args.min_lines:
            cmd_parts.append(f"--min-lines {args.min_lines}")
        if args.before:
            cmd_parts.append(f"--before {args.before}")
        if args.after:
            cmd_parts.append(f"--after {args.after}")
        cmd_parts.append("--no-ui")
        print(f"\n→ {' '.join(cmd_parts)}\n", file=sys.stderr)

    # Parse keywords
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []

    # Display informational message about what session types are being shown
    if args.original:
        print("Showing: Original sessions only (excluding trimmed, rollover, and sub-agent sessions)", file=sys.stderr)
    else:
        # Build list of included/excluded types
        excluded_types = []
        if args.no_sub:
            excluded_types.append("sub-agent")
        if args.no_trim:
            excluded_types.append("trimmed")
        if args.no_cont:
            excluded_types.append("rollover")

        if excluded_types:
            excluded_str = ", ".join(excluded_types)
            print(f"Showing: All sessions except {excluded_str}", file=sys.stderr)
        else:
            print("Showing: All session types (original, trimmed, rollover, and sub-agent)", file=sys.stderr)
            print("Tip: Use --no-sub, --no-trim, or --no-roll to exclude specific types", file=sys.stderr)
    print(file=sys.stderr)  # Blank line for readability

    # Check if searching current project only
    if not getattr(args, 'global'):
        claude_dir = get_claude_project_dir(args.claude_home)

        if not claude_dir.exists():
            print(f"No Claude project directory found for: {os.getcwd()}", file=sys.stderr)
            print(f"Expected directory: {claude_dir}", file=sys.stderr)
            sys.exit(1)

    # Find matching sessions
    matching_sessions = find_sessions(
        keywords,
        global_search=getattr(args, 'global'),
        claude_home=args.claude_home,
        original_only=args.original,
        no_sub=args.no_sub,
        no_trim=args.no_trim,
        no_cont=args.no_cont,
    )

    # Filter by minimum lines if specified (line_count is at index 3 in tuple)
    if args.min_lines > 0:
        matching_sessions = [s for s in matching_sessions if s[3] >= args.min_lines]

    # Filter by time bounds if specified (mod_time is at index 1 in tuple)
    if args.before or args.after:
        matching_sessions = filter_sessions_by_time(
            matching_sessions, before=args.before, after=args.after, time_index=1
        )

    if not matching_sessions:
        scope = "all projects" if getattr(args, 'global') else "current project"
        keyword_msg = f" containing all keywords: {', '.join(keywords)}" if keywords else ""
        if RICH_AVAILABLE and console and not args.shell:
            console.print(f"[yellow]No sessions found{keyword_msg} in {scope}[/yellow]")
        else:
            print(f"No sessions found{keyword_msg} in {scope}", file=sys.stderr)
        sys.exit(0)
    
    # ============================================================
    # Default: Node UI (rich interactive interface)
    # --simple-ui: Falls back to Rich table UI
    # ============================================================
    nonlaunch_flag = {"done": False}
    action_handler = create_action_handler(args.claude_home, nonlaunch_flag=nonlaunch_flag)
    rpc_path = str(Path(__file__).parent / "action_rpc.py")

    if not args.simple_ui:
        from claude_code_tools.export_session import extract_session_metadata

        def get_custom_title(session_id: str, cwd: str) -> str:
            """Extract custom title from session file if present."""
            try:
                fp = get_session_file_path(session_id, cwd, args.claude_home)
                if fp:
                    meta = extract_session_metadata(Path(fp), "claude")
                    return meta.get("customTitle", "")
            except Exception:
                pass
            return ""

        limited = [
            {
                "agent": "claude",
                "agent_display": "Claude",
                "session_id": s[0],
                "mod_time": s[1],
                "create_time": s[2],
                "lines": s[3],
                "project": s[4],
                "preview": s[5],
                "cwd": s[6],
                "branch": s[7] if len(s) > 7 else "",
                "file_path": get_session_file_path(s[0], s[6], args.claude_home),
                "claude_home": args.claude_home,
                "is_trimmed": s[8] if len(s) > 8 else False,
                "derivation_type": None,
                "is_sidechain": s[9] if len(s) > 9 else False,
                "custom_title": get_custom_title(s[0], s[6]),
            }
            for s in matching_sessions[: args.num_matches]
        ]

        focus_id = None
        start_action = False
        while True:
            nonlaunch_flag["done"] = False
            run_node_menu_ui(
                limited,
                keywords,
                action_handler,
                stderr_mode=args.shell,
                focus_session_id=focus_id,
                start_action=start_action,
                rpc_path=rpc_path,
            )
            if nonlaunch_flag["done"]:
                choice = prompt_post_action()
                if choice == "back":
                    focus_id = nonlaunch_flag.get("session_id")
                    start_action = True
                    continue
            break
    elif RICH_AVAILABLE and console:
        # Use Rich-based interactive UI
        selected_session = display_interactive_ui(matching_sessions, keywords, stderr_mode=args.shell, num_matches=args.num_matches)
        if selected_session:
            # Show action menu
            action = show_action_menu(selected_session)
            if not action:
                return

            session_id = selected_session[0]
            project_path = selected_session[6]  # Updated index after adding creation_time

            # Perform selected action
            if action == "resume":
                resume_session(session_id, project_path, shell_mode=args.shell, claude_home=args.claude_home)
            elif action == "suppress_resume":
                # Prompt for suppress options
                options = prompt_suppress_options()
                if options:
                    tools, threshold, trim_assistant = options
                    handle_suppress_resume_claude(
                        session_id, project_path, tools, threshold, trim_assistant, args.claude_home
                    )
            elif action == "smart_trim_resume":
                # Smart trim using parallel agents
                handle_smart_trim_resume_claude(
                    session_id, project_path, args.claude_home
                )
            elif action == "path":
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                print(f"\nSession file path:")
                print(session_file_path)
            elif action == "copy":
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                copy_session_file(session_file_path)
            elif action == "clone":
                clone_session(session_id, project_path, shell_mode=args.shell, claude_home=args.claude_home)
            elif action == "export":
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                handle_export_session(session_file_path)
            elif action == "continue":
                # Continue with context in fresh session
                from claude_code_tools.session_utils import continue_with_options
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                continue_with_options(
                    session_file_path,
                    "claude",
                    claude_home=args.claude_home,
                    codex_home=None
                )
    else:
        # Fallback: print session IDs as before
        if not args.shell:
            print("\nMatching sessions:")
        for idx, (session_id, mod_time, create_time, line_count, project_name, preview, project_path, git_branch) in enumerate(matching_sessions[:args.num_matches], 1):
            create_date = datetime.fromtimestamp(create_time).strftime('%m/%d')
            mod_date = datetime.fromtimestamp(mod_time).strftime('%m/%d %H:%M')
            date_display = f"{create_date} - {mod_date}"
            branch_display = git_branch if git_branch else "N/A"
            if getattr(args, 'global'):
                print(f"{idx}. {session_id} | {project_name} | {branch_display} | {date_display} | {line_count} lines", file=sys.stderr if args.shell else sys.stdout)
            else:
                print(f"{idx}. {session_id} | {branch_display} | {date_display} | {line_count} lines", file=sys.stderr if args.shell else sys.stdout)

        if len(matching_sessions) > args.num_matches:
            print(f"\n... and {len(matching_sessions) - args.num_matches} more sessions", file=sys.stderr if args.shell else sys.stdout)

        # Simple selection without rich
        if len(matching_sessions) == 1:
            if not args.shell:
                print("\nOnly one match found. Resuming automatically...")
            session_id, _, _, _, _, _, project_path, _, _, _ = matching_sessions[0]
            resume_session(session_id, project_path, shell_mode=args.shell, claude_home=args.claude_home)
        else:
            try:
                if args.shell:
                    # In shell mode, read from stdin but prompt to stderr
                    sys.stderr.write("\nEnter number to resume session (or Ctrl+C to cancel): ")
                    sys.stderr.flush()
                    choice = sys.stdin.readline().strip()
                else:
                    choice = input("\nEnter number to resume session (or Ctrl+C to cancel): ")

                # Handle empty input or EOF
                if not choice:
                    print("Cancelled (EOF)", file=sys.stderr)
                    sys.exit(0)

                idx = int(choice) - 1
                if 0 <= idx < min(args.num_matches, len(matching_sessions)):
                    session_id, _, _, _, _, _, project_path, _, _, _ = matching_sessions[idx]
                    resume_session(session_id, project_path, shell_mode=args.shell, claude_home=args.claude_home)
                else:
                    print("Invalid choice", file=sys.stderr)
                    sys.exit(1)
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled", file=sys.stderr)
                sys.exit(0)
            except ValueError:
                print("Invalid input", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
