"""Shared-resolution adapters for legacy argparse session commands."""

from __future__ import annotations

from collections.abc import Sequence

from claude_code_tools.cli_passthrough import (
    MissingPositionalError,
    option_value,
    positional_index,
    replace_positional,
)
from claude_code_tools.session_cli_resolution import (
    resolve_cli_session,
    session_homes,
)


def resolved_find_original_args(arguments: Sequence[str]) -> list[str]:
    """Replace ``find-original`` SESSION while preserving its options."""
    value_options = ("--claude-home",)
    try:
        session_index = positional_index(arguments, value_options=value_options)
    except MissingPositionalError:
        return list(arguments)
    query = arguments[session_index]
    resolved = resolve_cli_session(
        query,
        claude_home=option_value(arguments, "--claude-home"),
        selection_prompt="Which session's original do you want to find?",
    )
    return replace_positional(
        arguments,
        str(resolved.session_file),
        value_options=value_options,
    )


def resolved_find_derived_args(arguments: Sequence[str]) -> list[str]:
    """Replace ``find-derived`` SESSION while preserving its options."""
    value_options = (
        "--claude-home",
        "--codex-home",
        "--search-dir",
        "-d",
    )
    try:
        session_index = positional_index(arguments, value_options=value_options)
    except MissingPositionalError:
        return list(arguments)
    query = arguments[session_index]
    resolved = resolve_cli_session(
        query,
        claude_home=option_value(arguments, "--claude-home"),
        codex_home=option_value(arguments, "--codex-home"),
        selection_prompt="Which session's descendants do you want to find?",
    )
    delegated = replace_positional(
        arguments,
        str(resolved.session_file),
        value_options=value_options,
    )
    root_claude_home, root_codex_home = session_homes()
    if (
        option_value(delegated, "--claude-home") is None
        and root_claude_home is not None
    ):
        delegated.extend(["--claude-home", root_claude_home])
    if (
        option_value(delegated, "--codex-home") is None
        and root_codex_home is not None
    ):
        delegated.extend(["--codex-home", root_codex_home])
    return delegated


def resolved_menu_args(arguments: Sequence[str]) -> list[str]:
    """Resolve ``menu`` SESSION and supply exact agent/home metadata."""
    value_options = (
        "--agent",
        "--claude-home",
        "--codex-home",
        "--start-screen",
    )
    try:
        session_index = positional_index(arguments, value_options=value_options)
    except MissingPositionalError:
        return list(arguments)
    query = arguments[session_index]
    resolved = resolve_cli_session(
        query,
        option_value(arguments, "--agent"),
        claude_home=option_value(arguments, "--claude-home"),
        codex_home=option_value(arguments, "--codex-home"),
        selection_prompt="Which session do you want to open?",
    )
    delegated = replace_positional(
        arguments,
        str(resolved.session_file),
        value_options=value_options,
    )
    if option_value(delegated, "--agent") is None:
        delegated.extend(["--agent", resolved.agent])
    root_claude_home, root_codex_home = session_homes()
    if (
        option_value(delegated, "--claude-home") is None
        and root_claude_home is not None
    ):
        delegated.extend(["--claude-home", root_claude_home])
    if option_value(delegated, "--codex-home") is None and root_codex_home is not None:
        delegated.extend(["--codex-home", root_codex_home])
    return delegated


def resolved_agent_export_args(
    arguments: Sequence[str],
    agent: str,
) -> list[str] | None:
    """Resolve an agent-specific export SESSION without moving options."""
    home_option = "--claude-home" if agent == "claude" else "--codex-home"
    value_options = ("--output", "-o", home_option)
    try:
        session_index = positional_index(
            arguments,
            value_options=value_options,
        )
    except MissingPositionalError:
        return None
    resolved = resolve_cli_session(
        arguments[session_index],
        agent,
        claude_home=option_value(arguments, home_option) if agent == "claude" else None,
        codex_home=option_value(arguments, home_option) if agent == "codex" else None,
        selection_prompt=(f"Which {agent.title()} session do you want to export?"),
    )
    return replace_positional(
        arguments,
        str(resolved.session_file),
        value_options=value_options,
    )
