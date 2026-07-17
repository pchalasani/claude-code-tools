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
import time
import termios
import tty
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from claude_code_tools.session_menu import (
    show_action_menu as menu_show_action_menu,
    show_resume_submenu as menu_show_resume_submenu,
    prompt_suppress_options as menu_prompt_suppress_options,
)
from claude_code_tools.node_menu_ui import run_node_menu_ui


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
from claude_code_tools.trim_session import (
    trim_and_create_session,
    is_trimmed_session,
    get_session_derivation_type,
)
from claude_code_tools.smart_trim_core import (
    identify_trimmable_lines_cli,
    is_claude_cli_available,
)
from claude_code_tools.smart_trim import trim_lines
from claude_code_tools.session_utils import (
    get_codex_home,
    get_session_uuid,
    format_session_id_display,
    filter_sessions_by_time,
)
from claude_code_tools.node_menu_ui import run_find_options_ui

try:
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def _string_or_empty(value: object) -> str:
    """Return string metadata values, normalizing malformed values to empty."""
    return value if isinstance(value, str) else ""


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
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type") == "session_meta":
                        payload = entry.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        git_info = payload.get("git", {})
                        if not isinstance(git_info, dict):
                            git_info = {}
                        return {
                            "id": _string_or_empty(payload.get("id")),
                            "cwd": _string_or_empty(payload.get("cwd")),
                            "branch": _string_or_empty(git_info.get("branch")),
                            "timestamp": _string_or_empty(
                                payload.get("timestamp")
                            ),
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
        msg_count = 0
        last_message = None  # Tuple of (type_prefix, content)
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            continue
                        # Only count and extract user/assistant messages
                        if entry.get("type") == "response_item":
                            payload = entry.get("payload")
                            if not isinstance(payload, dict):
                                continue
                            role = _string_or_empty(payload.get("role"))
                            if role in ("user", "assistant"):
                                msg_count += 1
                                content = payload.get("content")
                                if isinstance(content, list) and len(content) > 0:
                                    first_item = content[0]
                                    if isinstance(first_item, dict):
                                        text = _string_or_empty(
                                            first_item.get("text")
                                        )
                                        if text and not is_system_message(text):
                                            cleaned = (
                                                text[:400]
                                                .replace("\n", " ")
                                                .strip()
                                            )
                                            type_prefix = f"[{role}]"
                                            if len(cleaned) > 20:
                                                last_message = (type_prefix, cleaned)
                                            elif last_message is None:
                                                last_message = (type_prefix, cleaned)
                    except json.JSONDecodeError:
                        continue
            preview = f"{last_message[0]} {last_message[1]}" if last_message else None
            return True, msg_count, preview
        except (OSError, IOError, UnicodeError):
            return False, 0, None

    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    msg_count = 0
    last_message = None  # Tuple of (type_prefix, content)

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                # Search for keywords in all text content
                line_lower = line.lower()
                for kw in keywords_lower:
                    if kw in line_lower:
                        found_keywords.add(kw)

                try:
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        continue

                    # Only count and extract user/assistant messages
                    if entry.get("type") == "response_item":
                        payload = entry.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        role = _string_or_empty(payload.get("role"))
                        if role in ("user", "assistant"):
                            msg_count += 1
                            content = payload.get("content")
                            if isinstance(content, list) and len(content) > 0:
                                first_item = content[0]
                                if isinstance(first_item, dict):
                                    text = _string_or_empty(
                                        first_item.get("text")
                                    )
                                    if text and not is_system_message(text):
                                        # Keep updating with latest message
                                        cleaned = (
                                            text[:400]
                                            .replace("\n", " ")
                                            .strip()
                                        )
                                        type_prefix = f"[{role}]"
                                        # Only keep if it's substantial (>20 chars)
                                        if len(cleaned) > 20:
                                            last_message = (type_prefix, cleaned)
                                        elif last_message is None:
                                            # Keep even short messages if no better option
                                            last_message = (type_prefix, cleaned)

                except json.JSONDecodeError:
                    continue

        all_found = len(found_keywords) == len(keywords_lower)
        preview = f"{last_message[0]} {last_message[1]}" if last_message else None
        return all_found, msg_count, preview

    except (OSError, IOError, UnicodeError):
        return False, 0, None


def find_sessions(
    codex_home: Path,
    keywords: list[str],
    num_matches: Optional[int] = 10,
    global_search: bool = False,
    original_only: bool = False,
    no_sub: bool = False,
    no_trim: bool = False,
    no_cont: bool = False,
) -> list[dict]:
    """
    Find Codex sessions matching keywords.

    Args:
        codex_home: Path to Codex home directory
        keywords: List of keywords to search for
        num_matches: Maximum number of results to return, or None for all
            matching sessions.
        global_search: If False, filter to current directory only
        original_only: If True, show only original sessions (excludes trimmed and rollover)
        no_sub: If True, exclude sub-agent sessions (Note: Codex doesn't have sub-agents)
        no_trim: If True, exclude trimmed sessions
        no_cont: If True, exclude rollover sessions (internally "continued")

    Returns list of dicts with: session_id, project, branch, date,
                                 lines, preview, cwd, file_path, is_trimmed
    """
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return []

    # Get current directory for filtering (if not global search)
    current_cwd = os.getcwd() if not global_search else None

    matches = []

    # Rollouts normally use YYYY/MM/DD, but the on-disk contract permits any
    # directory depth beneath sessions.
    session_files: list[Path] = []
    try:
        session_files.extend(
            sessions_dir.rglob("rollout-*.jsonl")
        )
    except OSError:
        # Preserve paths yielded before an inaccessible directory interrupted
        # traversal. Per-file guards below isolate failures in those paths.
        pass
    session_files.sort(reverse=True)

    for session_file in session_files:
        try:
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

            # Check if session is trimmed/continued
            is_trimmed = is_trimmed_session(session_file)
            derivation_type = (
                get_session_derivation_type(session_file)
                if is_trimmed
                else None
            )

            # Apply filters (original_only overrides individual filters)
            # Note: Codex doesn't have sub-agent sessions, so no_sub has no
            # effect.
            if original_only:
                if is_trimmed:
                    continue
            else:
                if no_trim and derivation_type == "trimmed":
                    continue
                if no_cont and derivation_type == "continued":
                    continue

            # Get timestamps - prefer JSON metadata for create_time
            stat = session_file.stat()
            mod_time = stat.st_mtime
            if metadata.get("timestamp"):
                try:
                    ts = metadata["timestamp"]
                    # Parse ISO format (e.g., "2025-10-22T16:05:28.707Z")
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    create_time = datetime.fromisoformat(ts).timestamp()
                except (OSError, OverflowError, TypeError, ValueError):
                    create_time = getattr(
                        stat, "st_birthtime", stat.st_ctime
                    )
            else:
                create_time = getattr(stat, "st_birthtime", stat.st_ctime)

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
                    "create_time": create_time,  # For date range display
                    "lines": line_count,
                    "preview": preview or "No preview",
                    "cwd": metadata["cwd"],
                    "file_path": str(session_file),
                    "is_trimmed": is_trimmed,
                }
            )
        except (
            AttributeError,
            KeyError,
            OSError,
            OverflowError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            # One disappearing, inaccessible, or malformed rollout must not
            # prevent discovery of other sessions.
            continue

        # Early exit if we have enough matches
        if num_matches is not None and len(matches) >= num_matches * 3:
            break

    # Sort by modification time (newest first) and limit
    matches.sort(key=lambda x: x["mod_time"], reverse=True)
    return matches if num_matches is None else matches[:num_matches]


def display_interactive_ui(
    matches: list[dict],
    keywords: list[str] = None,
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
        table.add_column("Session ID", style="dim", no_wrap=True)
        table.add_column("Project", style="green")
        table.add_column("Branch", style="magenta")
        table.add_column("Date-Range", style="blue")
        table.add_column("Lines", justify="right")
        table.add_column("Last User Message", style="dim", max_width=60, overflow="fold")

        for i, match in enumerate(matches, 1):
            # Format session ID with annotations using centralized helper
            session_id_display = format_session_id_display(
                match["session_id"],
                is_trimmed=match.get("is_trimmed", False),
                truncate_length=16,
            )

            table.add_row(
                str(i),
                session_id_display,
                match["project"],
                match["branch"],
                match["date"],
                str(match["lines"]),
                match["preview"],  # No truncation, let Rich wrap it
            )

        console.print(table)

        # Show footnote if any sessions are trimmed
        has_trimmed = any(m.get("is_trimmed", False) for m in matches)
        if has_trimmed:
            console.print("[dim](t) = Trimmed session[/dim]")
    else:
        # Fallback to plain text
        print("\nMatching Codex Sessions:")
        print("-" * 80)
        for i, match in enumerate(matches, 1):
            # Format session ID with annotations using centralized helper
            session_id_display = format_session_id_display(
                match['session_id'],
                is_trimmed=match.get("is_trimmed", False),
                truncate_length=16,
            )

            print(f"{i}. {session_id_display}")
            print(f"   Project: {match['project']}")
            print(f"   Branch: {match['branch']}")
            print(f"   Date: {match['date']}")
            print(f"   Preview: {match['preview'][:60]}...")
            print()

        # Show footnote if any sessions are trimmed
        has_trimmed = any(m.get("is_trimmed", False) for m in matches)
        if has_trimmed:
            print("(t) = Trimmed session")

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


def append_to_codex_history(
    session_id: str, first_user_msg: str, codex_home: Path
) -> None:
    """
    Append session to Codex history.jsonl file.

    Args:
        session_id: Session UUID
        first_user_msg: First user message text
        codex_home: Codex home directory
    """
    history_file = codex_home / "history.jsonl"
    history_entry = {
        "session_id": session_id,
        "ts": int(time.time()),
        "text": first_user_msg[:500],  # Limit to 500 chars
    }

    with open(history_file, "a") as f:
        f.write(json.dumps(history_entry) + "\n")


def extract_first_user_message_codex(session_file: Path) -> str:
    """Extract first user message from Codex session file."""
    with open(session_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "response_item":
                payload = data.get("payload", {})
                if payload.get("type") == "message":
                    role = payload.get("role")
                    if role == "user":
                        return payload.get("text", "")

    return "Suppressed session"


def handle_suppress_resume_codex(
    match: dict,
    tools: Optional[str],
    threshold: int,
    trim_assistant_messages: Optional[int],
    codex_home: Path,
) -> str | None:
    """
    Suppress tool results and resume Codex session.

    Returns:
        'back' if user wants to go back to menu, None otherwise.
    """
    session_file = Path(match["file_path"])

    print(f"\n🔧 Trimming session...")

    # Parse tools into set if provided
    target_tools = None
    if tools:
        target_tools = {tool.strip().lower() for tool in tools.split(",")}

    try:
        # Use helper function to trim and create new session
        result = trim_and_create_session(
            "codex",
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
            original_session_id=match["session_id"],
        )
        if action == 'resume':
            resume_session(match["session_id"], match["cwd"])
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
        # Get first user message from original session
        first_msg = extract_first_user_message_codex(session_file)

        # Append to history
        append_to_codex_history(new_session_id, first_msg, codex_home)

        # Resume the new session
        resume_session(new_session_id, match["cwd"])
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


def handle_smart_trim_resume_codex(
    match: dict,
    codex_home: Path,
    custom_instructions: Optional[str] = None,
) -> str | None:
    """
    Smart trim session using parallel agents and resume Codex session.

    Args:
        match: Match dict with session info
        codex_home: Codex home directory
        custom_instructions: Optional custom instructions for the trim agents

    Returns:
        'back' if user wants to go back to menu, None otherwise.
    """
    import uuid
    from datetime import datetime

    session_file = Path(match["file_path"])

    # Determine which CLI to use: prefer Claude if available, else Codex
    if is_claude_cli_available():
        cli_type = "claude"
        print(f"\n🤖 Smart trimming session using Claude CLI...")
    else:
        cli_type = "codex"
        print(f"\n🤖 Smart trimming session using Codex CLI...")

    if custom_instructions:
        print(f"   Instructions: {custom_instructions[:60]}...")
    print(f"   This may take a minute as the agent analyzes the session...")

    try:
        # Identify trimmable lines using CLI
        trimmable = identify_trimmable_lines_cli(
            session_file,
            exclude_types=[],
            preserve_recent=10,
            custom_instructions=custom_instructions,
            cli_type=cli_type,
        )

        if not trimmable:
            # Show confirmation for "nothing to trim" case
            from claude_code_tools.node_menu_ui import run_trim_confirm_ui
            action = run_trim_confirm_ui(
                nothing_to_trim=True,
                original_session_id=match["session_id"],
            )

            if action == 'resume':
                resume_session(match["session_id"], match["cwd"])
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

        # Generate new session ID (UUID only) and filename with Codex format
        # New session goes in today's date folder (YYYY/MM/DD)
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H-%M-%S")
        date_path = now.strftime("%Y/%m/%d")
        new_session_id = str(uuid.uuid4())

        # Find sessions root by going up from input file (sessions/YYYY/MM/DD/file.jsonl)
        sessions_root = session_file.parent.parent.parent.parent
        output_dir = sessions_root / date_path
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"rollout-{timestamp}-{new_session_id}.jsonl"

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
            from claude_code_tools.node_menu_ui import run_trim_confirm_ui
            action = run_trim_confirm_ui(
                nothing_to_trim=True,
                original_session_id=match["session_id"],
            )
            if action == 'resume':
                resume_session(match["session_id"], match["cwd"])
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
            # Get first user message from original session
            first_msg = extract_first_user_message_codex(session_file)

            # Append to history
            append_to_codex_history(new_session_id, first_msg, codex_home)

            # Resume the new session
            resume_session(new_session_id, match["cwd"])
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


def handle_export_session(session_file_path: str, dest_override: str | None = None) -> None:
    """Export Codex session to text file."""
    from claude_code_tools.export_codex_session import export_session_to_markdown as do_export
    from datetime import datetime

    # Generate default export path using session's project directory
    session_file = Path(session_file_path)
    session_id = get_session_uuid(session_file.name)
    today = datetime.now().strftime("%Y%m%d")

    # Infer project directory from session metadata
    metadata = extract_session_metadata(session_file)
    if metadata and metadata.get("cwd"):
        output_dir = Path(metadata["cwd"]) / "exported-sessions"
    else:
        output_dir = Path.cwd() / "exported-sessions"

    default_path = output_dir / f"{today}-codex-session-{session_id}.txt"

    print(f"\nDefault export path: {default_path}")
    if dest_override is None:
        dest = input("Path (or Enter for default): ").strip()
        if not dest:
            dest_path = default_path
        else:
            dest_path = Path(dest).expanduser()
    else:
        dest_path = Path(dest_override).expanduser()

        # Force .txt extension
        if dest_path.suffix != ".txt":
            # Strip any existing extension and add .txt
            dest_path = dest_path.with_suffix(".txt")

    # Create parent directories if needed
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Export to text file
    try:
        with open(dest_path, 'w') as f:
            stats = do_export(Path(session_file_path), f, verbose=False)

        print(f"\n✅ Export complete!")
        print(f"   User messages: {stats['user_messages']}")
        print(f"   Assistant messages: {stats['assistant_messages']}")
        print(f"   Tool calls: {stats['tool_calls']}")
        print(f"   Tool results: {stats['tool_results']}")
        print(f"   Skipped items: {stats['skipped']}")
        print(f"\n📄 Exported to: {dest_path}")
    except Exception as e:
        print(f"\n❌ Error exporting session: {e}")


def show_action_menu(match: dict) -> Optional[str]:
    """
    Show action menu for selected session.

    Returns: action choice ('resume', 'path', 'copy', 'clone', 'export') or None if cancelled
    """
    return menu_show_action_menu(
        session_id=match['session_id'],
        agent="codex",
        project_name=match['project'],
        git_branch=match.get('branch'),
        is_sidechain=False,
        stderr_mode=False,
    )


def copy_session_file(file_path: str, dest_override: str | None = None, silent: bool = False) -> None:
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
        if not silent:
            print(f"\nCopied to: {dest_file}")

    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nError copying file: {e}")


def clone_session(
    file_path: str,
    session_id: str,
    cwd: str,
    shell_mode: bool = False,
    codex_home: Optional[str] = None
) -> None:
    """Clone a Codex session to a new file with new UUID and resume it."""
    import shutil
    import uuid
    import re

    source_path = Path(file_path)

    if not source_path.exists():
        print(f"\nError: Session file not found: {source_path}")
        return

    # Extract the timestamp part from filename
    # Format: rollout-YYYY-MM-DDTHH-MM-SS-<SESSION_ID>.jsonl
    filename = source_path.name
    match = re.match(r"(rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-)(.+)(\.jsonl)", filename)

    if not match:
        print(f"\nError: Invalid Codex session filename format: {filename}")
        return

    timestamp_prefix = match.group(1)  # rollout-YYYY-MM-DDTHH-MM-SS-
    suffix = match.group(3)  # .jsonl

    # Generate new UUID
    new_session_id = str(uuid.uuid4())

    # Create new filename with same timestamp but new UUID
    new_filename = f"{timestamp_prefix}{new_session_id}{suffix}"
    dest_path = source_path.parent / new_filename

    try:
        # Copy the file
        shutil.copy2(source_path, dest_path)

        # Update session ID in session_meta to match the new filename UUID
        from claude_code_tools.trim_session import update_session_id_in_file
        update_session_id_in_file(dest_path, new_session_id, agent="codex")

        if not shell_mode:
            print(f"\nCloned session:")
            print(f"  Original: {session_id}")
            print(f"  New:      {new_session_id}")
            print(f"\nResuming cloned session...")

        # Resume the new cloned session
        resume_session(new_session_id, cwd, shell_mode=shell_mode)

    except Exception as e:
        print(f"\nError cloning session: {e}")
        return


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


def create_action_handler(shell_mode: bool = False, codex_home: Optional[Path] = None, nonlaunch_flag: Optional[dict] = None):
    """Create an action handler for the TUI."""
    def action_handler(session, action: str, kwargs: Optional[dict] = None) -> str | None:
        """Handle actions from the TUI - session can be tuple or dict.

        Returns:
            'back' if the action wants to return to resume menu, None otherwise.
        """
        kwargs = kwargs or {}
        # Ensure session is a dict
        if not isinstance(session, dict):
            # Shouldn't happen in codex find, but handle gracefully
            session = {"session_id": str(session)}

        # Handle actions
        if action == "resume":
            resume_session(
                session["session_id"],
                session["cwd"],
                shell_mode
            )
        elif action == "suppress_resume":
            tools = kwargs.get("tools")
            threshold = kwargs.get("threshold")
            trim_assistant = kwargs.get("trim_assistant")
            if tools is None and threshold is None and trim_assistant is None:
                options = prompt_suppress_options()
                if not options:
                    return None
                tools, threshold, trim_assistant = options
            return handle_suppress_resume_codex(
                session, tools, threshold or 500, trim_assistant, codex_home
            )
        elif action == "smart_trim_resume":
            # Smart trim using parallel agents
            return handle_smart_trim_resume_codex(session, codex_home)
        elif action == "path":
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session.get("session_id")
        elif action == "copy":
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session.get("session_id")
        elif action == "clone":
            clone_session(
                session["file_path"],
                session["session_id"],
                session["cwd"],
                shell_mode=shell_mode
            )
        elif action == "export":
            handle_export_session(session["file_path"])
            if nonlaunch_flag is not None:
                nonlaunch_flag["done"] = True
                nonlaunch_flag["session_id"] = session.get("session_id")
        elif action == "continue":
            from claude_code_tools.session_utils import continue_with_options
            continue_with_options(
                session["file_path"],
                "codex",
                claude_home=None,
                codex_home=str(codex_home) if codex_home else None
            )

    return action_handler


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

To persist directory changes when resuming sessions:
    Add this to your shell config (.bashrc/.zshrc):
    fcs-codex() { eval $(find-codex-session --shell "$@"); }

    Then use: fcs-codex "keyword" -g
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
    parser.add_argument(
        "--original",
        action="store_true",
        help="Show only original sessions (excludes trimmed and continued sessions)",
    )
    parser.add_argument(
        "--no-sub",
        action="store_true",
        help="Exclude sub-agent sessions (Note: Codex doesn't have sub-agents)",
    )
    parser.add_argument(
        "--no-trim",
        action="store_true",
        help="Exclude trimmed sessions from results",
    )
    parser.add_argument(
        "--no-roll",
        dest="no_cont",  # Keep internal name for compatibility
        action="store_true",
        help="Exclude rollover sessions from results",
    )
    parser.add_argument(
        "--simple-ui",
        dest="simple_ui",
        action="store_true",
        help="Use simple Rich table UI instead of Node interactive UI",
    )
    parser.add_argument(
        "--min-lines",
        type=int,
        default=0,
        help="Only show sessions with at least this many lines (default: 0 = no minimum)",
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
            "global": args.global_search,
            "num_matches": args.num_matches,
            "original": args.original,
            "no_trim": args.no_trim,
            "no_cont": args.no_cont,
            "min_lines": args.min_lines or "",
            "before": args.before or "",
            "after": args.after or "",
        }
        opts = run_find_options_ui(initial_options, variant="find-codex")
        if opts is None:
            sys.exit(0)
        # Update args with user's selections
        args.keywords = opts.get("keywords") or ""
        args.global_search = opts.get("global", False)
        args.num_matches = opts.get("num_matches", 10)
        args.original = opts.get("original", False)
        args.no_trim = opts.get("no_trim", False)
        args.no_cont = opts.get("no_cont", False)
        args.min_lines = opts.get("min_lines") or 0
        args.before = opts.get("before")
        args.after = opts.get("after")

        # Build and display equivalent CLI command
        cmd_parts = ["aichat find-codex"]
        if args.keywords:
            cmd_parts.append(f'"{args.keywords}"')
        if args.global_search:
            cmd_parts.append("-g")
        if args.num_matches != 10:
            cmd_parts.append(f"-n {args.num_matches}")
        if args.original:
            cmd_parts.append("--original")
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

    # Get Codex home
    codex_home = get_codex_home(args.codex_home)
    if not codex_home.exists():
        print(f"Error: Codex home not found: {codex_home}", file=sys.stderr)
        sys.exit(1)

    # Display informational message about what session types are being shown
    if args.original:
        print("Showing: Original sessions only (excluding trimmed and rollover sessions)", file=sys.stderr)
    else:
        # Build list of excluded types (note: Codex doesn't have sub-agents)
        excluded_types = []
        if args.no_trim:
            excluded_types.append("trimmed")
        if args.no_cont:
            excluded_types.append("rollover")

        if excluded_types:
            excluded_str = ", ".join(excluded_types)
            print(f"Showing: All sessions except {excluded_str}", file=sys.stderr)
        else:
            print("Showing: All session types (original, trimmed, and rollover)", file=sys.stderr)
            print("Tip: Use --no-trim or --no-roll to exclude specific types", file=sys.stderr)
    print(file=sys.stderr)  # Blank line for readability

    # Find matching sessions
    matches = find_sessions(
        codex_home,
        keywords,
        args.num_matches,
        args.global_search,
        args.original,
        args.no_sub,
        args.no_trim,
        args.no_cont,
    )

    # Filter by minimum lines if specified
    if args.min_lines > 0:
        matches = [m for m in matches if m.get("lines", 0) >= args.min_lines]

    # Filter by time bounds if specified
    if args.before or args.after:
        matches = filter_sessions_by_time(
            matches, before=args.before, after=args.after
        )

    # Display UI and handle actions
    # ============================================================
    # Default: Node UI (rich interactive interface)
    # --simple-ui: Falls back to Rich table UI
    # ============================================================
    nonlaunch_flag = {"done": False}
    action_handler = create_action_handler(
        shell_mode=args.shell, codex_home=codex_home, nonlaunch_flag=nonlaunch_flag
    )
    rpc_path = str(Path(__file__).parent / "action_rpc.py")

    if not args.simple_ui:
        limited = [
            {
                "agent": "codex",
                "agent_display": "Codex",
                "session_id": m.get("session_id"),
                "mod_time": m.get("mod_time"),
                "create_time": m.get("create_time", m.get("mod_time")),
                "lines": m.get("lines"),
                "project": m.get("project"),
                "preview": m.get("preview"),
                "cwd": m.get("cwd"),
                "branch": m.get("branch", ""),
                "file_path": m.get("file_path"),
                "claude_home": None,
                "is_trimmed": m.get("is_trimmed", False),
                "derivation_type": m.get("derivation_type"),
                "is_sidechain": m.get("is_sidechain", False),
            }
            for m in matches
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
                rpc_path=rpc_path,
            )
            if nonlaunch_flag["done"]:
                choice = prompt_post_action()
                if choice == "back":
                    focus_id = nonlaunch_flag.get("session_id")
                    start_action = True
                    continue
            break
    else:
        # Fallback to Rich-based UI
        selected_match = display_interactive_ui(matches, keywords)
        if not selected_match:
            return

        # Show action menu
        action = show_action_menu(selected_match)
        if not action:
            return

        # Perform selected action using action handler
        action_handler(selected_match, action)


if __name__ == "__main__":
    main()
