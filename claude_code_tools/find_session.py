#!/usr/bin/env python3
"""
Unified session finder - search across multiple coding agents (Claude Code, Codex, etc.)

Usage:
    find-session [keywords] [OPTIONS]
    fs [keywords] [OPTIONS]  # via shell wrapper

Examples:
    find-session "langroid,MCP"      # Search all agents in current project
    find-session -g                  # Show all sessions across all projects
    find-session "bug" --agents claude  # Search only Claude sessions
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Import search functions from existing tools
from claude_code_tools.find_claude_session import (
    find_sessions as find_claude_sessions,
    resume_session as resume_claude_session,
    get_session_file_path as get_claude_session_file_path,
    copy_session_file as copy_claude_session_file,
)
from claude_code_tools.find_codex_session import (
    find_sessions as find_codex_sessions,
    resume_session as resume_codex_session,
    get_codex_home,
    copy_session_file as copy_codex_session_file,
)

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


@dataclass
class AgentConfig:
    """Configuration for a coding agent."""

    name: str  # Internal name (e.g., "claude", "codex")
    display_name: str  # Display name (e.g., "Claude", "Codex")
    home_dir: Optional[str]  # Custom home directory (None = default)
    enabled: bool = True


def get_default_agents() -> List[AgentConfig]:
    """Get default agent configurations."""
    return [
        AgentConfig(name="claude", display_name="Claude", home_dir=None),
        AgentConfig(name="codex", display_name="Codex", home_dir=None),
    ]


def load_config() -> List[AgentConfig]:
    """Load agent configuration from config file or use defaults."""
    config_path = Path.home() / ".config" / "find-session" / "config.json"

    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                agents = []
                for agent_data in data.get("agents", []):
                    agents.append(
                        AgentConfig(
                            name=agent_data["name"],
                            display_name=agent_data.get(
                                "display_name", agent_data["name"].title()
                            ),
                            home_dir=agent_data.get("home_dir"),
                            enabled=agent_data.get("enabled", True),
                        )
                    )
                return agents
        except (json.JSONDecodeError, KeyError, IOError):
            pass

    # Return defaults if config doesn't exist or is invalid
    return get_default_agents()


def search_all_agents(
    keywords: List[str],
    global_search: bool = False,
    num_matches: int = 10,
    agents: Optional[List[str]] = None,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> List[dict]:
    """
    Search sessions across all enabled agents.

    Returns list of dicts with agent metadata added.
    """
    agent_configs = load_config()

    # Filter by requested agents if specified
    if agents:
        agent_configs = [a for a in agent_configs if a.name in agents]

    # Filter by enabled agents
    agent_configs = [a for a in agent_configs if a.enabled]

    all_sessions = []

    for agent_config in agent_configs:
        if agent_config.name == "claude":
            # Search Claude sessions
            home = claude_home or agent_config.home_dir
            sessions = find_claude_sessions(
                keywords, global_search=global_search, claude_home=home
            )

            # Add agent metadata to each session
            for session in sessions:
                session_dict = {
                    "agent": "claude",
                    "agent_display": agent_config.display_name,
                    "session_id": session[0],
                    "mod_time": session[1],
                    "create_time": session[2],
                    "lines": session[3],
                    "project": session[4],
                    "preview": session[5],
                    "cwd": session[6],
                    "branch": session[7] if len(session) > 7 else "",
                    "claude_home": home,
                }
                all_sessions.append(session_dict)

        elif agent_config.name == "codex":
            # Search Codex sessions
            home = codex_home or agent_config.home_dir
            codex_home_path = get_codex_home(home)

            if codex_home_path.exists():
                sessions = find_codex_sessions(
                    codex_home_path,
                    keywords,
                    num_matches=num_matches * 2,  # Get more for merging
                    global_search=global_search,
                )

                # Add agent metadata to each session
                for session in sessions:
                    session_dict = {
                        "agent": "codex",
                        "agent_display": agent_config.display_name,
                        "session_id": session["session_id"],
                        "mod_time": session["mod_time"],
                        "create_time": session.get("mod_time"),  # Codex doesn't separate these
                        "lines": session["lines"],
                        "project": session["project"],
                        "preview": session["preview"],
                        "cwd": session["cwd"],
                        "branch": session.get("branch", ""),
                        "file_path": session.get("file_path", ""),
                    }
                    all_sessions.append(session_dict)

    # Sort by modification time (newest first) and limit
    all_sessions.sort(key=lambda x: x["mod_time"], reverse=True)
    return all_sessions[:num_matches]


def display_interactive_ui(
    sessions: List[dict], keywords: List[str], stderr_mode: bool = False, num_matches: int = 10
) -> Optional[dict]:
    """Display unified session selection UI."""
    if not RICH_AVAILABLE:
        return None

    # Use stderr console if in stderr mode
    ui_console = Console(file=sys.stderr) if stderr_mode else Console()

    # Limit to specified number of sessions
    display_sessions = sessions[:num_matches]

    if not display_sessions:
        ui_console.print("[red]No sessions found[/red]")
        return None

    # Create table
    title = (
        f"Sessions matching: {', '.join(keywords)}" if keywords else "All sessions"
    )
    table = Table(
        title=title, box=box.ROUNDED, show_header=True, header_style="bold cyan"
    )

    table.add_column("#", style="bold yellow", width=3)
    table.add_column("Agent", style="magenta", width=6)
    table.add_column("Session ID", style="dim", width=10)
    table.add_column("Project", style="green")
    table.add_column("Branch", style="cyan")
    table.add_column("Date", style="blue")
    table.add_column("Lines", style="cyan", justify="right", width=6)
    table.add_column("Last User Message", style="white", max_width=50, overflow="fold")

    for idx, session in enumerate(display_sessions, 1):
        # Format date from mod_time
        from datetime import datetime

        mod_time = session["mod_time"]
        date_str = datetime.fromtimestamp(mod_time).strftime("%m/%d %H:%M")

        branch_display = session.get("branch", "") or "N/A"

        table.add_row(
            str(idx),
            session["agent_display"],
            session["session_id"][:8] + "...",
            session["project"],
            branch_display,
            date_str,
            str(session["lines"]),
            session["preview"],
        )

    ui_console.print(table)
    ui_console.print("\n[bold]Select a session:[/bold]")
    ui_console.print(f"  • Enter number (1-{len(display_sessions)}) to select")
    ui_console.print("  • Press Enter to cancel\n")

    while True:
        try:
            from rich.prompt import Prompt

            choice = Prompt.ask(
                "Your choice", default="", show_default=False, console=ui_console
            )

            # Handle empty input - cancel
            if not choice or not choice.strip():
                ui_console.print("[yellow]Cancelled[/yellow]")
                return None

            idx = int(choice) - 1
            if 0 <= idx < len(display_sessions):
                return display_sessions[idx]
            else:
                ui_console.print("[red]Invalid choice. Please try again.[/red]")

        except KeyboardInterrupt:
            ui_console.print("\n[yellow]Cancelled[/yellow]")
            return None
        except EOFError:
            ui_console.print("\n[yellow]Cancelled (EOF)[/yellow]")
            return None
        except ValueError:
            ui_console.print("[red]Invalid choice. Please try again.[/red]")


def show_action_menu(session: dict, stderr_mode: bool = False) -> Optional[str]:
    """Show action menu for selected session."""
    output = sys.stderr if stderr_mode else sys.stdout

    print(f"\n=== Session: {session['session_id'][:16]}... ===", file=output)
    print(f"Agent: {session['agent_display']}", file=output)
    print(f"Project: {session['project']}", file=output)
    if session.get("branch"):
        print(f"Branch: {session['branch']}", file=output)
    print("\nWhat would you like to do?", file=output)
    print("1. Resume session (default)", file=output)
    print("2. Show session file path", file=output)
    print("3. Copy session file to file (*.jsonl) or directory", file=output)
    print(file=output)

    try:
        if stderr_mode:
            # In stderr mode, prompt to stderr so it's visible
            sys.stderr.write("Enter choice [1-3] (or Enter for 1): ")
            sys.stderr.flush()
            choice = sys.stdin.readline().strip()
        else:
            choice = input("Enter choice [1-3] (or Enter for 1): ").strip()

        if not choice or choice == "1":
            return "resume"
        elif choice == "2":
            return "path"
        elif choice == "3":
            return "copy"
        else:
            print("Invalid choice.", file=output)
            return None
    except KeyboardInterrupt:
        print("\nCancelled.", file=output)
        return None


def handle_action(session: dict, action: str, shell_mode: bool = False) -> None:
    """Handle the selected action based on agent type."""
    agent = session["agent"]

    if action == "resume":
        if agent == "claude":
            resume_claude_session(
                session["session_id"],
                session["cwd"],
                shell_mode=shell_mode,
                claude_home=session.get("claude_home"),
            )
        elif agent == "codex":
            resume_codex_session(
                session["session_id"], session["cwd"], shell_mode=shell_mode
            )

    elif action == "path":
        if agent == "claude":
            file_path = get_claude_session_file_path(
                session["session_id"],
                session["cwd"],
                claude_home=session.get("claude_home"),
            )
            print("\nSession file path:")
            print(file_path)
        elif agent == "codex":
            print("\nSession file path:")
            print(session.get("file_path", "Unknown"))

    elif action == "copy":
        if agent == "claude":
            file_path = get_claude_session_file_path(
                session["session_id"],
                session["cwd"],
                claude_home=session.get("claude_home"),
            )
            copy_claude_session_file(file_path)
        elif agent == "codex":
            copy_codex_session_file(session.get("file_path", ""))


def main():
    parser = argparse.ArgumentParser(
        description="Unified session finder - search across multiple coding agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    find-session "langroid,MCP"           # Search all agents in current project
    find-session -g                       # Show all sessions across all projects
    find-session "bug" --agents claude    # Search only Claude sessions
    find-session "error" --agents codex   # Search only Codex sessions
        """,
    )
    parser.add_argument(
        "keywords",
        nargs="?",
        default="",
        help="Comma-separated keywords to search (AND logic). If omitted, shows all sessions.",
    )
    parser.add_argument(
        "-g",
        "--global",
        dest="global_search",
        action="store_true",
        help="Search across all projects, not just the current one",
    )
    parser.add_argument(
        "-n",
        "--num-matches",
        type=int,
        default=10,
        help="Number of matching sessions to display (default: 10)",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=["claude", "codex"],
        help="Limit search to specific agents (default: all)",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Output shell commands for evaluation (for use with shell function)",
    )
    parser.add_argument(
        "--claude-home", type=str, help="Path to Claude home directory (default: ~/.claude)"
    )
    parser.add_argument(
        "--codex-home", type=str, help="Path to Codex home directory (default: ~/.codex)"
    )

    args = parser.parse_args()

    # Parse keywords
    keywords = (
        [k.strip() for k in args.keywords.split(",") if k.strip()]
        if args.keywords
        else []
    )

    # Search all agents
    matching_sessions = search_all_agents(
        keywords,
        global_search=args.global_search,
        num_matches=args.num_matches,
        agents=args.agents,
        claude_home=args.claude_home,
        codex_home=args.codex_home,
    )

    if not matching_sessions:
        scope = "all projects" if args.global_search else "current project"
        keyword_msg = (
            f" containing all keywords: {', '.join(keywords)}" if keywords else ""
        )
        if RICH_AVAILABLE:
            console = Console()
            console.print(f"[yellow]No sessions found{keyword_msg} in {scope}[/yellow]")
        else:
            print(f"No sessions found{keyword_msg} in {scope}", file=sys.stderr)
        sys.exit(0)

    # Display interactive UI
    if RICH_AVAILABLE:
        selected_session = display_interactive_ui(
            matching_sessions, keywords, stderr_mode=args.shell, num_matches=args.num_matches
        )
        if selected_session:
            # Show action menu
            action = show_action_menu(selected_session, stderr_mode=args.shell)
            if action:
                handle_action(selected_session, action, shell_mode=args.shell)
    else:
        # Fallback without rich
        print("\nMatching sessions:")
        for idx, session in enumerate(matching_sessions[: args.num_matches], 1):
            print(
                f"{idx}. [{session['agent_display']}] {session['session_id'][:16]}... | "
                f"{session['project']} | {session.get('branch', 'N/A')}"
            )


if __name__ == "__main__":
    main()
