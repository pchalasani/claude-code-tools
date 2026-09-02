"""Rich terminal rendering for the ``aichat port`` command."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.status import Status
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from claude_code_tools.port_service import (
        PortAmbiguityError,
        PortResult,
        ResolvedSession,
    )
_AGENT_LABELS = {"claude": "Claude", "codex": "Codex"}
_MACOS_CLIPBOARD_COMMANDS = (("pbcopy",),)
_LINUX_CLIPBOARD_COMMANDS = (
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
)
_POWERSHELL_CLIPBOARD_ARGS = (
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
)


def _clipboard_commands(
    platform_name: str,
) -> tuple[tuple[str, ...], ...]:
    """Return platform-specific clipboard commands in preference order."""
    if platform_name == "darwin":
        return _MACOS_CLIPBOARD_COMMANDS
    if platform_name == "win32":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        system32 = system_root / "System32"
        powershell = system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return (
            (str(system32 / "clip.exe"),),
            (
                str(powershell),
                *_POWERSHELL_CLIPBOARD_ARGS,
            ),
        )
    return _LINUX_CLIPBOARD_COMMANDS


def copy_to_clipboard(
    text: str,
    *,
    platform_name: str | None = None,
) -> bool:
    """Copy text with the first available working clipboard command.

    Windows commands use explicit system paths so the current directory is
    never searched for a clipboard executable.

    Args:
        text: Text to place on the clipboard.
        platform_name: Platform identifier. Defaults to ``sys.platform``.

    Returns:
        True when a clipboard command succeeds, otherwise False.
    """
    commands = _clipboard_commands(platform_name or sys.platform)
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return True
    return False


def _agent_label(agent: str) -> str:
    """Return the human-facing label for an agent identifier."""
    return _AGENT_LABELS.get(agent, agent.title())


def _target_agent(source_agent: str) -> str:
    """Return the destination agent identifier for a source agent."""
    return "codex" if source_agent == "claude" else "claude"


def _target_label(source_agent: str) -> str:
    """Return the product label for a destination agent."""
    target = _target_agent(source_agent)
    return "Claude Code" if target == "claude" else "Codex"


class PortDisplay:
    """Render progress, ambiguity choices, errors, and final results."""

    def __init__(self, query: str) -> None:
        """Create a display for one port query."""
        self.query = query
        self.console = Console()
        self.error_console = Console(stderr=True)

    def start(self) -> None:
        """Print the command header and requested session query."""
        content = Text()
        content.append("Query  ", style="dim")
        content.append(self.query, style="bold")
        self.console.print(
            Panel.fit(
                content,
                title="[bold cyan]Session port[/bold cyan]",
                border_style="cyan",
            )
        )

    def resolving(self) -> Status:
        """Start the visible resolution phase and return its spinner."""
        self.console.print("[bold cyan]1/3[/]  Resolving session")
        return self.console.status(
            "[dim]Searching Claude and Codex histories...[/dim]",
            spinner="dots",
        )

    def choose_candidate(self, error: "PortAmbiguityError") -> "ResolvedSession | None":
        """Present matching sessions and return the user's selection."""
        from claude_code_tools.port_service import ResolvedSession
        from claude_code_tools.session_selection import choose_session_record

        record = choose_session_record(
            error.query,
            error.records,
            error.match_count,
            prompt="Which source session do you want to port?",
            console=self.console,
        )
        if record is None:
            return None
        return ResolvedSession(
            agent=record.agent,
            session_file=Path(record.session_file),
        )

    def resolved(self, session: "ResolvedSession") -> None:
        """Print the chosen source and detected direction."""
        source = _agent_label(session.agent)
        target = _target_label(session.agent)
        self.console.print(
            Text.assemble(
                ("  ✓ ", "bold green"),
                (f"Detected source agent: {session.agent}", "bold"),
                f" — porting to {target}",
            )
        )
        self.console.print(
            Text.assemble(("    Source file  ", "dim"), str(session.session_file))
        )
        self.console.print(f"[bold cyan]2/3[/]  Porting {source} → {target}")

    def porting(self, source_agent: str) -> Status:
        """Return the conversion-phase spinner."""
        target = _target_label(source_agent)
        return self.console.status(
            f"[dim]Converting transcript and creating {target} session...[/dim]",
            spinner="dots",
        )

    def complete(self, result: "PortResult", source_agent: str) -> None:
        """Render the final destination details and resume command."""
        target_agent = _target_agent(source_agent)
        target = _agent_label(target_agent)
        self.console.print("[bold cyan]3/3[/]  Ready")

        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column()
        table.add_row(
            f"New {target} session id:",
            Text(result.new_session_id),
        )
        table.add_row("Session cwd:", Text(result.cwd or "Unknown"))
        table.add_row("Output file:", Text(str(result.output_file)))
        self.console.print(
            Panel(
                table,
                title="[bold green]Port complete[/bold green]",
                border_style="green",
            )
        )
        self.console.print("[bold]To resume:[/bold]")
        command = Text(
            f"  {result.resume_hint}",
            style="bold cyan",
            no_wrap=True,
            overflow="ignore",
        )
        self.console.print(command, soft_wrap=True)
        if target_agent == "codex":
            self.console.print()
            self.console.print(
                "[dim]Tip: Codex's /import is also available as an "
                "interactive alternative.[/dim]"
            )
        self.offer_session_id_copy(result.new_session_id, target_agent)

    def offer_session_id_copy(
        self,
        session_id: str,
        target_agent: str,
    ) -> None:
        """Offer to copy a newly created session ID in interactive terminals."""
        if not self.console.is_terminal or not sys.stdin.isatty():
            return
        self.console.print()
        try:
            should_copy = Confirm.ask(
                "[bold]Copy the new session ID to your clipboard?[/bold] "
                "[dim](Y/n)[/dim]",
                default=True,
                show_choices=False,
                show_default=False,
                console=self.console,
            )
        except (EOFError, KeyboardInterrupt):
            should_copy = False
        if not should_copy:
            self.console.print("[dim]Clipboard left unchanged.[/dim]")
            return
        if copy_to_clipboard(session_id):
            self.console.print(
                "[bold green]✓ Session ID copied to clipboard.[/bold green]"
            )
            command = "codex resume" if target_agent == "codex" else "claude --resume"
            self.console.print("[bold]Now you can run:[/bold]")
            self.console.print(
                Text(
                    f"  {command} <paste session ID>",
                    style="bold cyan",
                )
            )
            return
        self.console.print(
            "[yellow]Could not find a working system clipboard command. "
            "The session ID is still shown above.[/yellow]"
        )

    def error(self, message: str) -> None:
        """Render an expected failure without a traceback."""
        self.error_console.print(Text.assemble(("Error: ", "bold red"), message))

    def cancelled(self) -> None:
        """Render a user-requested cancellation."""
        self.console.print("[yellow]Port cancelled.[/yellow]")
