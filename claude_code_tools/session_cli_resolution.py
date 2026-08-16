"""Interactive shared session resolution for ``aichat`` commands."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from claude_code_tools.session_resolution import ResolvedSessionQuery


def session_homes() -> tuple[str | None, str | None]:
    """Return agent homes configured on the root Click context."""
    context = click.get_current_context(silent=True)
    if context is None:
        return None, None
    root_obj = context.find_root().obj or {}
    return root_obj.get("claude_home"), root_obj.get("codex_home")


def resolve_cli_session(
    session: str,
    agent: str | None = None,
    *,
    claude_home: str | None = None,
    codex_home: str | None = None,
    interactive: bool = True,
    allow_legacy_claude_filename: bool = False,
    selection_prompt: str = "Which session do you want to use?",
) -> "ResolvedSessionQuery":
    """Resolve one CLI session query and handle user-facing failures.

    Args:
        session: Name, ID/fragment, filename fragment, or direct path.
        agent: Optional hard agent constraint.
        claude_home: Optional Claude home override.
        codex_home: Optional Codex home override.
        interactive: Whether a TTY ambiguity may offer selection.
        allow_legacy_claude_filename: Preserve exact transcript-stem lookup
            for commands that historically accepted it.
        selection_prompt: Question shown beneath an ambiguity table.

    Returns:
        The unique or interactively selected session.
    """
    from claude_code_tools.session_resolution import (
        ResolvedSessionQuery,
        SessionQueryAmbiguity,
        SessionQueryError,
        resolve_session_query,
        resolved_session_from_record,
    )

    root_claude_home, root_codex_home = session_homes()
    effective_claude_home = root_claude_home if claude_home is None else claude_home
    effective_codex_home = root_codex_home if codex_home is None else codex_home
    try:
        return resolve_session_query(
            session,
            agent=agent,
            claude_home=effective_claude_home,
            codex_home=effective_codex_home,
        )
    except SessionQueryAmbiguity as error:
        if interactive:
            from claude_code_tools.session_selection import (
                choose_session_record,
                stdin_is_interactive,
            )

            if stdin_is_interactive():
                try:
                    selected = choose_session_record(
                        error.query,
                        error.records,
                        error.match_count,
                        prompt=selection_prompt,
                    )
                except (EOFError, KeyboardInterrupt):
                    selected = None
                if selected is not None:
                    return resolved_session_from_record(selected)
                click.echo("Session selection cancelled.", err=True)
                raise click.exceptions.Exit(1) from None
        click.echo(f"Error: {error}", err=True)
        raise click.exceptions.Exit(1) from None
    except SessionQueryError as error:
        if allow_legacy_claude_filename and agent == "claude":
            legacy_path = exact_claude_filename(
                session,
                effective_claude_home,
            )
            if legacy_path is not None:
                return ResolvedSessionQuery(
                    "claude",
                    legacy_path,
                    None,
                    None,
                )
        click.echo(f"Error: {error}", err=True)
        raise click.exceptions.Exit(1) from None


def exact_claude_filename(
    session: str,
    claude_home: str | None,
) -> Path | None:
    """Resolve one unique exact Claude transcript stem without indexing."""
    if not session or Path(session).name != session:
        return None

    from claude_code_tools.session_utils import get_claude_home

    projects = get_claude_home(claude_home) / "projects"
    try:
        matches = [
            candidate
            for project in projects.iterdir()
            if project.is_dir()
            and (candidate := project / f"{session}.jsonl").is_file()
        ]
    except (OSError, RuntimeError, ValueError):
        return None
    return matches[0].absolute() if len(matches) == 1 else None


def resolve_export_session(
    session: str,
    agent: str | None = None,
) -> "ResolvedSessionQuery":
    """Resolve an export query with the shared interactive behavior."""
    return resolve_cli_session(
        session,
        agent,
        selection_prompt="Which session do you want to export?",
    )
