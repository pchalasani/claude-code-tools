"""Rendering and CLI entry layer for ``aichat resolve``.

Split from :mod:`claude_code_tools.resolve_session` (which owns the
pure resolution layer) to keep both modules under the repo's
1000-line file limit.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import cast

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_code_tools.resolve_session import (
    Agent,
    ResolveResult,
    ResolverError,
    SessionRecord,
    resolve,
)
from claude_code_tools.session_utils import format_session_id_display


def _result_payload(result: ResolveResult) -> dict[str, object]:
    """Convert a tagged result to its exact JSON payload."""
    if result.kind == "single":
        return result.records[0].to_dict()
    if result.kind == "ambiguous":
        return {
            "error": "ambiguous",
            "query": result.query,
            "agent": result.agent,
            "match_count": result.match_count,
            "candidates": [record.to_dict() for record in result.records],
        }
    return {
        "error": "not_found",
        "query": result.query,
        "agent": result.agent,
        "home": result.home,
    }


def render_json(result: ResolveResult) -> None:
    """Print one JSON object for a tagged resolution result."""
    print(json.dumps(_result_payload(result)))


def _success_table(record: SessionRecord) -> Table:
    """Build the human-readable table for one resolved session."""
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")
    values = (
        ("Agent", record.agent),
        (
            "Session ID",
            format_session_id_display(
                record.session_id,
                truncate_length=len(record.session_id),
            ).removesuffix("..."),
        ),
        ("Name", record.name or "—"),
        ("Directory", record.directory or "—"),
        ("Home", record.home),
        ("Session file", record.session_file),
        ("Matched by", record.matched_by or "—"),
        ("Modified", record.modified),
        ("Archived", "yes" if record.archived else "no"),
    )
    for label, value in values:
        table.add_row(label, Text(value))
    return table


def _candidate_table(records: tuple[SessionRecord, ...]) -> Table:
    """Build the disambiguation table for candidate sessions."""
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Session ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Directory")
    table.add_column("Modified", no_wrap=True)
    table.add_column("Archived", no_wrap=True)
    for record in records:
        table.add_row(
            Text(format_session_id_display(record.session_id)),
            Text(record.name or "—"),
            Text(record.directory or "—"),
            Text(record.modified),
            "yes" if record.archived else "no",
        )
    return table


def render_pretty(result: ResolveResult) -> None:
    """Print a Rich panel or table for a tagged resolution result."""
    console = Console()
    if result.kind == "single":
        console.print(Panel(_success_table(result.records[0]), title="Session"))
    elif result.kind == "ambiguous":
        message = Text(style="yellow")
        message.append(f"{result.match_count} sessions match '")
        message.append(result.query)
        message.append("' — disambiguate:")
        console.print(message)
        console.print(_candidate_table(result.records))
    else:
        message = Text(style="yellow")
        message.append("No session found for '")
        message.append(result.query)
        message.append(f"' in {result.home}")
        console.print(message)


def _render_error(code: str, detail: str, pretty: bool) -> None:
    """Render an expected operational error."""
    if pretty:
        message = Text()
        message.append("Error:", style="red")
        message.append(f" {detail}")
        Console().print(message)
    else:
        print(json.dumps({"error": code, "detail": detail}))


def run(
    query: str,
    agent: str,
    home: str | Path | None,
    fmt: str = "auto",
) -> int:
    """Resolve, render, and return the command's process exit code.

    Args:
        query: Session name, full ID, ID prefix or substring, or a
            session-file name fragment.
        agent: ``claude`` or ``codex``.
        home: Optional explicit agent home.
        fmt: ``auto``, ``json``, or ``pretty``.

    Returns:
        Zero for a unique match, two for ambiguity, or one otherwise.
    """
    pretty = fmt == "pretty" or (fmt == "auto" and sys.stdout.isatty())
    try:
        if agent not in ("claude", "codex"):
            raise ResolverError("invalid_agent", f"Unsupported agent: {agent}")
        if fmt not in ("auto", "json", "pretty"):
            raise ResolverError("invalid_format", f"Unsupported format: {fmt}")
        result = resolve(query, cast(Agent, agent), home)
    except ResolverError as error:
        _render_error(error.code, error.detail, pretty)
        return 1
    except (OSError, sqlite3.Error) as error:
        _render_error(type(error).__name__, str(error), pretty)
        return 1
    except Exception as error:
        _render_error("resolver_error", str(error) or type(error).__name__, pretty)
        return 1

    if pretty:
        render_pretty(result)
    else:
        render_json(result)
    if result.kind == "single":
        return 0
    if result.kind == "ambiguous":
        return 2
    return 1
