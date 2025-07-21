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
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set, Tuple, Optional

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


def get_claude_project_dir() -> Path:
    """Convert current working directory to Claude project directory path."""
    cwd = os.getcwd()

    # Replace / with - to match Claude's directory naming convention
    project_path = cwd.replace("/", "-")
    claude_dir = Path.home() / ".claude" / "projects" / project_path
    return claude_dir


def get_all_claude_projects() -> List[Tuple[Path, str]]:
    """Get all Claude project directories with their original paths."""
    projects_dir = Path.home() / ".claude" / "projects"
    
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


def search_keywords_in_file(filepath: Path, keywords: List[str]) -> tuple[bool, int]:
    """
    Check if all keywords are present in the JSONL file and count lines.
    
    Args:
        filepath: Path to the JSONL file
        keywords: List of keywords to search for (case-insensitive)
        
    Returns:
        Tuple of (matches: bool, line_count: int)
        - matches: True if ALL keywords are found in the file
        - line_count: Total number of lines in the file
    """
    # Convert keywords to lowercase for case-insensitive search
    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    line_count = 0
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line_count += 1
                line_lower = line.lower()
                # Check which keywords are in this line
                for keyword in keywords_lower:
                    if keyword in line_lower:
                        found_keywords.add(keyword)
    except Exception:
        # Skip files that can't be read
        return False, 0
    
    matches = len(found_keywords) == len(keywords_lower)
    return matches, line_count


def get_session_preview(filepath: Path) -> str:
    """Get a preview of the session from the first user message."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    if data.get('type') == 'message' and data.get('role') == 'user':
                        content = data.get('content', '')
                        if isinstance(content, str):
                            # Get first 60 chars of the message
                            preview = content.strip().replace('\n', ' ')[:60]
                            if len(content) > 60:
                                preview += "..."
                            return preview
                        elif isinstance(content, list):
                            # Handle structured content
                            for item in content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    text = item.get('text', '')
                                    preview = text.strip().replace('\n', ' ')[:60]
                                    if len(text) > 60:
                                        preview += "..."
                                    return preview
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass
    return "No preview available"


def find_sessions(keywords: List[str], global_search: bool = False) -> List[Tuple[str, float, int, str, str, str]]:
    """
    Find all Claude Code sessions containing the specified keywords.
    
    Args:
        keywords: List of keywords to search for
        global_search: If True, search all projects; if False, search current project only
        
    Returns:
        List of tuples (session_id, modification_time, line_count, project_name, preview, project_path) sorted by modification time
    """
    matching_sessions = []
    
    if global_search:
        # Search all projects
        projects = get_all_claude_projects()
        
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
                        matches, line_count = search_keywords_in_file(jsonl_file, keywords)
                        if matches:
                            session_id = jsonl_file.stem
                            mod_time = jsonl_file.stat().st_mtime
                            preview = get_session_preview(jsonl_file)
                            matching_sessions.append((session_id, mod_time, line_count, project_name, preview, original_path))
                    
                    progress.advance(task)
        else:
            # Fallback without rich
            for project_dir, original_path in projects:
                project_name = extract_project_name(original_path)
                
                for jsonl_file in project_dir.glob("*.jsonl"):
                    matches, line_count = search_keywords_in_file(jsonl_file, keywords)
                    if matches:
                        session_id = jsonl_file.stem
                        mod_time = jsonl_file.stat().st_mtime
                        preview = get_session_preview(jsonl_file)
                        matching_sessions.append((session_id, mod_time, line_count, project_name, preview, original_path))
    else:
        # Search current project only
        claude_dir = get_claude_project_dir()
        
        if not claude_dir.exists():
            return []
        
        project_name = extract_project_name(os.getcwd())
        
        # Search all JSONL files in the directory
        for jsonl_file in claude_dir.glob("*.jsonl"):
            matches, line_count = search_keywords_in_file(jsonl_file, keywords)
            if matches:
                session_id = jsonl_file.stem
                mod_time = jsonl_file.stat().st_mtime
                preview = get_session_preview(jsonl_file)
                matching_sessions.append((session_id, mod_time, line_count, project_name, preview, os.getcwd()))
    
    # Sort by modification time (newest first)
    matching_sessions.sort(key=lambda x: x[1], reverse=True)
    
    return matching_sessions


def display_interactive_ui(sessions: List[Tuple[str, float, int, str, str, str]], keywords: List[str], stderr_mode: bool = False) -> Optional[Tuple[str, str]]:
    """Display interactive UI for session selection."""
    if not RICH_AVAILABLE:
        return None
    
    # Use stderr console if in stderr mode
    ui_console = Console(file=sys.stderr) if stderr_mode else console
    if not ui_console:
        return None
    
    # Limit to top 10 sessions
    display_sessions = sessions[:10]
    
    if not display_sessions:
        ui_console.print("[red]No sessions found[/red]")
        return None
    
    # Create table
    table = Table(
        title=f"Sessions matching: {', '.join(keywords)}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan"
    )
    
    table.add_column("#", style="bold yellow", width=3)
    table.add_column("Session ID", style="dim")
    table.add_column("Project", style="green")
    table.add_column("Date", style="blue")
    table.add_column("Preview", style="white", overflow="fold")
    
    for idx, (session_id, mod_time, line_count, project_name, preview, _) in enumerate(display_sessions, 1):
        mod_date = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M')
        table.add_row(
            str(idx),
            session_id[:8] + "...",
            project_name,
            mod_date,
            preview
        )
    
    ui_console.print(table)
    ui_console.print("\n[bold]Select a session:[/bold]")
    ui_console.print("  • Enter number (1-10) to select")
    ui_console.print("  • Press Ctrl+C to cancel\n")
    
    while True:
        try:
            # In stderr mode, we need to ensure nothing goes to stdout
            if stderr_mode:
                # Temporarily redirect stdout to devnull
                old_stdout = sys.stdout
                sys.stdout = open(os.devnull, 'w')
            
            choice = Prompt.ask(
                "Your choice",
                choices=[str(i) for i in range(1, len(display_sessions) + 1)],
                show_choices=False,
                console=ui_console
            )
            
            # Restore stdout
            if stderr_mode:
                sys.stdout.close()
                sys.stdout = old_stdout
            
            idx = int(choice) - 1
            if 0 <= idx < len(display_sessions):
                session_info = display_sessions[idx]
                return (session_info[0], session_info[5])  # Return (session_id, project_path)
                
        except KeyboardInterrupt:
            ui_console.print("\n[yellow]Cancelled[/yellow]")
            return None
        except (ValueError, EOFError):
            ui_console.print("[red]Invalid choice. Please try again.[/red]")


def resume_session(session_id: str, project_path: str, shell_mode: bool = False):
    """Resume a Claude session using claude -r command."""
    current_dir = os.getcwd()
    
    # In shell mode, output commands for the shell to evaluate
    if shell_mode:
        if project_path != current_dir:
            print(f'cd {shlex.quote(project_path)}')
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
        help="Comma-separated keywords to search for (case-insensitive)"
    )
    parser.add_argument(
        "-g", "--global",
        action="store_true",
        help="Search across all Claude projects, not just the current one"
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Output shell commands for evaluation (for use with shell function)"
    )
    
    args = parser.parse_args()
    
    # Parse keywords
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    
    if not keywords:
        print("Error: No keywords provided", file=sys.stderr)
        sys.exit(1)
    
    # Check if searching current project only
    if not getattr(args, 'global'):
        claude_dir = get_claude_project_dir()
        
        if not claude_dir.exists():
            print(f"No Claude project directory found for: {os.getcwd()}", file=sys.stderr)
            print(f"Expected directory: {claude_dir}", file=sys.stderr)
            sys.exit(1)
    
    # Find matching sessions
    matching_sessions = find_sessions(keywords, global_search=getattr(args, 'global'))
    
    if not matching_sessions:
        scope = "all projects" if getattr(args, 'global') else "current project"
        if RICH_AVAILABLE and console and not args.shell:
            console.print(f"[yellow]No sessions found containing all keywords in {scope}:[/yellow] {', '.join(keywords)}")
        else:
            print(f"No sessions found containing all keywords in {scope}: {', '.join(keywords)}", file=sys.stderr)
        sys.exit(0)
    
    # If we have rich and there are results, show interactive UI
    if RICH_AVAILABLE and console:
        result = display_interactive_ui(matching_sessions, keywords, stderr_mode=args.shell)
        if result:
            session_id, project_path = result
            resume_session(session_id, project_path, shell_mode=args.shell)
    else:
        # Fallback: print session IDs as before
        if not args.shell:
            print("\nMatching sessions:")
        for idx, (session_id, mod_time, line_count, project_name, preview, project_path) in enumerate(matching_sessions[:10], 1):
            mod_date = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
            if getattr(args, 'global'):
                print(f"{idx}. {session_id} | {project_name} | {mod_date} | {line_count} lines", file=sys.stderr if args.shell else sys.stdout)
            else:
                print(f"{idx}. {session_id} | {mod_date} | {line_count} lines", file=sys.stderr if args.shell else sys.stdout)
        
        if len(matching_sessions) > 10:
            print(f"\n... and {len(matching_sessions) - 10} more sessions", file=sys.stderr if args.shell else sys.stdout)
        
        # Simple selection without rich
        if len(matching_sessions) == 1:
            if not args.shell:
                print("\nOnly one match found. Resuming automatically...")
            session_id, _, _, _, _, project_path = matching_sessions[0]
            resume_session(session_id, project_path, shell_mode=args.shell)
        else:
            try:
                if args.shell:
                    # In shell mode, read from stdin but prompt to stderr
                    sys.stderr.write("\nEnter number to resume session (or Ctrl+C to cancel): ")
                    sys.stderr.flush()
                    choice = sys.stdin.readline().strip()
                else:
                    choice = input("\nEnter number to resume session (or Ctrl+C to cancel): ")
                    
                idx = int(choice) - 1
                if 0 <= idx < min(10, len(matching_sessions)):
                    session_id, _, _, _, _, project_path = matching_sessions[idx]
                    resume_session(session_id, project_path, shell_mode=args.shell)
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