"""Command-line interfaces for the shared Codex app server."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import NoReturn, Sequence

import click

from claude_code_tools.codex_server import (
    ENDPOINT,
    CodexServerError,
    ServerStatus,
    _command_env,
    _log_tail,
    _open_log_reader,
    _paths,
    _resolve_codex,
    ensure_server,
    get_status,
    restart_server,
    stop_server,
)


NON_TUI_COMMANDS = {
    "a",
    "app",
    "app-server",
    "apply",
    "archive",
    "cloud",
    "completion",
    "debug",
    "delete",
    "doctor",
    "e",
    "exec",
    "exec-server",
    "features",
    "help",
    "login",
    "logout",
    "mcp",
    "mcp-server",
    "plugin",
    "remote-control",
    "review",
    "sandbox",
    "unarchive",
    "update",
}

GLOBAL_OPTIONS_WITH_VALUES = {
    "--add-dir",
    "--ask-for-approval",
    "--cd",
    "--config",
    "--disable",
    "--enable",
    "--image",
    "--local-provider",
    "--model",
    "--profile",
    "--remote-auth-token-env",
    "--sandbox",
    "-C",
    "-a",
    "-c",
    "-i",
    "-m",
    "-p",
    "-s",
}


def _echo_status(status: ServerStatus, json_output: bool) -> None:
    """Render lifecycle status for a human or script."""
    if json_output:
        click.echo(json.dumps(status.as_json(), sort_keys=True))
        return
    if status.status == "running" and status.ownership == "helper":
        click.echo(f"running (managed by codex-server, pid {status.pid})")
    elif status.status == "running":
        click.echo("running (external; codex-server will not stop it)")
    elif status.ownership == "helper":
        click.echo(f"{status.status} (managed by codex-server, pid {status.pid})")
    else:
        click.echo(status.status)
    click.echo(f"endpoint: {ENDPOINT}")
    if status.detail:
        click.echo(f"detail: {status.detail}")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def server_cli() -> None:
    """Manage the shared app server used for Codex workflow callbacks."""


@server_cli.command("start")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def start_command(json_output: bool) -> None:
    """Start the server, or reuse the listener already running."""
    try:
        status = ensure_server()
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_status(status, json_output)


@server_cli.command("status")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def status_command(json_output: bool) -> None:
    """Show endpoint health and ownership."""
    try:
        status = get_status()
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_status(status, json_output)


@server_cli.command("stop")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def stop_command(json_output: bool) -> None:
    """Stop the server only when this helper owns it."""
    try:
        status = stop_server()
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_status(status, json_output)


@server_cli.command("restart")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def restart_command(json_output: bool) -> None:
    """Restart the server only when this helper owns it."""
    try:
        status = restart_server()
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_status(status, json_output)


@server_cli.command("logs")
@click.option(
    "--lines",
    "line_count",
    default=100,
    show_default=True,
    type=click.IntRange(min=0),
    help="Number of recent lines to show.",
)
@click.option("--follow", "follow", is_flag=True, help="Keep following the log.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def logs_command(line_count: int, follow: bool, json_output: bool) -> None:
    """Show output from the helper-owned app server."""
    try:
        paths = _paths(os.environ)
        with _open_log_reader(paths.log_path):
            pass
        tail = _log_tail(paths.log_path, line_count)
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        if follow:
            raise click.ClickException("--json and --follow cannot be combined")
        click.echo(
            json.dumps(
                {"logPath": str(paths.log_path), "text": tail},
                sort_keys=True,
            )
        )
        return
    if tail:
        click.echo(tail)
    if not follow:
        return
    try:
        with _open_log_reader(paths.log_path) as stream:
            stream.seek(0, os.SEEK_END)
            while True:
                line = stream.readline()
                if line:
                    click.echo(line, nl=False)
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        return
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc


def _has_remote_option(arguments: Sequence[str]) -> bool:
    """Return whether arguments try to override the managed endpoint."""
    for argument in arguments:
        if argument == "--":
            return False
        if argument == "--remote" or argument.startswith("--remote="):
            return True
    return False


def _is_information_only(arguments: Sequence[str]) -> bool:
    """Return whether Codex can answer without a running server."""
    for argument in arguments:
        if argument == "--":
            return False
        if argument in {"-h", "--help", "-V", "--version"}:
            return True
    return False


def _is_non_tui_command(arguments: Sequence[str]) -> bool:
    """Return whether arguments select a known non-TUI subcommand."""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return False
        option = argument.split("=", 1)[0]
        if option in GLOBAL_OPTIONS_WITH_VALUES:
            index += 1 if "=" in argument else 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument in NON_TUI_COMMANDS
    return False


def dynamic_main() -> NoReturn:
    """Ensure the shared server, then replace this process with Codex."""
    arguments = sys.argv[1:]
    if _has_remote_option(arguments):
        click.echo(
            "Error: codex-dynamic owns --remote; use codex directly for a "
            "custom endpoint",
            err=True,
        )
        raise SystemExit(2)
    active_env = dict(os.environ)
    use_remote = not (_is_information_only(arguments) or _is_non_tui_command(arguments))
    try:
        codex_path = _resolve_codex(active_env)
        if use_remote:
            paths = _paths(active_env)
            child_env = _command_env(active_env, paths)
            ensure_server(child_env)
        else:
            child_env = active_env
    except CodexServerError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    command = (
        [codex_path, "--remote", ENDPOINT, *arguments]
        if use_remote
        else [codex_path, *arguments]
    )
    try:
        os.execvpe(codex_path, command, child_env)
    except OSError as exc:
        click.echo(f"Error: cannot launch Codex: {exc}", err=True)
        raise SystemExit(126) from exc
    raise AssertionError("os.execvpe unexpectedly returned")


def server_main() -> None:
    """Run the ``codex-server`` command group."""
    server_cli(prog_name="codex-server")


if __name__ == "__main__":
    server_main()
