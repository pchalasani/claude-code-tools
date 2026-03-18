#!/usr/bin/env python3
"""
Interactive session menu CLI tool.

Usage:
    session-menu <session_id_or_path> [options]

This tool provides an interactive menu for managing Claude Code and
Codex sessions. It can accept either a session ID or a full file path,
auto-detecting the agent type.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from claude_code_tools.node_menu_ui import run_node_menu_ui

from claude_code_tools.session_menu import (
    show_action_menu,
    prompt_suppress_options,
)
from claude_code_tools.session_utils import (
    get_claude_home,
    get_codex_home,
    detect_agent_from_path,
    extract_cwd_from_session,
    extract_git_branch_claude,
    extract_session_metadata_codex,
    find_session_file,
    is_malformed_session,
    default_export_path,
    get_session_uuid,
    count_user_messages,
)
from claude_code_tools.find_session import extract_first_user_message
from claude_code_tools.find_claude_session import get_session_start_timestamp


def is_sidechain_session(session_file: Path) -> bool:
    """Check if session is a sidechain (sub-agent) session."""
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    # Check top-level isSidechain (sub-agent sessions)
                    if entry.get("isSidechain") is True:
                        return True
                    # Check inside file-history-snapshot metadata (legacy)
                    if entry.get("type") == "file-history-snapshot":
                        metadata = entry.get("metadata", {})
                        if metadata.get("is_sidechain", False):
                            return True
                except json.JSONDecodeError:
                    continue
    except (OSError, IOError):
        pass
    return False


def execute_action(
    action: str,
    agent: str,
    session_file: Path,
    project_path: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
    action_kwargs: Optional[dict] = None,
    session_id: Optional[str] = None,
) -> str | None:
    """
    Execute the selected action by delegating to appropriate tool.

    Args:
        action: The action to execute
        agent: 'claude' or 'codex'
        session_file: Path to session file
        project_path: Project directory path
        claude_home: Optional custom Claude home
        codex_home: Optional custom Codex home
        action_kwargs: Optional action-specific arguments
        session_id: Optional session ID (required for Codex, uses file stem for Claude)

    Returns:
        'back' if user wants to go back to menu, None otherwise.
    """
    # For Claude, session_id is the file stem; for Codex, extract UUID
    if session_id is None:
        session_id = get_session_uuid(session_file.stem)
    if action == "path":
        print(f"\nSession file path:")
        print(f"{session_file}")

    elif action == "copy":
        if agent == "claude":
            from claude_code_tools.find_claude_session import (
                copy_session_file,
            )
        else:
            from claude_code_tools.find_codex_session import (
                copy_session_file,
            )
        copy_session_file(str(session_file))

    elif action == "export":
        if agent == "claude":
            from claude_code_tools.find_claude_session import (
                handle_export_session,
            )
        else:
            from claude_code_tools.find_codex_session import (
                handle_export_session,
            )
        handle_export_session(str(session_file))

    elif action == "clone":
        if agent == "claude":
            from claude_code_tools.find_claude_session import clone_session
            clone_session(
                session_id, project_path, shell_mode=False, claude_home=claude_home
            )
        else:
            from claude_code_tools.find_codex_session import clone_session
            clone_session(
                str(session_file),
                session_id,
                project_path,
                shell_mode=False,
                codex_home=codex_home
            )

    elif action == "resume":
        if agent == "claude":
            from claude_code_tools.find_claude_session import resume_session
            resume_session(
                session_id, project_path, shell_mode=False, claude_home=claude_home
            )
        else:
            from claude_code_tools.find_codex_session import resume_session
            resume_session(session_id, project_path, shell_mode=False)

    elif action == "suppress_resume":
        action_kwargs = action_kwargs or {}
        tools = action_kwargs.get("tools")
        threshold = action_kwargs.get("threshold")
        trim_assistant = action_kwargs.get("trim_assistant")
        if tools is None and threshold is None and trim_assistant is None:
            result = prompt_suppress_options()
            if result is None:
                return
            tools, threshold, trim_assistant = result

        if agent == "claude":
            from claude_code_tools.find_claude_session import (
                handle_suppress_resume_claude,
            )
            result = handle_suppress_resume_claude(
                session_id,
                project_path,
                tools,
                threshold or 500,
                trim_assistant,
                claude_home,
            )
            return result
        else:
            from claude_code_tools.find_codex_session import (
                handle_suppress_resume_codex,
            )
            result = handle_suppress_resume_codex(
                {"file_path": str(session_file), "cwd": project_path,
                 "session_id": session_id},
                tools,
                threshold or 500,
                trim_assistant,
                Path(codex_home) if codex_home else Path.home() / ".codex",
            )
            return result

    elif action == "smart_trim_resume":
        # Get custom instructions from kwargs (from Node UI form)
        custom_instructions = action_kwargs.get("prompt") if action_kwargs else None
        if agent == "claude":
            from claude_code_tools.find_claude_session import (
                handle_smart_trim_resume_claude,
            )
            result = handle_smart_trim_resume_claude(
                session_id, project_path, claude_home,
                custom_instructions=custom_instructions,
            )
            return result
        else:
            from claude_code_tools.find_codex_session import (
                handle_smart_trim_resume_codex,
            )
            result = handle_smart_trim_resume_codex(
                {"file_path": str(session_file), "cwd": project_path,
                 "session_id": session_id},
                Path(codex_home) if codex_home else Path.home() / ".codex",
                custom_instructions=custom_instructions,
            )
            return result

    elif action == "continue":
        # Continue with context in fresh session
        from claude_code_tools.session_utils import continue_with_options
        preset_agent = action_kwargs.get("agent") if action_kwargs else None
        preset_prompt = action_kwargs.get("prompt") if action_kwargs else None
        rollover_type = action_kwargs.get("rollover_type", "context") if action_kwargs else "context"
        continue_with_options(
            str(session_file),
            agent,
            claude_home=claude_home,
            codex_home=codex_home,
            preset_agent=preset_agent,
            preset_prompt=preset_prompt,
            rollover_type=rollover_type,
        )

    elif action == "delete":
        # Delete the session file
        try:
            session_file.unlink()
            print(f"\n✅ Session deleted: {session_id}")
        except Exception as e:
            print(f"\n❌ Error deleting session: {e}", file=sys.stderr)


def main():
    """Main entry point for session-menu CLI."""
    parser = argparse.ArgumentParser(
        description="Interactive menu for Claude/Codex sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  session-menu abc123def456                    # By session ID
  session-menu ~/.claude/projects/foo/session.jsonl  # By file path
  session-menu abc123 --agent claude           # Force agent type
        """,
    )
    parser.add_argument(
        "session_id_or_path",
        help="Session ID or full path to session file",
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "codex"],
        help="Force agent type (auto-detected if not specified)",
    )
    parser.add_argument(
        "--claude-home",
        help="Custom Claude home directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--codex-home",
        help="Custom Codex home directory (default: ~/.codex)",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Shell mode for persistent directory changes",
    )
    parser.add_argument(
        "--simple-ui",
        dest="simple_ui",
        action="store_true",
        help="Use simple Rich menu instead of Node interactive UI",
    )
    parser.add_argument(
        "--start-screen",
        dest="start_screen",
        help="Start at specific Node UI screen (e.g., lineage, trim_menu)",
    )

    args = parser.parse_args()

    # Determine if input is a file path or session ID
    input_path = Path(args.session_id_or_path).expanduser()

    if input_path.exists() and input_path.is_file():
        # Input is a file path
        agent = args.agent or detect_agent_from_path(input_path)
        if not agent:
            print(
                "Error: Could not auto-detect agent type. "
                "Use --agent to specify.",
                file=sys.stderr,
            )
            sys.exit(1)

        session_file = input_path
        session_id = get_session_uuid(session_file.stem)

        # Extract project info based on agent type
        if agent == "claude":
            git_branch = extract_git_branch_claude(session_file)
            # Extract actual cwd from session file
            actual_cwd = extract_cwd_from_session(session_file)
            if actual_cwd:
                project_path = actual_cwd
                project_name = Path(actual_cwd).name
            else:
                # Cannot extract cwd - this shouldn't happen for valid Claude sessions
                print(
                    "Error: Could not extract working directory from "
                    f"session file: {session_file}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            metadata = extract_session_metadata_codex(session_file)
            if metadata:
                project_path = metadata.get("cwd", "")
                project_name = Path(project_path).name or "unknown"
                git_branch = metadata.get("branch")
            else:
                print(
                    "Error: Could not extract metadata from session file.",
                    file=sys.stderr,
                )
                sys.exit(1)

    else:
        # Input is a session ID - search for it
        session_id = args.session_id_or_path
        result = find_session_file(
            session_id, args.claude_home, args.codex_home
        )

        if not result:
            print(
                f"Error: Session '{session_id}' not found in Claude or "
                f"Codex homes.",
                file=sys.stderr,
            )
            sys.exit(1)

        agent, session_file, project_path, git_branch = result

        # Update session_id to the actual UUID from the found file
        session_id = get_session_uuid(session_file.stem)

        # Override agent if specified
        if args.agent and args.agent != agent:
            print(
                f"Warning: Found session in {agent} but --agent {args.agent} "
                f"specified. Using {agent}.",
                file=sys.stderr,
            )

    # Derive project name from project path
    project_name = Path(project_path).name if project_path else "unknown"

    # Check if sidechain
    is_sidechain = is_sidechain_session(session_file)

    if args.simple_ui:
        # Simple Rich menu UI
        action = show_action_menu(
            session_id=session_id,
            agent=agent,
            project_name=project_name,
            git_branch=git_branch,
            is_sidechain=is_sidechain,
            stderr_mode=args.shell,
        )

        if action:
            execute_action(
                action,
                agent,
                session_file,
                project_path,
                args.claude_home,
                args.codex_home,
                session_id=session_id,
            )
    else:
        # Default: Node interactive UI
        # Count user messages (not total JSONL lines)
        line_count = count_user_messages(session_file, agent)

        # Extract preview (last user message), clean for single-line display
        preview = ""
        try:
            raw_preview = extract_first_user_message(session_file, agent, last=True)
            # Replace newlines with spaces for single-line display
            preview = raw_preview.replace('\n', ' ').strip() if raw_preview else ""
        except Exception:
            pass

        # Get session start timestamp from JSON metadata, fall back to file stats
        stat = session_file.stat()
        create_time = get_session_start_timestamp(session_file)
        if create_time is None:
            create_time = getattr(stat, 'st_birthtime', stat.st_ctime)

        # Get display session ID (UUID only, handles both Claude and Codex)
        display_session_id = get_session_uuid(session_id)

        session_dict = {
            "agent": agent,
            "agent_display": agent.title(),
            "session_id": display_session_id,
            "mod_time": stat.st_mtime,
            "create_time": create_time,
            "lines": line_count,
            "project": project_name,
            "preview": preview,
            "cwd": project_path,
            "branch": git_branch or "",
            "file_path": str(session_file),
            "default_export_path": str(default_export_path(session_file, agent)),
            "claude_home": args.claude_home,
            "is_trimmed": False,
            "derivation_type": None,
            "is_sidechain": is_sidechain,
        }

        def handler(session_dict_in, action, kwargs=None):
            return execute_action(
                action,
                agent,
                session_file,
                project_path,
                args.claude_home,
                args.codex_home,
                action_kwargs=kwargs,
                session_id=session_id,
            )

        rpc_path = str(Path(__file__).parent / "action_rpc.py")
        run_node_menu_ui(
            [session_dict], [session_id], handler,
            stderr_mode=args.shell,
            start_screen=args.start_screen,
            rpc_path=rpc_path
        )


if __name__ == "__main__":
    main()
