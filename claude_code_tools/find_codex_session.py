#!/usr/bin/env python3
"""
Find and resume Codex sessions by searching keywords in session history.

Usage:
    find-codex-session "keywords" [OPTIONS]
    fcs-codex "keywords" [OPTIONS]  # via shell wrapper

Examples:
    find-codex-session "langroid,MCP"           # Current project only
    find-codex-session "error,debugging" -g     # All projects
    find-codex-session "keywords" -n 5          # Limit results
    fcs-codex "keywords" --shell                # Via shell wrapper
"""

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def get_codex_home(custom_home: Optional[str] = None) -> Path:
    """Get the Codex home directory."""
    if custom_home:
        return Path(custom_home).expanduser()
    return Path.home() / ".codex"


def extract_session_id_from_filename(filename: str) -> Optional[str]:
    """
    Extract session ID from Codex session filename.

    Format: rollout-YYYY-MM-DDTHH-MM-SS-<SESSION_ID>.jsonl
    Returns: SESSION_ID portion
    """
    # Pattern: anything after the timestamp part
    # e.g., rollout-2025-10-07T13-48-15-0199bfc9-c444-77e1-8c8a-f91c94fcd832.jsonl
    match = re.match(
        r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl", filename
    )
    if match:
        return match.group(1)
    return None


def extract_session_metadata(session_file: Path) -> Optional[dict]:
    """
    Extract metadata from the first session_meta entry in a Codex session file.

    Returns dict with: id, cwd, branch, timestamp
    """
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "session_meta":
                        payload = entry.get("payload", {})
                        git_info = payload.get("git", {})
                        return {
                            "id": payload.get("id", ""),
                            "cwd": payload.get("cwd", ""),
                            "branch": git_info.get("branch", ""),
                            "timestamp": payload.get("timestamp", ""),
                        }
                except json.JSONDecodeError:
                    continue
        return None
    except (OSError, IOError):
        return None


def get_project_name(cwd: str) -> str:
    """Extract project name from working directory path."""
    if not cwd:
        return "unknown"
    path = Path(cwd)
    return path.name if path.name else "unknown"


def is_system_message(text: str) -> bool:
    """Check if text is system-generated (XML tags, env context, etc)"""
    if not text or len(text.strip()) < 5:
        return True
    text = text.strip()
    # Check for XML-like tags (user_instructions, environment_context, etc)
    if text.startswith("<") and ">" in text[:100]:
        return True
    return False


def search_keywords_in_file(
    session_file: Path, keywords: list[str]
) -> tuple[bool, int, Optional[str]]:
    """
    Search for keywords in a Codex session file.

    Returns: (found, line_count, preview)
    - found: True if all keywords found (case-insensitive AND logic), or True if no keywords
    - line_count: total lines in file
    - preview: best user message content (skips system messages)
    """
    # If no keywords, match all files
    if not keywords:
        line_count = 0
        last_user_message = None
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_count += 1
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        # Extract user messages (skip system messages)
                        if (
                            entry.get("type") == "response_item"
                            and entry.get("payload", {}).get("role") == "user"
                        ):
                            content = entry.get("payload", {}).get("content", [])
                            if isinstance(content, list) and len(content) > 0:
                                first_item = content[0]
                                if isinstance(first_item, dict):
                                    text = first_item.get("text", "")
                                    if text and not is_system_message(text):
                                        cleaned = text[:400].replace("\n", " ").strip()
                                        if len(cleaned) > 20:
                                            last_user_message = cleaned
                                        elif last_user_message is None:
                                            last_user_message = cleaned
                    except json.JSONDecodeError:
                        continue
            return True, line_count, last_user_message
        except (OSError, IOError):
            return False, 0, None

    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    line_count = 0
    last_user_message = None  # Keep track of the LAST user message

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line_count += 1
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)

                    # Extract user messages (skip system messages)
                    # Keep updating to get the LAST one
                    if (
                        entry.get("type") == "response_item"
                        and entry.get("payload", {}).get("role") == "user"
                    ):
                        content = entry.get("payload", {}).get("content", [])
                        if isinstance(content, list) and len(content) > 0:
                            first_item = content[0]
                            if isinstance(first_item, dict):
                                text = first_item.get("text", "")
                                if text and not is_system_message(text):
                                    # Keep updating with latest message
                                    cleaned = text[:400].replace("\n", " ").strip()
                                    # Only keep if it's substantial (>20 chars)
                                    if len(cleaned) > 20:
                                        last_user_message = cleaned
                                    elif last_user_message is None:
                                        # Keep even short messages if no better option
                                        last_user_message = cleaned

                    # Search for keywords in all text content
                    line_lower = line.lower()
                    for kw in keywords_lower:
                        if kw in line_lower:
                            found_keywords.add(kw)

                except json.JSONDecodeError:
                    continue

        all_found = len(found_keywords) == len(keywords_lower)
        return all_found, line_count, last_user_message

    except (OSError, IOError):
        return False, 0, None


def find_sessions(
    codex_home: Path,
    keywords: list[str],
    num_matches: int = 10,
    global_search: bool = False,
) -> list[dict]:
    """
    Find Codex sessions matching keywords.

    Args:
        codex_home: Path to Codex home directory
        keywords: List of keywords to search for
        num_matches: Maximum number of results to return
        global_search: If False, filter to current directory only

    Returns list of dicts with: session_id, project, branch, date,
                                 lines, preview, cwd, file_path
    """
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return []

    # Get current directory for filtering (if not global search)
    current_cwd = os.getcwd() if not global_search else None

    matches = []

    # Walk through YYYY/MM/DD directory structure
    for year_dir in sorted(sessions_dir.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue

        for month_dir in sorted(year_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue

            for day_dir in sorted(month_dir.iterdir(), reverse=True):
                if not day_dir.is_dir():
                    continue

                # Process all JSONL files in this day
                session_files = sorted(
                    day_dir.glob("rollout-*.jsonl"), reverse=True
                )

                for session_file in session_files:
                    # Search for keywords
                    found, line_count, preview = search_keywords_in_file(
                        session_file, keywords
                    )

                    if not found:
                        continue

                    # Extract metadata
                    metadata = extract_session_metadata(session_file)
                    if not metadata:
                        # Fallback: extract session ID from filename
                        session_id = extract_session_id_from_filename(
                            session_file.name
                        )
                        if not session_id:
                            continue
                        metadata = {
                            "id": session_id,
                            "cwd": "",
                            "branch": "",
                            "timestamp": "",
                        }

                    # Filter by current directory if not global search
                    if current_cwd and metadata["cwd"] != current_cwd:
                        continue

                    # Get file stats for timestamps
                    stat = session_file.stat()
                    mod_time = stat.st_mtime
                    create_time = getattr(stat, 'st_birthtime', stat.st_ctime)

                    # Format dates: "10/04 - 10/09 13:45"
                    create_date = datetime.fromtimestamp(create_time).strftime("%m/%d")
                    mod_date = datetime.fromtimestamp(mod_time).strftime("%m/%d %H:%M")
                    date_str = f"{create_date} - {mod_date}"

                    matches.append(
                        {
                            "session_id": metadata["id"],
                            "project": get_project_name(metadata["cwd"]),
                            "branch": metadata["branch"] or "",
                            "date": date_str,
                            "mod_time": mod_time,  # For sorting
                            "lines": line_count,
                            "preview": preview or "No preview",
                            "cwd": metadata["cwd"],
                            "file_path": str(session_file),
                        }
                    )

                    # Early exit if we have enough matches
                    if len(matches) >= num_matches * 3:
                        break

    # Sort by modification time (newest first) and limit
    matches.sort(key=lambda x: x["mod_time"], reverse=True)
    return matches[:num_matches]


def display_interactive_ui(
    matches: list[dict],
    keywords: Optional[list[str]] = None,
) -> Optional[dict]:
    """
    Display matches in interactive UI and get user selection.

    Returns: selected match dict or None if cancelled
    """
    if not matches:
        print("No matching sessions found.")
        return None

    if RICH_AVAILABLE:
        console = Console()
        title = f"Codex Sessions matching: {', '.join(keywords)}" if keywords else "All Codex Sessions"
        table = Table(title=title, show_header=True)
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Session ID", style="yellow", no_wrap=True)
        table.add_column("Project", style="green")
        table.add_column("Branch", style="magenta")
        table.add_column("Date-Range", style="blue")
        table.add_column("Lines", justify="right")
        table.add_column("Last User Message", style="dim", max_width=60, overflow="fold")

        for i, match in enumerate(matches, 1):
            table.add_row(
                str(i),
                match["session_id"][:16] + "...",
                match["project"],
                match["branch"],
                match["date"],
                str(match["lines"]),
                match["preview"],  # No truncation, let Rich wrap it
            )

        console.print(table)
    else:
        # Fallback to plain text
        print("\nMatching Codex Sessions:")
        print("-" * 80)
        for i, match in enumerate(matches, 1):
            print(f"{i}. {match['session_id'][:16]}...")
            print(f"   Project: {match['project']}")
            print(f"   Branch: {match['branch']}")
            print(f"   Date: {match['date']}")
            print(f"   Preview: {match['preview'][:60]}...")
            print()

    # Get user selection
    if len(matches) == 1:
        print(f"\nAuto-selecting only match: {matches[0]['session_id'][:16]}...")
        return matches[0]

    try:
        choice = input(
            "\nEnter number to select session (or Enter to cancel): "
        ).strip()
        if not choice:
            print("Cancelled.")
            return None

        idx = int(choice) - 1
        if 0 <= idx < len(matches):
            return matches[idx]
        else:
            print("Invalid selection.")
            return None
    except ValueError:
        print("Invalid input.")
        return None
    except KeyboardInterrupt:
        print("\nCancelled.")
        return None


def show_action_menu(match: dict) -> Optional[str]:
    """
    Show action menu for selected session.

    Returns: action choice ('resume', 'path', 'copy') or None if cancelled
    """
    print(f"\n=== Session: {match['session_id'][:16]}... ===")
    print(f"Project: {match['project']}")
    print(f"Branch: {match['branch']}")
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


def copy_session_file(file_path: str) -> None:
    """Copy session file to user-specified file or directory."""
    try:
        dest = input("\nEnter destination file or directory path: ").strip()
        if not dest:
            print("Cancelled.")
            return

        dest_path = Path(dest).expanduser()
        source = Path(file_path)

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


def resume_session(
    session_id: str, cwd: str, shell_mode: bool = False
) -> None:
    """
    Resume a Codex session.

    In shell mode: outputs commands for eval
    In interactive mode: executes codex resume
    """
    if shell_mode:
        # Output commands for shell eval
        # Redirect prompts to stderr, commands to stdout
        if cwd and cwd != os.getcwd():
            print(f"cd {shlex.quote(cwd)}", file=sys.stdout)
        print(f"codex resume {shlex.quote(session_id)}", file=sys.stdout)
    else:
        # Interactive mode
        if cwd and cwd != os.getcwd():
            response = input(
                f"\nSession is in different directory: {cwd}\n"
                "Change directory and resume? [Y/n]: "
            ).strip()
            if response.lower() in ("", "y", "yes"):
                try:
                    os.chdir(cwd)
                    print(f"Changed to: {cwd}")
                except OSError as e:
                    print(f"Error changing directory: {e}")
                    return

        # Execute codex resume
        try:
            os.execvp("codex", ["codex", "resume", session_id])
        except OSError as e:
            print(f"Error launching codex: {e}")
            sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Find and resume Codex sessions by keyword search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  find-codex-session "langroid,MCP"           # Current project only
  find-codex-session "error,debugging" -g     # All projects
  find-codex-session "keywords" -n 5          # Limit results
  fcs-codex "keywords" --shell                # Via shell wrapper
        """,
    )

    parser.add_argument(
        "keywords",
        nargs='?',
        default="",
        help="Comma-separated keywords to search (AND logic). If omitted, shows all sessions.",
    )
    parser.add_argument(
        "-g",
        "--global",
        dest="global_search",
        action="store_true",
        help="Search all projects (default: current project only)",
    )
    parser.add_argument(
        "-n",
        "--num-matches",
        type=int,
        default=10,
        help="Number of matches to display (default: 10)",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Output shell commands for eval (enables persistent cd)",
    )
    parser.add_argument(
        "--codex-home",
        help="Custom Codex home directory (default: ~/.codex)",
    )

    args = parser.parse_args()

    # Parse keywords
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []

    # Get Codex home
    codex_home = get_codex_home(args.codex_home)
    if not codex_home.exists():
        print(f"Error: Codex home not found: {codex_home}", file=sys.stderr)
        sys.exit(1)

    # Find matching sessions
    matches = find_sessions(
        codex_home, keywords, args.num_matches, args.global_search
    )

    # Display and get selection
    selected_match = display_interactive_ui(matches, keywords)
    if not selected_match:
        return

    # Show action menu
    action = show_action_menu(selected_match)
    if not action:
        return

    # Perform selected action
    if action == "resume":
        resume_session(
            selected_match["session_id"],
            selected_match["cwd"],
            args.shell
        )
    elif action == "path":
        print("\nSession file path:")
        print(selected_match["file_path"])
    elif action == "copy":
        copy_session_file(selected_match["file_path"])


if __name__ == "__main__":
    main()
