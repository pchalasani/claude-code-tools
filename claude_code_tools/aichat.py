#!/usr/bin/env python3
"""
aichat - Unified CLI for AI chat session management tools.

This provides a grouped command interface for managing Claude Code and
Codex sessions, following the pattern of tools like git, docker, etc.

All session-related tools are accessible as subcommands:
    aichat search          - Full-text search across all sessions
    aichat resume          - Resume a session (latest or by ID)
    aichat menu            - Interactive session menu
    aichat trim            - Trim session content
    ... and more

For backward compatibility, all flat commands (find-claude-session,
etc.) are still available.
"""

import click


class SessionIDGroup(click.Group):
    """Custom group that treats unknown commands as session IDs for menu."""

    def parse_args(self, ctx, args):
        # If the first arg looks like a session ID (not a known command), route to menu
        if args and args[0] not in self.commands and not args[0].startswith('-'):
            # Treat as session ID - prepend 'menu' to make it a menu command
            args = ['menu'] + args
        return super().parse_args(ctx, args)


@click.group(cls=SessionIDGroup, invoke_without_command=True)
@click.version_option()
@click.pass_context
def main(ctx):
    """
    Session management tools for Claude Code and Codex.

    This is the recommended interface for managing AI chat sessions.
    Each subcommand provides specialized functionality for finding,
    managing, and manipulating session files.

    For help on any subcommand, use:

    \b
        aichat COMMAND --help

    Examples:

    \b
        aichat                        # Action menu for latest session(s)
        aichat abc123-def456          # Shortcut for: aichat menu abc123-def456
        aichat search "langroid"      # Full-text search
        aichat resume                 # Resume latest session
        aichat resume abc123-def456   # Resume specific session
        aichat menu abc123-def456
        aichat trim session-id.jsonl
    """
    # Auto-index sessions on every aichat command (incremental, fast if up-to-date)
    # Skip for build-index/clear-index to avoid double-indexing or state conflicts
    # In JSON mode (-j/--json), suppress all output for clean parsing
    import sys
    skip_auto_index_cmds = ['build-index', 'clear-index', 'index-stats']
    should_skip = any(cmd in sys.argv for cmd in skip_auto_index_cmds)
    json_mode = any(arg in sys.argv for arg in ['-j', '--json'])
    if not should_skip:
        try:
            from claude_code_tools.search_index import auto_index
            from claude_code_tools.session_utils import get_claude_home, get_codex_home
            # Respect CLAUDE_CONFIG_DIR and CODEX_HOME environment variables
            auto_index(
                claude_home=get_claude_home(),
                codex_home=get_codex_home(),
                verbose=False,
                silent=json_mode,
            )
        except ImportError:
            pass  # tantivy not installed
        except Exception:
            pass  # Index errors shouldn't block commands

    if ctx.invoked_subcommand is None:
        # No subcommand - find latest sessions and show action menu
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='action',
            select_target='action',
            results_title=' Select a session ',
        )


# Shared help text for find commands
_FIND_OPTIONS_COMMON = """
Options:
  KEYWORDS              Comma-separated keywords to search (AND logic)
  -g, --global          Search across all projects
  -n, --num-matches N   Number of matches to display (default: 10)
  --original            Show only original sessions
  --no-sub              Exclude sub-agent sessions
  --no-trim             Exclude trimmed sessions
  --no-roll             Exclude rollover sessions
  --min-lines N         Only sessions with at least N lines
  --before TIMESTAMP    Sessions modified before (inclusive)
  --after TIMESTAMP     Sessions modified after (inclusive)
  --no-ui               Skip options menu, run search with CLI args directly
  --simple-ui           Use Rich table UI instead of Node UI"""

_FIND_OPTIONS_CODEX = _FIND_OPTIONS_COMMON.replace(
    "  --no-sub              Exclude sub-agent sessions\n", ""
)

_FIND_TIMESTAMP_HELP = """
Timestamp formats: YYYYMMDD, YYYY-MM-DD, MM/DD/YY, MM/DD/YYYY
                   Optional time: T16:45:23, T16:45, T16"""

_FIND_CTX_SETTINGS = {
    "ignore_unknown_options": True,
    "allow_extra_args": True,
    "allow_interspersed_args": False,
}


def _find_and_run_session_ui(
    session_id: str | None,
    agent_constraint: str,  # 'claude', 'codex', or 'both'
    start_screen: str,
    select_target: str | None = None,
    results_title: str | None = None,
    direct_action: str | None = None,
    action_kwargs: dict | None = None,
) -> None:
    """
    Find session(s) and run Node UI for the specified action.

    If session_id is provided, routes to session_menu_cli.
    Otherwise, finds latest sessions for current project/branch and shows UI.

    Args:
        session_id: Optional explicit session ID or path
        agent_constraint: 'claude', 'codex', or 'both'
        start_screen: Screen to show when single session found
        select_target: Screen to go to after selection (multiple sessions)
        results_title: Title for selection screen
        direct_action: If set, execute this action directly instead of showing UI
        action_kwargs: Optional kwargs to pass to action
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    from claude_code_tools.node_menu_ui import run_node_menu_ui
    from claude_code_tools.search_index import SessionIndex
    from claude_code_tools.session_menu_cli import execute_action
    from claude_code_tools.session_utils import default_export_path

    if session_id:
        # Route to session_menu_cli with appropriate start screen
        sys.argv = [
            sys.argv[0].replace('aichat', 'session-menu'),
            '--start-screen', start_screen,
            session_id,
        ]
        from claude_code_tools.session_menu_cli import main as menu_main
        menu_main()
        return

    # No session provided - find latest sessions for current project and branch
    current_cwd = os.getcwd()
    current_branch = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        )
        current_branch = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Use the Tantivy index for fast session lookup
    index_path = Path.home() / ".cctools" / "search-index"
    if not index_path.exists():
        print(
            "Session index not found. Run 'aichat search' first to build the index.",
            file=sys.stderr
        )
        sys.exit(1)

    try:
        idx = SessionIndex(index_path)
    except Exception as e:
        print(f"Failed to open session index: {e}", file=sys.stderr)
        sys.exit(1)

    candidates = []
    used_fallback = False  # Track if any agent fell back to any-branch

    def _get_latest_session(agent: str) -> tuple[dict | None, bool]:
        """Get latest session for agent, with fallback to any branch.

        Returns (session_dict, used_fallback).
        """
        # First try with branch filter
        if current_branch:
            session = idx.get_latest_session(
                cwd=current_cwd, branch=current_branch, agent=agent
            )
            if session:
                return session, False

        # Fallback: try without branch filter
        session = idx.get_latest_session(cwd=current_cwd, agent=agent)
        return session, bool(session)  # used_fallback=True only if we found one

    def _session_to_candidate(s: dict) -> dict:
        """Convert index session dict to UI candidate format."""
        export_path = s.get("export_path", "")
        session_file = Path(export_path) if export_path else None
        agent = s.get("agent", "claude")

        # Parse timestamps - index stores as ISO strings
        mod_time = 0
        create_time = 0
        if s.get("modified"):
            try:
                from datetime import datetime
                mod_time = datetime.fromisoformat(
                    s["modified"].replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                pass
        if s.get("created"):
            try:
                from datetime import datetime
                create_time = datetime.fromisoformat(
                    s["created"].replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                pass

        # Build preview from last message content (convention: show most recent)
        preview = ""
        if s.get("last_msg_content"):
            role = s.get("last_msg_role", "assistant")
            preview = f"[{role}] {s['last_msg_content'][:200]}"

        return {
            "agent": agent,
            "agent_display": "Claude" if agent == "claude" else "Codex",
            "session_id": s.get("session_id", ""),
            "mod_time": mod_time,
            "create_time": create_time,
            "lines": s.get("lines", 0),
            "project": s.get("project", ""),
            "preview": preview,
            "cwd": s.get("cwd", ""),
            "branch": s.get("branch", ""),
            "file_path": export_path,
            "default_export_path": str(
                default_export_path(session_file, agent)
            ) if session_file and session_file.exists() else "",
            "is_trimmed": s.get("derivation_type") == "trimmed",
            "derivation_type": s.get("derivation_type"),
            "is_sidechain": s.get("is_sidechain") == "true",
        }

    # Find Claude sessions if applicable
    if agent_constraint in ('claude', 'both'):
        session, fallback = _get_latest_session("claude")
        if session:
            candidates.append(_session_to_candidate(session))
            if fallback:
                used_fallback = True

    # Find Codex sessions if applicable
    if agent_constraint in ('codex', 'both'):
        session, fallback = _get_latest_session("codex")
        if session:
            candidates.append(_session_to_candidate(session))
            if fallback:
                used_fallback = True

    # Build the unified scope message
    project_name = Path(current_cwd).name
    agent_desc = {
        'claude': 'Claude',
        'codex': 'Codex',
        'both': 'Claude/Codex',
    }.get(agent_constraint, 'Claude/Codex')
    branch_part = f" on branch '{current_branch}'" if current_branch else ""
    fallback_note = " (fallback to any branch if none found)" if current_branch else ""
    scope_text = (
        f"Latest {agent_desc} sessions for '{project_name}'{branch_part}{fallback_note}"
    )

    if not candidates:
        print(
            f"No {agent_desc} sessions found for '{project_name}'"
            f"{branch_part} (even with fallback to any branch).",
            file=sys.stderr
        )
        sys.exit(1)

    # Handler for when user selects and acts - returns 'back' if user wants to
    # go back to menu
    def handler(sess, action, kwargs=None):
        session_file = Path(sess["file_path"])
        merged_kwargs = {**(action_kwargs or {}), **(kwargs or {})}
        return execute_action(
            action,
            sess["agent"],
            session_file,
            sess["cwd"],
            action_kwargs=merged_kwargs if merged_kwargs else None,
            session_id=sess.get("session_id"),
        )

    rpc_path = str(Path(__file__).parent / "action_rpc.py")

    # If direct_action specified, execute it immediately for single session
    if direct_action and len(candidates) == 1:
        handler(candidates[0], direct_action, action_kwargs)
        return

    # Create handler that may execute direct_action after selection
    def selection_handler(sess, action, kwargs=None):
        if direct_action:
            # Execute direct_action instead of whatever came from UI
            return handler(sess, direct_action, action_kwargs)
        else:
            return handler(sess, action, kwargs)

    if len(candidates) == 1:
        # Single session - go directly to start_screen
        run_node_menu_ui(
            candidates,
            [],
            selection_handler,
            start_screen=start_screen,
            scope_line=scope_text,
            rpc_path=rpc_path,
            direct_action=direct_action,
        )
    else:
        # Multiple sessions - show selection first
        run_node_menu_ui(
            candidates,
            [],
            selection_handler,
            start_screen="results",
            select_target=select_target or start_screen,
            results_title=results_title or f" Select session for {start_screen} ",
            start_zoomed=True,
            scope_line=scope_text,
            rpc_path=rpc_path,
            direct_action=direct_action,
        )


@main.command("find", context_settings=_FIND_CTX_SETTINGS, add_help_option=False, hidden=True)
@click.pass_context
def find(ctx):
    """Find sessions across all agents (Claude Code, Codex, etc.)."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'find-session')] + ctx.args
    from claude_code_tools.find_session import main as find_main
    find_main()


find.__doc__ = f"""Find sessions across all agents (Claude Code, Codex, etc.).
{_FIND_OPTIONS_COMMON}
  --agents AGENT [...]  Limit to one or more agents (e.g., --agents claude,
                        --agents claude codex). Default: all.
{_FIND_TIMESTAMP_HELP}

Examples:
  aichat find "langroid,MCP"
  aichat find -g --min-lines 50 --agents claude
  aichat find --after 11/20/25 --before 11/25/25
"""


@main.command("find-claude", context_settings=_FIND_CTX_SETTINGS, add_help_option=False, hidden=True)
@click.pass_context
def find_claude(ctx):
    """Find Claude Code sessions by keywords."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'find-claude-session')] + ctx.args
    from claude_code_tools.find_claude_session import main as find_claude_main
    find_claude_main()


find_claude.__doc__ = f"""Find Claude Code sessions by keywords.
{_FIND_OPTIONS_COMMON}
{_FIND_TIMESTAMP_HELP}

Examples:
  aichat find-claude "bug fix"
  aichat find-claude -g --min-lines 100
  aichat find-claude --after 2025-11-01
"""


@main.command("find-codex", context_settings=_FIND_CTX_SETTINGS, add_help_option=False, hidden=True)
@click.pass_context
def find_codex(ctx):
    """Find Codex sessions by keywords."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'find-codex-session')] + ctx.args
    from claude_code_tools.find_codex_session import main as find_codex_main
    find_codex_main()


find_codex.__doc__ = f"""Find Codex sessions by keywords.
{_FIND_OPTIONS_CODEX}
{_FIND_TIMESTAMP_HELP}

Examples:
  aichat find-codex "error"
  aichat find-codex -g --after 11/15/25
"""


@main.command("find-original", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def find_original(ctx):
    """Find the original session from a trimmed/continued session."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'find-original-session')] + ctx.args
    from claude_code_tools.find_original_session import (
        main as find_original_main,
    )
    find_original_main()


@main.command("find-derived", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def find_derived(ctx):
    """Find derived sessions (trimmed/continued) from an original."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'find-trimmed-sessions')] + ctx.args
    from claude_code_tools.find_trimmed_sessions import (
        main as find_derived_main,
    )
    find_derived_main()


@main.command("menu", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def menu(ctx):
    """Interactive menu for a specific session (by ID or path)."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'session-menu')] + ctx.args
    from claude_code_tools.session_menu_cli import main as menu_main
    menu_main()


@main.command("trim")
@click.argument("session", required=False)
@click.option("--tools", "-t",
              help="Comma-separated tools to trim (e.g., 'bash,read,edit'). "
                   "Default: all tools.")
@click.option("--len", "-l", "threshold", type=int, default=500,
              help="Minimum length threshold in chars for trimming (default: 500)")
@click.option("--trim-assistant", "-a", "trim_assistant", type=int,
              help="Trim assistant messages: positive N trims first N over threshold, "
                   "negative N trims all except last |N| over threshold")
@click.option("--output-dir", "-o",
              help="Output directory (default: same as input file)")
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
@click.option("--claude-home", help="Path to Claude home directory")
@click.option("--simple-ui", is_flag=True,
              help="Use simple CLI trim instead of Node UI (requires session ID)")
def trim(session, tools, threshold, trim_assistant, output_dir, agent, claude_home, simple_ui):
    """Trim session to reduce size by truncating large tool outputs.

    Trims tool call results and optionally assistant messages that exceed
    the length threshold. Creates a new trimmed session file with lineage
    metadata linking back to the original.

    If no session ID provided, finds latest session for current project/branch
    and opens the interactive trim menu.

    \b
    Examples:
        aichat trim                          # Interactive menu for latest session
        aichat trim abc123                   # Interactive menu for specific session
        aichat trim abc123 --simple-ui       # Direct CLI trim with defaults
        aichat trim abc123 -t bash,read -l 1000   # Trim specific tools, custom threshold
        aichat trim abc123 -a 5              # Also trim first 5 long assistant msgs

    \b
    Options:
        --tools, -t    Which tool outputs to trim (default: all)
        --len, -l      Character threshold for trimming (default: 500)
        --trim-assistant, -a   Trim assistant messages too
        --output-dir, -o       Where to write trimmed file
    """
    import sys

    if simple_ui or tools or trim_assistant or output_dir:
        # Direct CLI mode - need a session
        if not session:
            print("Error: Direct trim options require a session ID", file=sys.stderr)
            print("Use 'aichat trim' without options for interactive mode", file=sys.stderr)
            sys.exit(1)

        # Build args for trim-session CLI
        args = [session]
        if tools:
            args.extend(["--tools", tools])
        if threshold != 500:
            args.extend(["--len", str(threshold)])
        if trim_assistant is not None:
            args.extend(["--trim-assistant-messages", str(trim_assistant)])
        if output_dir:
            args.extend(["--output-dir", output_dir])
        if agent:
            args.extend(["--agent", agent])
        if claude_home:
            args.extend(["--claude-home", claude_home])

        sys.argv = [sys.argv[0].replace('aichat', 'trim-session')] + args
        from claude_code_tools.trim_session import main as trim_main
        trim_main()
        return

    _find_and_run_session_ui(
        session_id=session,
        agent_constraint='both',
        start_screen='trim_menu',
        select_target='trim_menu',
        results_title=' Which session to trim? ',
    )


@main.command("smart-trim")
@click.argument("session", required=False)
@click.option("--instructions", "-i",
              help="Custom instructions for what to prioritize when trimming")
@click.option("--exclude-types", "-e",
              help="Comma-separated message types to never trim (default: user)")
@click.option("--preserve-recent", "-p", type=int, default=10,
              help="Always preserve last N messages (default: 10)")
@click.option("--len", "-l", "content_threshold", type=int, default=200,
              help="Minimum chars for content extraction (default: 200)")
@click.option("--output-dir", "-o",
              help="Output directory (default: same as input)")
@click.option("--dry-run", "-n", is_flag=True,
              help="Show what would be trimmed without doing it")
@click.option("--claude-home", help="Path to Claude home directory")
def smart_trim(session, instructions, exclude_types, preserve_recent, content_threshold,
               output_dir, dry_run, claude_home):
    """Smart trim using Claude SDK agents (EXPERIMENTAL).

    Uses an LLM to intelligently analyze the session and decide what can
    be safely trimmed while preserving important context. Creates a new
    trimmed session with lineage metadata.

    If no session ID provided, finds latest session for current project/branch.

    \b
    Examples:
        aichat smart-trim                     # Interactive for latest session
        aichat smart-trim abc123              # Smart trim specific session
        aichat smart-trim abc123 --dry-run    # Preview what would be trimmed
        aichat smart-trim abc123 -p 20        # Preserve last 20 messages
        aichat smart-trim abc123 -e user,system  # Never trim user or system msgs
        aichat smart-trim abc123 -i "preserve auth-related messages"

    \b
    Options:
        --instructions, -i     Custom instructions for what to prioritize
        --exclude-types, -e    Message types to EXCLUDE from trimming
        --preserve-recent, -p  Keep last N messages untouched (default: 10)
        --len, -l              Min chars for content extraction (default: 200)
        --dry-run, -n          Preview only, don't actually trim
    """
    import sys
    from pathlib import Path

    from claude_code_tools.session_utils import find_session_file, detect_agent_from_path

    # If --instructions provided, use the handler function (same as TUI)
    if instructions and session:
        input_path = Path(session).expanduser()
        if input_path.exists() and input_path.is_file():
            session_file = input_path
            detected_agent = detect_agent_from_path(session_file)
            session_id = session_file.stem
            project_path = str(session_file.parent)
        else:
            result = find_session_file(session)
            if not result:
                print(f"Error: Session not found: {session}", file=sys.stderr)
                sys.exit(1)
            detected_agent, session_file, project_path, _ = result
            session_id = session_file.stem

        # Use the handler function which supports custom_instructions
        if detected_agent == "claude":
            from claude_code_tools.find_claude_session import handle_smart_trim_resume_claude
            handle_smart_trim_resume_claude(
                session_id, project_path, claude_home,
                custom_instructions=instructions,
            )
        else:
            from claude_code_tools.find_codex_session import handle_smart_trim_resume_codex
            from claude_code_tools.session_utils import get_codex_home
            handle_smart_trim_resume_codex(
                {"file_path": str(session_file), "cwd": project_path, "session_id": session_id},
                Path(get_codex_home(cli_arg=None)),
                custom_instructions=instructions,
            )
        return

    # If any direct CLI options specified (but not instructions), use smart_trim.py
    if exclude_types or preserve_recent != 10 or content_threshold != 200 or output_dir or dry_run:
        if not session:
            print("Error: Direct smart-trim options require a session ID", file=sys.stderr)
            print("Use 'aichat smart-trim' without options for interactive mode", file=sys.stderr)
            sys.exit(1)

        # Build args for smart-trim CLI
        args = [session]
        if exclude_types:
            args.extend(["--exclude-types", exclude_types])
        if preserve_recent != 10:
            args.extend(["--preserve-recent", str(preserve_recent)])
        if content_threshold != 200:
            args.extend(["--content-threshold", str(content_threshold)])
        if output_dir:
            args.extend(["--output-dir", output_dir])
        if dry_run:
            args.append("--dry-run")
        if claude_home:
            args.extend(["--claude-home", claude_home])

        sys.argv = [sys.argv[0].replace('aichat', 'smart-trim')] + args
        from claude_code_tools.smart_trim import main as smart_trim_main
        smart_trim_main()
        return

    # Show interactive UI for entering instructions
    # (whether session provided or not - find latest if not)
    _find_and_run_session_ui(
        session_id=session,
        agent_constraint='both',
        start_screen='smart_trim_form',
        select_target='smart_trim_form',
        results_title=' Which session to smart-trim? ',
    )


@main.command("export", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False), help="Force export with specific agent")
@click.argument("session", required=False)
@click.pass_context
def export_session(ctx, agent, session):
    """Export session to text/markdown format.

    If no session ID provided, finds latest session for current project/branch.
    Auto-detects session type and uses matching export command.
    Use --agent to override and force export with a specific agent.
    """
    import sys
    from pathlib import Path
    from claude_code_tools.session_utils import detect_agent_from_path, find_session_file

    if not session:
        # No session provided - find latest and export directly
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='action',
            results_title=' Which session to export? ',
            direct_action='export',
        )
        return

    # Session provided - existing behavior
    # Try to detect session type
    detected_agent = None
    session_file = None

    # First check if it's a file path
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = detect_agent_from_path(session_file)
    else:
        # Try to find by session ID
        result = find_session_file(session)
        if result:
            detected_agent, session_file, _, _ = result

    # Determine which agent to use
    if agent:
        # User explicitly specified agent
        export_agent = agent.lower()
        if detected_agent and detected_agent != export_agent:
            print(f"\nDetected {detected_agent.upper()} session")
            print(f"Exporting with {export_agent.upper()} (user specified)")
        else:
            print(f"\nExporting with {export_agent.upper()} (user specified)")
    elif detected_agent:
        # Use detected agent
        export_agent = detected_agent
        print(f"\nDetected {detected_agent.upper()} session")
        print(f"Exporting with {export_agent.upper()}")
    else:
        # Default to Claude if cannot detect
        export_agent = "claude"
        print(f"\nCould not detect session type, defaulting to CLAUDE")

    print()

    # Route to appropriate export command
    if export_agent == "claude":
        sys.argv = [sys.argv[0].replace('aichat', 'export-claude-session'), session] + ctx.args
        from claude_code_tools.export_claude_session import main as export_main
        export_main()
    else:
        sys.argv = [sys.argv[0].replace('aichat', 'export-codex-session'), session] + ctx.args
        from claude_code_tools.export_codex_session import main as export_main
        export_main()


@main.command("export-claude", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def export_claude(ctx):
    """Export Claude Code session to text/markdown format.

    If no session ID provided, finds latest Claude session for current
    project/branch.
    """
    import sys
    args = ctx.args
    session_id = args[0] if args and not args[0].startswith('-') else None

    if session_id:
        # Pass to existing export CLI
        sys.argv = [sys.argv[0].replace('aichat', 'export-claude-session')] + args
        from claude_code_tools.export_claude_session import (
            main as export_claude_main,
        )
        export_claude_main()
    else:
        # Find latest Claude session and export directly
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='claude',
            start_screen='action',
            results_title=' Which Claude session to export? ',
            direct_action='export',
        )


@main.command("export-codex", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def export_codex(ctx):
    """Export Codex session to text/markdown format.

    If no session ID provided, finds latest Codex session for current
    project/branch.
    """
    import sys
    args = ctx.args
    session_id = args[0] if args and not args[0].startswith('-') else None

    if session_id:
        # Pass to existing export CLI
        sys.argv = [sys.argv[0].replace('aichat', 'export-codex-session')] + args
        from claude_code_tools.export_codex_session import (
            main as export_codex_main,
        )
        export_codex_main()
    else:
        # Find latest Codex session and export directly
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='codex',
            start_screen='action',
            results_title=' Which Codex session to export? ',
            direct_action='export',
        )


@main.command("delete", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def delete(ctx):
    """Delete a session file with safety confirmation."""
    import sys
    sys.argv = [sys.argv[0].replace('aichat', 'delete-session')] + ctx.args
    from claude_code_tools.delete_session import main as delete_main
    delete_main()


@main.command("info")
@click.argument("session", required=False)
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def info(session, agent, json_output):
    """Show information about a session.

    Displays session metadata including file path, agent type, project,
    branch, timestamps, message counts, and lineage (parent sessions).

    If no session ID provided, shows info for latest session in current
    project/branch.

    \b
    Examples:
        aichat info                     # Info for latest session
        aichat info abc123-def456       # Info for specific session
        aichat info --json abc123       # Output as JSON
    """
    import json as json_lib
    import sys
    from pathlib import Path
    from datetime import datetime

    from claude_code_tools.session_utils import (
        find_session_file,
        detect_agent_from_path,
        extract_cwd_from_session,
        count_user_messages,
        default_export_path,
    )
    from claude_code_tools.session_lineage import get_continuation_lineage

    # Find session file
    if session:
        input_path = Path(session).expanduser()
        if input_path.exists() and input_path.is_file():
            session_file = input_path
            detected_agent = agent or detect_agent_from_path(session_file)
        else:
            result = find_session_file(session)
            if not result:
                print(f"Error: Session not found: {session}", file=sys.stderr)
                sys.exit(1)
            detected_agent, session_file, _, _ = result
            if agent:
                detected_agent = agent
    else:
        # No session provided - find latest
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='action',
            direct_action='path',  # Just show path for now
        )
        return

    # Gather session info
    session_id = session_file.stem
    mod_time = datetime.fromtimestamp(session_file.stat().st_mtime)
    create_time = datetime.fromtimestamp(session_file.stat().st_ctime)
    file_size = session_file.stat().st_size
    line_count = sum(1 for _ in open(session_file))

    # Extract metadata from session
    from claude_code_tools.export_session import extract_session_metadata
    metadata = extract_session_metadata(session_file, detected_agent)
    cwd = metadata.get("cwd") or extract_cwd_from_session(session_file)
    project = Path(cwd).name if cwd else "unknown"
    custom_title = metadata.get("customTitle", "")
    user_msg_count = count_user_messages(session_file, detected_agent)

    # Get lineage
    lineage = get_continuation_lineage(session_file, export_missing=False)
    lineage_info = []
    for node in lineage:
        lineage_info.append({
            "file": str(node.session_file),
            "type": node.derivation_type or "original",
        })

    info_data = {
        "session_id": session_id,
        "agent": detected_agent,
        "custom_title": custom_title,
        "file_path": str(session_file),
        "project": project,
        "cwd": cwd,
        "created": create_time.isoformat(),
        "modified": mod_time.isoformat(),
        "file_size_bytes": file_size,
        "line_count": line_count,
        "user_message_count": user_msg_count,
        "export_path": str(default_export_path(session_file, detected_agent)),
        "lineage": lineage_info,
    }

    if json_output:
        print(json_lib.dumps(info_data, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Session: {session_id}")
        if custom_title:
            print(f"Title:   {custom_title}")
        print(f"{'='*60}")
        print(f"Agent:      {detected_agent}")
        print(f"Project:    {project}")
        print(f"CWD:        {cwd}")
        print(f"File:       {session_file}")
        print(f"Created:    {create_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Modified:   {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Size:       {file_size:,} bytes")
        print(f"Lines:      {line_count:,}")
        print(f"User msgs:  {user_msg_count}")
        if lineage_info:
            print(f"\nLineage ({len(lineage_info)} sessions):")
            for i, node in enumerate(lineage_info):
                prefix = "  └─" if i == len(lineage_info) - 1 else "  ├─"
                fname = Path(node["file"]).name
                print(f"{prefix} {fname} ({node['type']})")


@main.command("copy")
@click.argument("session", required=False)
@click.option("--dest", "-d", help="Destination path (default: prompted)")
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
def copy_session(session, dest, agent):
    """Copy a session file to a new location.

    If no session ID provided, finds latest session for current project/branch.

    \b
    Examples:
        aichat copy abc123-def456              # Copy with prompted destination
        aichat copy abc123 -d ~/backups/       # Copy to specific directory
        aichat copy abc123 -d ./my-session.jsonl  # Copy with specific filename
    """
    import sys
    from pathlib import Path

    from claude_code_tools.session_utils import find_session_file, detect_agent_from_path

    if not session:
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='action',
            direct_action='copy',
        )
        return

    # Find session file
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = agent or detect_agent_from_path(session_file)
    else:
        result = find_session_file(session)
        if not result:
            print(f"Error: Session not found: {session}", file=sys.stderr)
            sys.exit(1)
        detected_agent, session_file, _, _ = result
        if agent:
            detected_agent = agent

    # Import agent-specific copy function
    if detected_agent == "claude":
        from claude_code_tools.find_claude_session import copy_session_file
    else:
        from claude_code_tools.find_codex_session import copy_session_file

    copy_session_file(str(session_file), dest)


@main.command("query")
@click.argument("session", required=False)
@click.argument("question", required=False)
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
def query_session(session, question, agent):
    """Query a session with a question using an AI agent.

    Exports the session and uses Claude to answer questions about its content.
    If no question provided, opens interactive query mode.

    \b
    Examples:
        aichat query abc123 "What was the main bug fixed?"
        aichat query abc123 "Summarize the changes made"
        aichat query                           # Interactive mode for latest session
    """
    import sys
    from pathlib import Path

    from claude_code_tools.session_utils import find_session_file, detect_agent_from_path

    if not session:
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='query',
            direct_action='query',
        )
        return

    # Find session file
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = agent or detect_agent_from_path(session_file)
    else:
        result = find_session_file(session)
        if not result:
            print(f"Error: Session not found: {session}", file=sys.stderr)
            sys.exit(1)
        detected_agent, session_file, cwd, _ = result
        if agent:
            detected_agent = agent

    # If no question, show interactive query UI
    if not question:
        from claude_code_tools.node_menu_ui import run_node_menu_ui
        from claude_code_tools.session_menu_cli import execute_action

        rpc_path = str(Path(__file__).parent / "action_rpc.py")
        session_data = {
            "session_id": session_file.stem,
            "agent": detected_agent,
            "file_path": str(session_file),
            "cwd": str(session_file.parent),
        }

        def handler(sess, action, kwargs=None):
            return execute_action(
                action, sess["agent"], Path(sess["file_path"]),
                sess["cwd"], action_kwargs=kwargs
            )

        run_node_menu_ui(
            [session_data], [], handler,
            start_screen="query",
            rpc_path=rpc_path,
        )
        return

    # Direct query with provided question
    from claude_code_tools.session_utils import default_export_path

    # Export session first
    if detected_agent == "claude":
        from claude_code_tools.find_claude_session import handle_export_session
    else:
        from claude_code_tools.find_codex_session import handle_export_session

    export_path = default_export_path(session_file, detected_agent)
    handle_export_session(str(session_file))

    # Query using Claude
    import subprocess
    prompt = f"Read the session transcript at {export_path} and answer: {question}"
    subprocess.run(["claude", "-p", prompt])


@main.command("clone")
@click.argument("session", required=False)
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
def clone_session_cmd(session, agent):
    """Clone a session and resume the clone.

    Creates a copy of the session with a new ID and resumes it,
    leaving the original session untouched.

    If no session ID provided, finds latest session for current project/branch.

    \b
    Examples:
        aichat clone abc123-def456    # Clone specific session
        aichat clone                  # Clone latest session
    """
    import sys
    from pathlib import Path

    from claude_code_tools.session_utils import find_session_file, detect_agent_from_path

    if not session:
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='resume',
            direct_action='clone',
        )
        return

    # Find session file
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = agent or detect_agent_from_path(session_file)
        cwd = str(session_file.parent)
    else:
        result = find_session_file(session)
        if not result:
            print(f"Error: Session not found: {session}", file=sys.stderr)
            sys.exit(1)
        detected_agent, session_file, cwd, _ = result
        if agent:
            detected_agent = agent

    session_id = session_file.stem

    # Execute clone
    if detected_agent == "claude":
        from claude_code_tools.find_claude_session import clone_session
        clone_session(session_id, cwd, shell_mode=False)
    else:
        from claude_code_tools.find_codex_session import clone_session
        clone_session(str(session_file), session_id, cwd, shell_mode=False)


@main.command("rollover")
@click.argument("session", required=False)
@click.option("--quick", is_flag=True,
              help="Quick rollover without context extraction (just preserve lineage)")
@click.option("--prompt", "-p", help="Custom prompt for context extraction")
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
def rollover(session, quick, prompt, agent):
    """Rollover: hand off work to a fresh session with preserved lineage.

    Creates a new session with a summary of the current work and links back
    to the parent session. The new session starts with full context available
    while the parent session is preserved intact.

    \b
    Options:
        --quick    Skip context extraction, just preserve lineage pointers
        --prompt   Custom instructions for what context to extract

    \b
    Examples:
        aichat rollover abc123              # Rollover with context extraction
        aichat rollover abc123 --quick      # Quick rollover (lineage only)
        aichat rollover abc123 -p "Focus on the auth changes"
        aichat rollover                     # Rollover latest session
    """
    import sys
    from pathlib import Path

    from claude_code_tools.session_utils import (
        find_session_file,
        detect_agent_from_path,
        continue_with_options,
    )

    if not session:
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='continue_form',
            direct_action='continue',
            action_kwargs={"rollover_type": "quick" if quick else "context"},
        )
        return

    # Find session file
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = agent or detect_agent_from_path(session_file)
    else:
        result = find_session_file(session)
        if not result:
            print(f"Error: Session not found: {session}", file=sys.stderr)
            sys.exit(1)
        detected_agent, session_file, _, _ = result
        if agent:
            detected_agent = agent

    # Execute rollover
    rollover_type = "quick" if quick else "context"
    continue_with_options(
        str(session_file),
        detected_agent,
        preset_prompt=prompt,
        rollover_type=rollover_type,
    )


@main.command("lineage")
@click.argument("session", required=False)
@click.option("--agent", type=click.Choice(["claude", "codex"], case_sensitive=False),
              help="Force agent type (auto-detected if not specified)")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def lineage(session, agent, json_output):
    """Show the parent lineage chain of a session.

    Traces back through continue_metadata and trim_metadata to find
    all ancestor sessions, from the current session back to the original.

    \b
    Examples:
        aichat lineage abc123-def456    # Show lineage for specific session
        aichat lineage                  # Show lineage for latest session
        aichat lineage abc123 --json    # Output as JSON
    """
    import json as json_lib
    import sys
    from pathlib import Path
    from datetime import datetime

    from claude_code_tools.session_utils import find_session_file, detect_agent_from_path
    from claude_code_tools.session_lineage import get_continuation_lineage

    if not session:
        _find_and_run_session_ui(
            session_id=None,
            agent_constraint='both',
            start_screen='lineage',
        )
        return

    # Find session file
    input_path = Path(session).expanduser()
    if input_path.exists() and input_path.is_file():
        session_file = input_path
        detected_agent = agent or detect_agent_from_path(session_file)
    else:
        result = find_session_file(session)
        if not result:
            print(f"Error: Session not found: {session}", file=sys.stderr)
            sys.exit(1)
        detected_agent, session_file, _, _ = result
        if agent:
            detected_agent = agent

    # Get lineage
    lineage_chain = get_continuation_lineage(session_file, export_missing=False)

    if not lineage_chain:
        print("No lineage found (this is an original session).")
        return

    lineage_data = []
    for node in lineage_chain:
        mod_time = datetime.fromtimestamp(node.session_file.stat().st_mtime)
        lineage_data.append({
            "session_id": node.session_file.stem,
            "file_path": str(node.session_file),
            "derivation_type": node.derivation_type or "original",
            "modified": mod_time.isoformat(),
            "exported_file": str(node.exported_file) if node.exported_file else None,
        })

    if json_output:
        print(json_lib.dumps(lineage_data, indent=2))
    else:
        print(f"\nLineage for: {session_file.stem}")
        print(f"{'='*60}")
        print(f"Chain has {len(lineage_data)} session(s):\n")

        for i, node in enumerate(lineage_data):
            # Current session or ancestor
            if i == 0:
                marker = "► "
            else:
                marker = "  "

            dtype = node["derivation_type"]
            fname = Path(node["file_path"]).name
            mod = node["modified"][:10]

            if i == len(lineage_data) - 1:
                prefix = f"{marker}└─"
            else:
                prefix = f"{marker}├─"

            print(f"{prefix} [{dtype:10}] {fname}  ({mod})")


@main.command("resume", context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "allow_interspersed_args": False})
@click.pass_context
def resume_session(ctx):
    """Resume a session with various options (resume, clone, trim, continue).

    If no session ID provided, finds latest session for current project/branch.
    Shows resume menu with options: resume as-is, clone, trim+resume,
    smart-trim, or continue with context.
    """
    args = ctx.args
    session_id = args[0] if args and not args[0].startswith('-') else None
    _find_and_run_session_ui(
        session_id=session_id,
        agent_constraint='both',
        start_screen='resume',
        select_target='resume',
        results_title=' Which session to resume? ',
    )


# =============================================================================
# Search Infrastructure Commands
# =============================================================================


@main.command("clear-index")
@click.option(
    "--index", "-i",
    type=click.Path(),
    default="~/.cctools/search-index",
    help="Index directory (default: ~/.cctools/search-index/)",
)
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be deleted")
def clear_index(index, dry_run):
    """Clear the Tantivy search index.

    Deletes the entire index directory to allow a fresh rebuild.
    """
    import shutil
    from pathlib import Path

    index_path = Path(index).expanduser()

    if not index_path.exists():
        print(f"Index directory does not exist: {index_path}")
        return

    # Count files for info
    file_count = sum(1 for _ in index_path.rglob("*") if _.is_file())

    if dry_run:
        print(f"Dry run: would delete index at {index_path}")
        print(f"   {file_count} files would be removed")
    else:
        shutil.rmtree(index_path)
        print(f"✅ Cleared index at {index_path}")
        print(f"   {file_count} files removed")


@main.command("clear-exports", hidden=True)
@click.option("--verbose", "-v", is_flag=True, help="Show what's being deleted")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be deleted")
def clear_exports(verbose, dry_run):
    """Clear all exported session files from project directories.

    Finds export files in per-project directories:
    {project}/exported-sessions/{agent}/*.txt

    Use this before re-exporting to ensure a clean slate.
    """
    from claude_code_tools.export_all import (
        collect_sessions_to_export,
        extract_export_dir_from_session,
    )

    print("Scanning for export files to delete...")
    sessions = collect_sessions_to_export()
    print(f"Found {len(sessions)} sessions to check")

    deleted_count = 0
    dirs_cleaned = set()

    with click.progressbar(
        sessions,
        label="Scanning",
        show_pos=True,
        item_show_func=lambda x: x[0].name if x else "",
    ) as bar:
        for session_file, agent in bar:
            export_dir = extract_export_dir_from_session(session_file, agent=agent)
            if export_dir and export_dir.exists():
                export_file = export_dir / f"{session_file.stem}.txt"
                if export_file.exists():
                    if dry_run:
                        if verbose:
                            print(f"\nWould delete: {export_file}")
                    else:
                        if verbose:
                            print(f"\nDeleting: {export_file}")
                        export_file.unlink()
                    deleted_count += 1
                    dirs_cleaned.add(export_dir)

    if dry_run:
        print(f"\nDry run: would delete {deleted_count} files "
              f"from {len(dirs_cleaned)} directories")
    else:
        print(f"\n✅ Cleared {deleted_count} export files "
              f"from {len(dirs_cleaned)} directories")


@main.command("export-all", hidden=True)
@click.option("--force", "-f", is_flag=True, help="Re-export all (ignore mtime)")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress and failures")
def export_all(force, verbose):
    """Export all sessions with YAML front matter for indexing.

    Each session is exported to its own project directory:
    {project}/exported-sessions/{agent}/{session_id}.txt

    Skips sessions that haven't changed since last export (unless --force).
    """
    from claude_code_tools.export_all import (
        collect_sessions_to_export,
        export_single_session,
    )

    print("Collecting sessions...")
    sessions = collect_sessions_to_export()
    print(f"Found {len(sessions)} sessions to process")

    stats: dict = {
        "exported": 0,
        "skipped": 0,
        "failed": 0,
        "exported_files": [],
        "failures": [],
    }

    with click.progressbar(
        sessions,
        label="Exporting",
        show_pos=True,
        item_show_func=lambda x: x[0].name if x else "",
    ) as bar:
        for session_file, agent in bar:
            result = export_single_session(session_file, agent, force)

            if result["status"] == "exported":
                stats["exported"] += 1
                stats["exported_files"].append(result["export_file"])
            elif result["status"] == "skipped":
                stats["skipped"] += 1
                if result["export_file"]:
                    stats["exported_files"].append(result["export_file"])
            else:  # failed
                stats["failed"] += 1
                stats["failures"].append({
                    "session": str(session_file),
                    "agent": agent,
                    "error": result["error"],
                })

    print(f"\n✅ Export complete:")
    print(f"   Exported: {stats['exported']}")
    print(f"   Skipped:  {stats['skipped']} (up-to-date)")
    print(f"   Failed:   {stats['failed']}")
    print(f"   Total export files: {len(stats['exported_files'])}")

    if stats["failures"] and verbose:
        print(f"\n⚠️  Failures ({len(stats['failures'])}):")
        for failure in stats["failures"]:
            print(f"   {failure['session']}")
            print(f"      Error: {failure['error']}")


@main.command("build-index")
@click.option(
    "--claude-home",
    type=click.Path(),
    default=None,
    help="Claude home directory (default: ~/.claude or CLAUDE_CONFIG_DIR)",
)
@click.option(
    "--codex-home",
    type=click.Path(),
    default=None,
    help="Codex home directory (default: ~/.codex or CODEX_HOME)",
)
def build_index(claude_home, codex_home):
    """Build/update the Tantivy search index from JSONL session files.

    Indexes sessions directly from JSONL files (no export step needed).
    Incremental by default - only indexes new/modified sessions.

    Use 'aichat clear-index' first if you want a full rebuild.
    """
    from claude_code_tools.search_index import auto_index
    from claude_code_tools.session_utils import get_claude_home, get_codex_home

    claude_home_path = get_claude_home(cli_arg=claude_home)
    codex_home_path = get_codex_home(cli_arg=codex_home)

    print("Building search index...")
    stats = auto_index(
        claude_home=claude_home_path,
        codex_home=codex_home_path,
        verbose=True,
        silent=False,  # Show tqdm progress bar
    )

    print(f"\n✅ Index build complete:")
    print(f"   Indexed: {stats['indexed']}")
    print(f"   Skipped: {stats['skipped']} (unchanged)")
    failed = stats['failed']
    if failed > 0:
        empty = stats.get('empty', 0)
        parse_err = stats.get('parse_error', 0)
        index_err = stats.get('index_error', 0)
        print(f"   Failed:  {failed} (empty: {empty}, parse: {parse_err}, "
              f"index: {index_err})")
    else:
        print(f"   Failed:  0")
    print(f"   Total:   {stats['total_files']} "
          f"(Claude: {stats.get('claude_files', '?')}, "
          f"Codex: {stats.get('codex_files', '?')})")


def _scan_session_files(
    claude_home: "Path",
    codex_home: "Path",
) -> dict:
    """
    Scan filesystem for all JSONL session files and read their metadata.

    Returns dict with:
        - files: dict mapping file_path (str) -> {path, is_subagent, agent, ...}
        - counts: {claude_resumable, claude_subagent, codex_resumable, codex_subagent}
        - errors: list of files that couldn't be parsed
    """
    import json
    from claude_code_tools.session_utils import is_valid_session

    files = {}
    counts = {
        "claude_resumable": 0,
        "claude_subagent": 0,
        "codex_resumable": 0,
        "codex_subagent": 0,
        "claude_invalid": 0,
        "codex_invalid": 0,
    }
    errors = []

    # Scan Claude sessions
    claude_projects = claude_home / "projects"
    if claude_projects.exists():
        for jsonl_path in claude_projects.glob("**/*.jsonl"):
            is_subagent = jsonl_path.name.startswith("agent-")
            try:
                with open(jsonl_path) as f:
                    first_line = f.readline().strip()
                if first_line:
                    data = json.loads(first_line)

                    # Skip sessions run from inside claude_home or codex_home
                    cwd = data.get("cwd", "")
                    claude_home_str = str(claude_home)
                    codex_home_str = str(codex_home)
                    if cwd and (
                        cwd.startswith(claude_home_str)
                        or cwd.startswith(codex_home_str)
                    ):
                        continue
                    # Use file path as key (unique per file)
                    file_key = str(jsonl_path)

                    # Check if actually resumable (has user/assistant messages)
                    if is_subagent:
                        is_resumable = True  # Subagents are always valid
                    else:
                        is_resumable = is_valid_session(jsonl_path)

                    if not is_resumable:
                        counts["claude_invalid"] += 1
                        continue  # Skip non-resumable files

                    files[file_key] = {
                        "path": jsonl_path,
                        "is_subagent": is_subagent,
                        "agent": "claude",
                        "session_id": data.get("sessionId", ""),
                    }
                    if is_subagent:
                        counts["claude_subagent"] += 1
                    else:
                        counts["claude_resumable"] += 1
            except Exception as e:
                errors.append({"path": jsonl_path, "error": str(e)})

    # Scan Codex sessions
    if codex_home.exists():
        for jsonl_path in codex_home.glob("**/*.jsonl"):
            is_subagent = jsonl_path.name.startswith("agent-")
            try:
                with open(jsonl_path) as f:
                    first_line = f.readline().strip()
                if first_line:
                    data = json.loads(first_line)

                    # Skip sessions run from inside claude_home or codex_home
                    cwd = data.get("cwd", "")
                    claude_home_str = str(claude_home)
                    codex_home_str = str(codex_home)
                    if cwd and (
                        cwd.startswith(claude_home_str)
                        or cwd.startswith(codex_home_str)
                    ):
                        continue
                    # Use file path as key (unique per file)
                    file_key = str(jsonl_path)

                    # Check if actually resumable (has user/assistant messages)
                    if is_subagent:
                        is_resumable = True  # Subagents are always valid
                    else:
                        is_resumable = is_valid_session(jsonl_path)

                    if not is_resumable:
                        counts["codex_invalid"] += 1
                        continue  # Skip non-resumable files

                    files[file_key] = {
                        "path": jsonl_path,
                        "is_subagent": is_subagent,
                        "agent": "codex",
                        "session_id": data.get("sessionId", "") or data.get("id", ""),
                    }
                    if is_subagent:
                        counts["codex_subagent"] += 1
                    else:
                        counts["codex_resumable"] += 1
            except Exception as e:
                errors.append({"path": jsonl_path, "error": str(e)})

    return {"files": files, "counts": counts, "errors": errors}


@main.command("index-stats")
@click.option(
    "--index", "-i",
    type=click.Path(),
    default="~/.cctools/search-index",
    help="Index directory",
)
@click.option("--cwd", "-c", help="Filter to specific cwd path")
@click.option(
    "--claude-home",
    type=click.Path(),
    default=None,
    help="Claude home directory (default: ~/.claude or CLAUDE_CONFIG_DIR)",
)
@click.option(
    "--codex-home",
    type=click.Path(),
    default=None,
    help="Codex home directory (default: ~/.codex or CODEX_HOME)",
)
def index_stats(index, cwd, claude_home, codex_home):
    """Show statistics about the Tantivy search index with reconciliation.

    Compares index contents against actual session files on disk to identify
    missing or orphaned sessions.
    """
    from collections import Counter
    from pathlib import Path

    try:
        from claude_code_tools.search_index import SessionIndex
    except ImportError:
        print("Error: tantivy not installed")
        return

    from claude_code_tools.session_utils import get_claude_home, get_codex_home

    index_path = Path(index).expanduser()
    claude_home_path = get_claude_home(cli_arg=claude_home)
    codex_home_path = get_codex_home(cli_arg=codex_home)

    if not index_path.exists():
        print(f"Index not found at {index_path}")
        return

    # === Filesystem scan ===
    print("Scanning filesystem...")
    fs_data = _scan_session_files(claude_home_path, codex_home_path)
    fs_files = fs_data["files"]
    fs_counts = fs_data["counts"]
    fs_errors = fs_data["errors"]

    print("\n=== Filesystem ===")
    print(f"Claude resumable: {fs_counts['claude_resumable']}")
    print(f"Claude subagent:  {fs_counts['claude_subagent']}")
    print(f"Codex resumable:  {fs_counts['codex_resumable']}")
    print(f"Codex subagent:   {fs_counts['codex_subagent']}")
    total_valid = (
        fs_counts['claude_resumable'] + fs_counts['claude_subagent'] +
        fs_counts['codex_resumable'] + fs_counts['codex_subagent']
    )
    total_invalid = fs_counts['claude_invalid'] + fs_counts['codex_invalid']
    print(f"Total valid:      {total_valid}")
    if total_invalid > 0:
        print(f"Skipped invalid:  {total_invalid} (no resumable messages)")

    if fs_errors:
        print(f"Parse errors:     {len(fs_errors)}")

    # === Index stats ===
    idx = SessionIndex(index_path)
    results = idx.get_recent(limit=100000)

    print("\n=== Index ===")
    print(f"Total documents: {len(results)}")

    # Build index lookup by export_path (unique key for each indexed file)
    # Filter to only entries matching the specified claude_home/codex_home
    indexed_by_path = {}
    claude_home_str = str(claude_home_path)
    codex_home_str = str(codex_home_path)

    for r in results:
        fpath = r.get("export_path", "")
        stored_home = r.get("claude_home", "")
        # Only include if home matches (claude or codex)
        if fpath and (stored_home == claude_home_str or stored_home == codex_home_str):
            indexed_by_path[fpath] = {
                "agent": r.get("agent", ""),
                "session_id": r.get("session_id", ""),
                "is_subagent": "agent-" in fpath,
            }

    # Also count unique session IDs for display (filtered)
    unique_session_ids = set(
        r.get("session_id", "") for r in results
        if r.get("session_id") and r.get("claude_home") in (claude_home_str, codex_home_str)
    )
    print(f"Unique session IDs: {len(unique_session_ids)} (for specified homes)")
    print(f"Total file paths:   {len(indexed_by_path)}")

    # === Reconciliation ===
    print("\n=== Reconciliation (by file path) ===")

    fs_paths = set(fs_files.keys())
    idx_paths = set(indexed_by_path.keys())

    missing_from_index = fs_paths - idx_paths
    orphaned_in_index = idx_paths - fs_paths
    in_sync = fs_paths & idx_paths

    print(f"Files on disk:  {len(fs_paths)}")
    print(f"Files indexed:  {len(idx_paths)}")
    print(f"In sync:        {len(in_sync)}")

    if not missing_from_index and not orphaned_in_index:
        print("✅ Index is fully in sync with filesystem")
    else:
        if missing_from_index:
            print(f"❌ Missing from index: {len(missing_from_index)}")
            # Count by type
            missing_subagent = sum(
                1 for p in missing_from_index if fs_files[p]["is_subagent"]
            )
            missing_resumable = len(missing_from_index) - missing_subagent
            print(f"   ({missing_resumable} resumable, {missing_subagent} subagent)")
            # Show a few examples
            for fpath in list(missing_from_index)[:3]:
                info = fs_files[fpath]
                stype = "subagent" if info["is_subagent"] else "resumable"
                fname = info["path"].name
                print(f"   - {fname} ({info['agent']}, {stype})")
            if len(missing_from_index) > 3:
                print(f"   ... and {len(missing_from_index) - 3} more")

        if orphaned_in_index:
            print(f"⚠️  Orphaned in index (file gone): {len(orphaned_in_index)}")
            # Count by type
            orphan_subagent = sum(
                1 for p in orphaned_in_index if indexed_by_path[p]["is_subagent"]
            )
            orphan_resumable = len(orphaned_in_index) - orphan_subagent
            print(f"   ({orphan_resumable} resumable, {orphan_subagent} subagent)")
            for fpath in list(orphaned_in_index)[:3]:
                info = indexed_by_path[fpath]
                fname = fpath.split("/")[-1]
                print(f"   - {fname} ({info['agent']})")
            if len(orphaned_in_index) > 3:
                print(f"   ... and {len(orphaned_in_index) - 3} more")

    # Count by cwd if filter specified
    if cwd:
        matching = [r for r in results if r.get("cwd") == cwd]
        matching_unique = set(r.get("session_id") for r in matching)
        print(f"\nWith cwd '{cwd}':")
        print(f"  Documents: {len(matching)}")
        print(f"  Unique sessions: {len(matching_unique)}")

    # Top cwds
    cwd_counts = Counter(r.get("cwd", "unknown") for r in results)
    print("\nTop 5 cwds:")
    for path, count in cwd_counts.most_common(5):
        short = path[-50:] if len(path) > 50 else path
        print(f"  {count:4d} | ...{short}" if len(path) > 50 else f"  {count:4d} | {short}")

    # Claude home stats
    claude_home_counts = Counter(r.get("claude_home", "") for r in results)
    print("\nClaude homes:")
    for home, count in claude_home_counts.most_common(10):
        home_display = home if home else "(empty)"
        print(f"  {count:4d} | {home_display}")


@main.command("search")
@click.option(
    '--claude-home',
    'claude_home_arg',
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help='Path to Claude home directory (overrides CLAUDE_CONFIG_DIR env var)',
)
@click.option(
    '--codex-home',
    'codex_home_arg',
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help='Path to Codex home directory (overrides CODEX_HOME env var)',
)
@click.option('-g', '--global', 'global_search', is_flag=True,
              help='Search across all projects (not just current)')
@click.option('--dir', 'filter_dir',
              help='Filter to directory[:branch] (overrides -g)')
@click.option('--branch', 'filter_branch',
              help='Filter to specific git branch (only effective when not global)')
@click.option('-n', '--num-results', type=int, default=None,
              help='Limit number of results displayed')
@click.option('--no-original', is_flag=True, help='Exclude original sessions')
@click.option('--sub-agent', is_flag=True, help='Include sub-agent sessions (additive)')
@click.option('--no-trimmed', is_flag=True, help='Exclude trimmed sessions')
@click.option('--no-rollover', is_flag=True, help='Exclude rollover sessions')
@click.option('--min-lines', type=int, default=None,
              help='Only show sessions with at least N lines')
@click.option('--after', metavar='DATE',
              help='Sessions modified after date (YYYYMMDD, MM/DD/YY)')
@click.option('--before', metavar='DATE',
              help='Sessions modified before date (YYYYMMDD, MM/DD/YY)')
@click.option('--agent', type=click.Choice(['claude', 'codex', 'all']),
              default='all', help='Filter by agent type')
@click.option('--json', 'json_output', is_flag=True,
              help='Output as JSONL for AI agents. Fields per line: session_id, '
                   'agent, project, branch, cwd, lines, created, modified, '
                   'first_msg, last_msg, file_path, derivation_type, '
                   'is_sidechain, snippet')
@click.option('--by-time', 'by_time', is_flag=True,
              help='Sort results by last-modified time (default: sort by relevance)')
@click.argument('query', required=False)
def search(
    claude_home_arg, codex_home_arg, global_search, filter_dir, filter_branch,
    num_results, no_original, sub_agent, no_trimmed, no_rollover, min_lines,
    after, before, agent, json_output, by_time, query
):
    """Launch interactive TUI for full-text session search.

    Provides fast Tantivy-based search across all Claude and Codex sessions
    with auto-indexing, keyword highlighting, and session actions.

    \b
    Examples:
        aichat search                      # Interactive TUI
        aichat search "langroid agent"     # Pre-fill search query
        aichat search -g --after 11/20/25  # Global, recent sessions
        aichat search --dir ~/Git/myproj   # Filter to specific directory
        aichat search --json "MCP"         # JSON output (sorted by relevance)
        aichat search --json --by-time     # JSON output sorted by time

    \b
    Notes:
        --dir overrides -g (global) when both are specified.
        --by-time sorts by last-modified time; default is relevance.

    \b
    Environment variables:
        CLAUDE_CONFIG_DIR  - Default Claude home (overridden by --claude-home)
        CODEX_HOME         - Default Codex home (overridden by --codex-home)
    """
    import json as json_lib
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    # Find Rust binary - check PATH first (cargo install), then local dev build
    import shutil
    rust_binary_name = "aichat-search"
    rust_binary = shutil.which(rust_binary_name)
    if rust_binary:
        rust_binary = Path(rust_binary)
    else:
        # Fall back to local development build
        rust_binary = (
            Path(__file__).parent.parent
            / "rust-search-ui"
            / "target"
            / "release"
            / rust_binary_name
        )
    if not rust_binary.exists():
        print(f"Error: {rust_binary_name} not found.", file=sys.stderr)
        print("Install with: cargo install aichat-search", file=sys.stderr)
        print("Or build locally: cd rust-search-ui && cargo build --release",
              file=sys.stderr)
        return

    # Resolve home directories (CLI arg > env var > default)
    from claude_code_tools.session_utils import get_claude_home, get_codex_home
    claude_home = get_claude_home(cli_arg=claude_home_arg)
    codex_home = get_codex_home(cli_arg=codex_home_arg)

    # Build CLI args for Rust binary
    rust_args = [str(rust_binary)]

    # Home directories
    rust_args.extend(["--claude-home", str(claude_home)])
    rust_args.extend(["--codex-home", str(codex_home)])

    # Filter options
    if filter_dir:
        # --dir overrides -g
        # Support dir:branch format - extract branch if present before resolving path
        if ':' in filter_dir and not filter_dir.startswith('/') or (
            ':' in filter_dir and filter_dir.count(':') == 1 and
            '/' not in filter_dir.split(':')[-1]
        ):
            # Has branch suffix (dir:branch format)
            parts = filter_dir.rsplit(':', 1)
            dir_part = parts[0]
            branch_part = parts[1] if len(parts) > 1 else None
            resolved_dir = str(Path(dir_part).resolve())
            if branch_part:
                rust_args.extend(["--dir", f"{resolved_dir}:{branch_part}"])
            else:
                rust_args.extend(["--dir", resolved_dir])
        else:
            rust_args.extend(["--dir", str(Path(filter_dir).resolve())])
    elif global_search:
        rust_args.append("--global")
    if filter_branch:
        rust_args.extend(["--branch", filter_branch])
    if num_results:
        rust_args.extend(["--num-results", str(num_results)])
    if no_original:
        rust_args.append("--no-original")
    if sub_agent:
        rust_args.append("--sub-agent")
    if no_trimmed:
        rust_args.append("--no-trimmed")
    if no_rollover:
        rust_args.append("--no-rollover")
    if min_lines:
        rust_args.extend(["--min-lines", str(min_lines)])
    if after:
        rust_args.extend(["--after", after])
    if before:
        rust_args.extend(["--before", before])
    if agent and agent != "all":
        rust_args.extend(["--agent", agent])
    if query:
        rust_args.extend(["--query", query])
    if by_time:
        rust_args.append("--by-time")

    # JSON output mode - run Rust with --json, output to stdout, exit
    if json_output:
        rust_args.append("--json")
        try:
            result = subprocess.run(rust_args, capture_output=True, text=True)
            # Output JSON to stdout (errors to stderr)
            # Use end='' to avoid double newline (Rust already adds one)
            if result.stdout:
                print(result.stdout, end='')
            if result.returncode != 0 and result.stderr:
                print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
        except Exception as e:
            print(f"Error running search: {e}", file=sys.stderr)
            sys.exit(1)

    # Import for interactive TUI mode
    from claude_code_tools.node_menu_ui import run_node_menu_ui, run_dir_confirm_ui
    from claude_code_tools.session_menu_cli import execute_action

    # RPC path for action execution
    rpc_path = str(Path(__file__).parent / "action_rpc.py")

    def action_handler(sess, action, kwargs):
        """Handle action from Node menu."""
        agent_type = sess.get("agent", "claude")
        file_path = sess.get("file_path")
        cwd = sess.get("cwd") or "."

        if not file_path:
            print(f"Error: No file_path for session {sess.get('session_id')}")
            return

        return execute_action(
            action=action,
            agent=agent_type,
            session_file=Path(file_path),
            project_path=cwd,
            action_kwargs=kwargs,
            session_id=sess.get("session_id"),
        )

    def check_directory_and_confirm(sess):
        """Check if session is from different directory and get user confirmation.

        Returns:
            (proceed, original_dir) tuple:
            - proceed: True if user wants to proceed, False if cancelled
            - original_dir: The directory before any change (to restore on cancel),
                           or None if no directory change was made
        """
        session_dir = sess.get("cwd") or "."
        current_dir = os.getcwd()

        # If same directory, proceed (no restore needed)
        if os.path.realpath(session_dir) == os.path.realpath(current_dir):
            return (True, None)

        # Show confirmation dialog
        choice = run_dir_confirm_ui(current_dir, session_dir)

        if choice == "yes":
            # Change directory and proceed
            original_dir = current_dir
            try:
                os.chdir(session_dir)
            except Exception as e:
                print(f"Error changing directory: {e}")
                original_dir = None  # No restore needed if change failed
            return (True, original_dir)
        elif choice == "no":
            # Proceed without changing directory (no restore needed)
            return (True, None)
        else:
            # 'cancel' or None - user wants to go back
            return (False, None)

    # Main loop: Rust TUI → Node menu → back to Rust TUI
    while True:
        # Create temp file for IPC
        fd, out_path = tempfile.mkstemp(suffix="-rust-ui.json")
        os.close(fd)

        # Run Rust TUI (interactive - needs TTY)
        tui_args = rust_args + [out_path]
        try:
            result = subprocess.run(tui_args)
        except Exception as e:
            print(f"Error running Rust TUI: {e}")
            try:
                os.unlink(out_path)
            except Exception:
                pass
            return

        if result.returncode != 0:
            try:
                os.unlink(out_path)
            except Exception:
                pass
            return

        # Read JSON from temp file
        try:
            content = Path(out_path).read_text().strip()
        except Exception:
            content = ""
        finally:
            try:
                os.unlink(out_path)
            except Exception:
                pass

        if not content:
            # User quit without selecting - exit the loop
            return

        try:
            result = json_lib.loads(content)
        except json_lib.JSONDecodeError as e:
            print(f"Error parsing Rust output: {e}")
            print(f"Output was: {content[:200]}")
            return

        # New format: {"session": {...}, "action": "...", "filter_state": {...}}
        # Legacy format: just the session object
        if "session" in result and "action" in result:
            selected = result["session"]
            action = result["action"]
        else:
            # Legacy: session only, show Node menu
            selected = result
            action = "menu"

        # Extract and apply filter state for next loop iteration
        filter_state = result.get("filter_state", {})
        if filter_state:
            # Rebuild rust_args with preserved filter state
            rust_args = [str(rust_binary)]
            rust_args.extend(["--claude-home", str(claude_home)])
            rust_args.extend(["--codex-home", str(codex_home)])

            # Scope: --dir overrides --global
            if filter_state.get("filter_dir"):
                rust_args.extend(["--dir", filter_state["filter_dir"]])
            elif filter_state.get("scope_global"):
                rust_args.append("--global")

            # Branch filter
            if filter_state.get("filter_branch"):
                rust_args.extend(["--branch", filter_state["filter_branch"]])

            # Session type filters
            # Subtractive: add --no-* when type is excluded
            # Additive: add --sub-agent when sub-agents are included
            if not filter_state.get("include_original", True):
                rust_args.append("--no-original")
            if filter_state.get("include_sub"):
                rust_args.append("--sub-agent")
            if not filter_state.get("include_trimmed", True):
                rust_args.append("--no-trimmed")
            if not filter_state.get("include_continued", True):
                rust_args.append("--no-rollover")

            # Other filters
            if filter_state.get("filter_min_lines"):
                rust_args.extend(["--min-lines", str(filter_state["filter_min_lines"])])
            if filter_state.get("filter_after_date"):
                rust_args.extend(["--after", filter_state["filter_after_date"]])
            if filter_state.get("filter_before_date"):
                rust_args.extend(["--before", filter_state["filter_before_date"]])
            if filter_state.get("filter_agent"):
                rust_args.extend(["--agent", filter_state["filter_agent"]])
            if filter_state.get("query"):
                rust_args.extend(["--query", filter_state["query"]])
            if filter_state.get("sort_by_time"):
                rust_args.append("--by-time")

            # Restore scroll/selection state
            if filter_state.get("selected") is not None:
                rust_args.extend(["--selected", str(filter_state["selected"])])
            if filter_state.get("list_scroll") is not None:
                rust_args.extend(["--scroll", str(filter_state["list_scroll"])])

            # Preserve num_results if originally specified
            if num_results:
                rust_args.extend(["--num-results", str(num_results)])

        # Convert ISO date strings from Rust to Unix timestamps
        def iso_to_timestamp(iso_str: str) -> float:
            """Convert ISO date string to Unix timestamp."""
            if not iso_str:
                return 0.0
            try:
                from datetime import datetime
                # Handle both formats: with and without 'Z' suffix
                iso_str = iso_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso_str)
                return dt.timestamp()
            except (ValueError, TypeError):
                return 0.0

        # Convert to session format expected by handlers
        # Rust sends 'created'/'modified' as ISO strings, Node UI expects
        # 'create_time'/'mod_time' as Unix timestamps
        session = {
            "session_id": selected.get("session_id", ""),
            "agent": selected.get("agent", "claude"),
            "agent_display": selected.get("agent", "claude").title(),
            "project": selected.get("project", ""),
            "branch": selected.get("branch", ""),
            "lines": selected.get("lines", 0),
            "file_path": selected.get("file_path", ""),
            "cwd": selected.get("cwd", ""),
            "is_sidechain": selected.get("is_sidechain", False),
            "create_time": iso_to_timestamp(selected.get("created", "")),
            "mod_time": iso_to_timestamp(selected.get("modified", "")),
        }

        # Extract cwd from file_path metadata if needed (for older export format)
        file_path = selected.get("file_path", "")
        if file_path and file_path.endswith(".txt"):
            try:
                with open(file_path, "r") as f:
                    file_content = f.read(2000)
                    if file_content.startswith("---\n"):
                        end_idx = file_content.find("\n---\n", 4)
                        if end_idx != -1:
                            import yaml
                            metadata = yaml.safe_load(file_content[4:end_idx])
                            if metadata:
                                session["cwd"] = metadata.get("cwd") or session["cwd"]
                                session["file_path"] = metadata.get("file_path", file_path)
            except Exception:
                pass

        print(f"Selected: {session['project']} / {session['session_id'][:12]}...")

        # Track original directory for restoration on Ctrl+C
        original_dir_for_interrupt = None

        try:
            # Dispatch based on action
            if action == "menu":
                # Legacy: show Node action menu, loop back on Escape
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=True,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                )
                # Loop back to Rust TUI
            elif action == "view":
                # View is handled in Rust, shouldn't reach here
                pass
            elif action in ("path", "copy", "export"):
                # Non-launch actions: go directly to nonlaunch screen
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=False,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                    start_screen="nonlaunch",
                    direct_action=action,
                )
                # Continue loop to return to Rust TUI
            elif action == "query":
                # Query: go directly to query screen
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=False,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                    start_screen="query",
                    direct_action="query",
                )
                # Continue loop to return to Rust TUI
            elif action == "suppress_resume":
                # Trim + resume: check directory first, then show trim form
                proceed, original_dir = check_directory_and_confirm(session)
                if not proceed:
                    continue  # User cancelled - pop back to Rust search
                original_dir_for_interrupt = original_dir
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=False,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                    start_screen="trim",
                    direct_action="suppress_resume",
                    exit_on_back=True,  # Pop back to Rust search on cancel
                )
                # If we return here, user cancelled - restore directory and pop back
                if original_dir:
                    os.chdir(original_dir)
            elif action == "smart_trim_resume":
                # Smart trim: check directory first, then show options form
                proceed, original_dir = check_directory_and_confirm(session)
                if not proceed:
                    continue  # User cancelled - pop back to Rust search
                original_dir_for_interrupt = original_dir
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=False,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                    start_screen="smart_trim_form",
                    direct_action="smart_trim_resume",
                    exit_on_back=True,  # Pop back to Rust search on cancel
                )
                # If we return here, user cancelled - restore directory and pop back
                if original_dir:
                    os.chdir(original_dir)
            elif action == "continue":
                # Continue with context: check directory first, then show options form
                proceed, original_dir = check_directory_and_confirm(session)
                if not proceed:
                    continue  # User cancelled - pop back to Rust search
                original_dir_for_interrupt = original_dir
                run_node_menu_ui(
                    sessions=[session],
                    keywords=[],
                    action_handler=action_handler,
                    start_action=False,
                    focus_session_id=session["session_id"],
                    rpc_path=rpc_path,
                    start_screen="continue_form",
                    direct_action="continue",
                    exit_on_back=True,  # Pop back to Rust search on cancel
                )
                # If we return here, user cancelled - restore directory and pop back
                if original_dir:
                    os.chdir(original_dir)
            elif action in ("resume", "clone"):
                # Resume/clone: check directory first, then execute
                proceed, original_dir = check_directory_and_confirm(session)
                if not proceed:
                    continue  # User cancelled - pop back to Rust search
                original_dir_for_interrupt = original_dir
                # Note: if successful, action_handler calls os.execvp and never returns
                action_handler(session, action, {})
                # If we get here, something failed - restore directory and pop back
                if original_dir:
                    os.chdir(original_dir)
            elif action == "delete":
                # Delete: already confirmed in Rust UI, just execute
                action_handler(session, action, {})
                # Remove deleted session from search index
                try:
                    from claude_code_tools.search_index import SessionIndex
                    idx = SessionIndex(Path("~/.cctools/search-index").expanduser())
                    idx.prune_deleted()
                except Exception:
                    pass  # Index errors shouldn't block the UI
                # Loop back to Rust TUI (session list will refresh)
            else:
                print(f"Unknown action: {action}")
                # Continue loop
        except KeyboardInterrupt:
            # Ctrl+C pressed - restore directory if changed and pop back to search
            if original_dir_for_interrupt:
                try:
                    os.chdir(original_dir_for_interrupt)
                except Exception:
                    pass
            # Continue loop to return to Rust TUI


if __name__ == "__main__":
    main()
