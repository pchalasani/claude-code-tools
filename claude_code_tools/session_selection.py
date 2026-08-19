"""Reusable Rich selection for ambiguous session queries."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from claude_code_tools.resolve_session import SessionRecord

_AGENT_LABELS = {"claude": "Claude", "codex": "Codex"}
_MAX_TITLE_CHARS = 80


def stdin_is_interactive() -> bool:
    """Return whether stdin can safely answer an interactive picker."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, OSError):
        return False


def match_reason(record: "SessionRecord", query: str) -> str:
    """Explain why one resolver candidate matched the query.

    Args:
        record: Candidate returned by the shared resolver.
        query: Original user-supplied query.

    Returns:
        A concise human-readable match explanation.
    """
    matched_by = record.matched_by
    if matched_by == "id":
        return "exact session ID"
    if matched_by == "partial-id":
        return "session ID prefix"
    if matched_by == "id-substring":
        return "session ID contains query"
    if matched_by == "filename":
        return "filename contains query"
    if matched_by == "name":
        name = record.name or ""
        if name.casefold() == query.casefold():
            return "exact name/title"
        return "name/title contains query"
    return "resolver match"


def _clean_title(title: str | None) -> str:
    """Normalize and bound a candidate title for tabular display."""
    cleaned = " ".join((title or "Untitled session").split())
    if len(cleaned) > _MAX_TITLE_CHARS:
        return cleaned[: _MAX_TITLE_CHARS - 3] + "..."
    return cleaned


def _candidate_details(record: "SessionRecord", *, show_home: bool) -> Text:
    """Build a readable multi-line candidate summary."""
    details = Text()
    details.append(_clean_title(record.name), style="bold")
    details.append("\nProject / cwd  ", style="dim")
    details.append(record.directory or "Unknown")
    details.append("\nSession ID     ", style="dim")
    details.append(record.session_id)
    details.append("\nModified       ", style="dim")
    details.append(record.modified)
    if show_home:
        details.append("\nSource home    ", style="dim")
        details.append(record.home)
    return details


def choose_session_record(
    query: str,
    records: Sequence["SessionRecord"],
    match_count: int,
    *,
    prompt: str = "Which session do you want to use?",
    show_home: bool = False,
    console: Console | None = None,
) -> "SessionRecord | None":
    """Render ambiguous candidates and ask the user to select one.

    Args:
        query: Original user-supplied query.
        records: Newest-first selectable resolver records.
        match_count: Total number of matches before display capping.
        prompt: Question shown beneath the table.
        show_home: Whether to include the source account/home column.
        console: Optional Rich console, primarily for embedding and tests.

    Returns:
        The selected record, or None when the user chooses ``q``.

    Raises:
        EOFError: If stdin is not interactive.
    """
    if not stdin_is_interactive():
        raise EOFError

    output = console or Console()
    heading = Text()
    heading.append(f"{match_count} sessions matched ", style="yellow")
    heading.append(repr(query), style="bold yellow")
    heading.append(". Choose one to continue.", style="yellow")
    output.print(heading)

    table = Table(
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("Why it matched")
    table.add_column("Session")

    for index, record in enumerate(records, start=1):
        table.add_row(
            str(index),
            _AGENT_LABELS.get(record.agent, record.agent.title()),
            match_reason(record, query),
            _candidate_details(record, show_home=show_home),
        )
    output.print(table)

    hidden_count = match_count - len(records)
    if hidden_count > 0:
        output.print(
            f"[dim]{hidden_count} older matches are not shown. "
            "Use a more specific name, ID, or full path to select one.[/dim]"
        )

    choices = [str(index) for index in range(1, len(records) + 1)]
    choices.append("q")
    choice = Prompt.ask(
        f"[bold]{prompt}[/bold] [dim](number or q to cancel)[/dim]",
        choices=choices,
        show_choices=False,
        console=output,
    )
    if choice == "q":
        return None
    return records[int(choice) - 1]
