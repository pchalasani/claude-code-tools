"""Command-line interfaces for the shared Codex app server."""

from __future__ import annotations

import codecs
import json
import os
import sys
import time
from typing import BinaryIO, NoReturn, Sequence

import click

from claude_code_tools.codex_server import (
    CodexServerError,
    ServerStatus,
    _command_env,
    _log_generation_anchor_bytes,
    _log_tail_stream,
    _open_log_reader,
    _paths,
    _read_state,
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

CALLBACK_ENDPOINT_ENV = "CCTOOLS_CODEX_CALLBACK_ENDPOINT"

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

SERVER_CONFIGURATION_OPTIONS = {
    "--config",
    "--disable",
    "--enable",
    "--profile",
    "-c",
    "-p",
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
    click.echo(f"endpoint: {status.paths.endpoint}")
    if status.detail:
        click.echo(f"detail: {status.detail}")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def server_cli() -> None:
    """Manage generated app servers used for Codex workflow callbacks."""


@server_cli.command("start")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def start_command(json_output: bool) -> None:
    """Start or reuse the server for the current Codex/plugin generation."""
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
@click.option(
    "--force",
    is_flag=True,
    help="Stop all generations; every connected codex-dynamic TUI will exit.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def stop_command(force: bool, json_output: bool) -> None:
    """Stop the current generation; use --force to stop all generations."""
    try:
        status = stop_server(allow_disconnect=force)
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_status(status, json_output)


@server_cli.command("restart")
@click.option(
    "--force",
    is_flag=True,
    help="Restart all generations; every connected codex-dynamic TUI will exit.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON status.")
def restart_command(force: bool, json_output: bool) -> None:
    """Restart the current generation; use --force to clean up all."""
    try:
        status = restart_server(allow_disconnect=force)
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
    if json_output and follow:
        raise click.ClickException("--json and --follow cannot be combined")
    try:
        paths = _paths(os.environ)
        state = _read_state(paths)
        expected_identity = state.log_identity if state is not None else None
        if state is not None and expected_identity is None:
            raise CodexServerError(
                "active app-server state has no supervisor-owned log identity; "
                "restart codex-server before reading its log"
            )
        with _open_log_reader(paths.log_path, expected_identity) as stream:
            snapshot = _log_tail_stream(stream, line_count)
            tail = snapshot.text
            if json_output:
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
            stream.seek(snapshot.end)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            anchor = (
                snapshot.end,
                snapshot.generation,
                snapshot.suffix,
            )
            while True:
                if _log_was_rewritten(stream, anchor):
                    stream.seek(0)
                    decoder.reset()
                chunk = stream.read(8192)
                if chunk:
                    click.echo(decoder.decode(chunk), nl=False)
                    anchor = _log_anchor(stream)
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        return
    except CodexServerError as exc:
        raise click.ClickException(str(exc)) from exc


def _log_anchor(
    stream: BinaryIO,
    generation_length: int = _log_generation_anchor_bytes,
    suffix_length: int = 64,
) -> tuple[int, bytes, bytes]:
    """Capture generation-prefix and suffix bytes around the current offset."""
    position = stream.tell()
    start = max(0, position - suffix_length)
    prefix = os.pread(stream.fileno(), generation_length, 0)
    suffix = os.pread(stream.fileno(), position - start, start)
    return position, prefix, suffix


def _log_was_rewritten(
    stream: BinaryIO,
    anchor: tuple[int, bytes, bytes],
) -> bool:
    """Detect truncate-and-regrow even when the file regained its old size."""
    position, expected_prefix, expected_suffix = anchor
    if stream.tell() != position or os.fstat(stream.fileno()).st_size < position:
        return True
    actual_prefix = os.pread(stream.fileno(), len(expected_prefix), 0)
    if actual_prefix != expected_prefix:
        return True
    start = position - len(expected_suffix)
    return os.pread(stream.fileno(), len(expected_suffix), start) != expected_suffix


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


def _server_configuration_options(arguments: Sequence[str]) -> list[str]:
    """Extract global flags that can change the app-server plugin snapshot."""
    selected: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        option = argument.split("=", 1)[0]
        if option not in SERVER_CONFIGURATION_OPTIONS:
            index += 1
            continue
        selected.append(argument)
        if "=" not in argument and index + 1 < len(arguments):
            index += 1
            selected.append(arguments[index])
        index += 1
    return selected


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
    endpoint: str | None = None
    try:
        codex_path = _resolve_codex(active_env)
        if use_remote:
            status = ensure_server(
                active_env,
                codex_options=_server_configuration_options(arguments),
            )
            endpoint = status.paths.endpoint
            child_env = _command_env(active_env, status.paths)
            child_env[CALLBACK_ENDPOINT_ENV] = endpoint
        else:
            child_env = dict(active_env)
            child_env.pop(CALLBACK_ENDPOINT_ENV, None)
    except CodexServerError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    if use_remote:
        assert endpoint is not None
        command = [
            codex_path,
            "--config",
            (
                f"shell_environment_policy.set.{CALLBACK_ENDPOINT_ENV}="
                f"{json.dumps(endpoint)}"
            ),
            "--remote",
            endpoint,
            *arguments,
        ]
    else:
        command = [codex_path, *arguments]
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
