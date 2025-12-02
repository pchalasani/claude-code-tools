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
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

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
    
    # Use provided claude_home or default to ~/.claude
    if claude_home:
        base_dir = Path(claude_home).expanduser()
    else:
        base_dir = Path.home() / ".claude"

    # Replace / with - to match Claude's directory naming convention
    project_path = cwd.replace("/", "-")
    claude_dir = base_dir / "projects" / project_path
    return claude_dir


def get_all_claude_projects(claude_home: Optional[str] = None) -> List[Tuple[Path, str]]:
    """Get all Claude project directories with their original paths."""
    # Use provided claude_home or default to ~/.claude
    if claude_home:
        base_dir = Path(claude_home).expanduser()
    else:
        base_dir = Path.home() / ".claude"
    
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
        line_count = 0
        git_branch = None
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line_count += 1
                    # Extract git branch from JSON if not already found
                    if git_branch is None:
                        try:
                            data = json.loads(line.strip())
                            if 'gitBranch' in data and data['gitBranch']:
                                git_branch = data['gitBranch']
                        except (json.JSONDecodeError, KeyError):
                            pass
        except Exception:
            return False, 0, None
        return True, line_count, git_branch

    # Convert keywords to lowercase for case-insensitive search
    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    line_count = 0
    git_branch = None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line_count += 1
                line_lower = line.lower()

                # Extract git branch from JSON if not already found
                if git_branch is None:
                    try:
                        data = json.loads(line.strip())
                        if 'gitBranch' in data and data['gitBranch']:
                            git_branch = data['gitBranch']
                    except (json.JSONDecodeError, KeyError):
                        pass

                # Check which keywords are in this line
                for keyword in keywords_lower:
                    if keyword in line_lower:
                        found_keywords.add(keyword)
    except Exception:
        # Skip files that can't be read
        return False, 0, None

    matches = len(found_keywords) == len(keywords_lower)
    return matches, line_count, git_branch


def is_system_message(text: str) -> bool:
    """Check if text is system-generated (XML tags, env context, etc)"""
    if not text or len(text.strip()) < 5:
        return True
    text = text.strip()
    # Check for XML-like tags (user_instructions, environment_context, etc)
    if text.startswith("<") and ">" in text[:100]:
        return True
    return False


def get_session_preview(filepath: Path) -> str:
    """Get a preview of the session from the LAST user message."""
    last_user_message = None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    # Check top-level type for user messages
                    if data.get('type') == 'user':
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

                        # Filter out system messages and keep updating to get LAST message
                        if text and not is_system_message(text):
                            cleaned = text.replace('\n', ' ')[:400]
                            # Prefer substantial messages (>20 chars)
                            if len(cleaned) > 20:
                                last_user_message = cleaned
                            elif last_user_message is None:
                                last_user_message = cleaned

                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass

    return last_user_message if last_user_message else "No preview available"


def find_sessions(keywords: List[str], global_search: bool = False, claude_home: Optional[str] = None) -> List[Tuple[str, float, float, int, str, str, str, Optional[str]]]:
    """
    Find all Claude Code sessions containing the specified keywords.

    Args:
        keywords: List of keywords to search for
        global_search: If True, search all projects; if False, search current project only
        claude_home: Optional custom Claude home directory (defaults to ~/.claude)

    Returns:
        List of tuples (session_id, modification_time, creation_time, line_count, project_name, preview, project_path, git_branch) sorted by modification time
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
                            session_id = jsonl_file.stem
                            stat = jsonl_file.stat()
                            mod_time = stat.st_mtime
                            # Get creation time (birthtime on macOS, ctime elsewhere)
                            create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                            preview = get_session_preview(jsonl_file)
                            matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, original_path, git_branch))
                    
                    progress.advance(task)
        else:
            # Fallback without rich
            for project_dir, original_path in projects:
                project_name = extract_project_name(original_path)

                for jsonl_file in project_dir.glob("*.jsonl"):
                    matches, line_count, git_branch = search_keywords_in_file(jsonl_file, keywords)
                    if matches:
                        session_id = jsonl_file.stem
                        stat = jsonl_file.stat()
                        mod_time = stat.st_mtime
                        # Get creation time (birthtime on macOS, ctime elsewhere)
                        create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                        preview = get_session_preview(jsonl_file)
                        matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, original_path, git_branch))
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
                session_id = jsonl_file.stem
                stat = jsonl_file.stat()
                mod_time = stat.st_mtime
                # Get creation time (birthtime on macOS, ctime elsewhere)
                create_time = getattr(stat, 'st_birthtime', stat.st_ctime)
                preview = get_session_preview(jsonl_file)
                matching_sessions.append((session_id, mod_time, create_time, line_count, project_name, preview, os.getcwd(), git_branch))
    
    # Sort by modification time (newest first)
    matching_sessions.sort(key=lambda x: x[1], reverse=True)
    
    return matching_sessions


def display_interactive_ui(sessions: List[Tuple[str, float, float, int, str, str, str, Optional[str]]], keywords: List[str], stderr_mode: bool = False, num_matches: int = 10) -> Optional[Tuple[str, float, float, int, str, str, str, Optional[str]]]:
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
    
    for idx, session_info in enumerate(display_sessions, 1):
        # Safely unpack session info
        session_id = session_info[0]
        mod_time = session_info[1]
        create_time = session_info[2]
        line_count = session_info[3]
        project_name = session_info[4]
        preview = session_info[5]
        # project_path = session_info[6]  # Not used in display
        git_branch = session_info[7] if len(session_info) > 7 else None

        # Format: "10/04 - 10/09 13:45"
        create_date = datetime.fromtimestamp(create_time).strftime('%m/%d')
        mod_date = datetime.fromtimestamp(mod_time).strftime('%m/%d %H:%M')
        date_display = f"{create_date} - {mod_date}"
        branch_display = git_branch if git_branch else "N/A"
        table.add_row(
            str(idx),
            session_id[:8] + "...",
            project_name,
            branch_display,
            date_display,
            str(line_count),
            preview
        )
    
    ui_console.print(table)
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


def show_action_menu(session_info: Tuple[str, float, float, int, str, str, str, Optional[str]]) -> Optional[str]:
    """
    Show action menu for selected session.

    Returns: action choice ('resume', 'path', 'copy') or None if cancelled
    """
    session_id, _, _, _, project_name, _, project_path, git_branch = session_info

    print(f"\n=== Session: {session_id[:8]}... ===")
    print(f"Project: {project_name}")
    if git_branch:
        print(f"Branch: {git_branch}")
    print("\nWhat would you like to do?")
    print("1. Resume session (default)")
    print("2. Show session file path")
    print("3. Copy session file to file (*.jsonl) or directory")
    print()

    try:
        choice = input("Enter choice [1-3] (or Enter for 1): ").strip()
        if not choice or choice == "1":
            return "resume"
        elif choice == "2":
            return "path"
        elif choice == "3":
            return "copy"
        else:
            print("Invalid choice.")
            return None
    except KeyboardInterrupt:
        print("\nCancelled.")
        return None


def get_session_file_path(session_id: str, project_path: str, claude_home: Optional[str] = None) -> str:
    """Get the full file path for a session."""
    # Convert project path to Claude directory format
    base_dir = Path(claude_home).expanduser() if claude_home else Path.home() / ".claude"
    encoded_path = project_path.replace("/", "-")
    claude_project_dir = base_dir / "projects" / encoded_path
    return str(claude_project_dir / f"{session_id}.jsonl")


def copy_session_file(session_file_path: str) -> None:
    """Copy session file to user-specified file or directory."""
    try:
        dest = input("\nEnter destination file or directory path: ").strip()
        if not dest:
            print("Cancelled.")
            return

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
        print(f"\nCopied to: {dest_file}")

    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nError copying file: {e}")


def resume_session(session_id: str, project_path: str, shell_mode: bool = False, claude_home: Optional[str] = None):
    """Resume a Claude session using claude -r command."""
    current_dir = os.getcwd()

    # In shell mode, output commands for the shell to evaluate
    if shell_mode:
        if project_path != current_dir:
            print(f'cd {shlex.quote(project_path)}')
        # Set CLAUDE_CONFIG_DIR environment variable if custom path specified
        if claude_home:
            expanded_home = str(Path(claude_home).expanduser().absolute())
            print(f'CLAUDE_CONFIG_DIR={shlex.quote(expanded_home)} claude -r {shlex.quote(session_id)}')
        else:
            print(f'claude -r {shlex.quote(session_id)}')
        return
    
    # Check if we need to change directory
    change_dir = False
    if project_path != current_dir:
        if RICH_AVAILABLE and console:
            console.print("\n[yellow]This session is from a different project:[/yellow]")
            console.print(f"  Current directory: {current_dir}")
            console.print(f"  Session directory: {project_path}")
            
            if Confirm.ask("\nChange to the session's directory?", default=True):
                change_dir = True
            else:
                console.print("[yellow]Staying in current directory. Session resume may fail.[/yellow]")
        else:
            print("\nThis session is from a different project:")
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
        if claude_home:
            # Expand ~ and make absolute
            expanded_home = str(Path(claude_home).expanduser().absolute())
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
    
    args = parser.parse_args()
    
    # Parse keywords
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []
    
    # Check if searching current project only
    if not getattr(args, 'global'):
        claude_dir = get_claude_project_dir(args.claude_home)
        
        if not claude_dir.exists():
            print(f"No Claude project directory found for: {os.getcwd()}", file=sys.stderr)
            print(f"Expected directory: {claude_dir}", file=sys.stderr)
            sys.exit(1)
    
    # Find matching sessions
    matching_sessions = find_sessions(keywords, global_search=getattr(args, 'global'), claude_home=args.claude_home)
    
    if not matching_sessions:
        scope = "all projects" if getattr(args, 'global') else "current project"
        keyword_msg = f" containing all keywords: {', '.join(keywords)}" if keywords else ""
        if RICH_AVAILABLE and console and not args.shell:
            console.print(f"[yellow]No sessions found{keyword_msg} in {scope}[/yellow]")
        else:
            print(f"No sessions found{keyword_msg} in {scope}", file=sys.stderr)
        sys.exit(0)
    
    # If we have rich and there are results, show interactive UI
    if RICH_AVAILABLE and console:
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
            elif action == "path":
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                print("\nSession file path:")
                print(session_file_path)
            elif action == "copy":
                session_file_path = get_session_file_path(session_id, project_path, args.claude_home)
                copy_session_file(session_file_path)
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
            session_id, _, _, _, _, _, project_path, _ = matching_sessions[0]
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
                    session_id, _, _, _, _, project_path, _ = matching_sessions[idx]
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