"""CLI for the msg inter-agent communication system."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import click

from claude_code_tools.amux.scan import resolve_pane_agent
from claude_code_tools.process_identity import process_start_identity

from . import maintenance as maintenance_mode
from .json_contract import (
    agent_payload,
    continuation_payload,
    emit_error,
    emit_json,
    watcher_payload,
)
from .migrations import CURRENT_SCHEMA_VERSION
from .models import AgentKind, ConsumerProtocol, RegistrationIdentity
from .store import DEFAULT_DB_DIR, DEFAULT_DB_PATH, MsgStore
from .watcher import distribution_version, watcher_module_sha256


def _operation_from_argv(argv: list[str]) -> str:
    values: list[str] = []
    skip_value = False
    for arg in argv:
        if skip_value:
            skip_value = False
            continue
        if arg == "--db":
            skip_value = True
            continue
        if arg in {"--local", "--json"} or arg.startswith("--db="):
            continue
        if arg.startswith("-"):
            continue
        values.append(arg)
    if not values:
        return "cli"
    if values[0] in {"continuation", "maintenance", "watch", "thread"}:
        return ".".join(values[:2])
    return values[0]


class MachineContractGroup(click.Group):
    """Convert Click parse/callback failures into the versioned JSON contract."""

    def main(
        self,
        args=None,
        prog_name=None,
        complete_var=None,
        standalone_mode=True,
        **extra,
    ):
        argv = list(args if args is not None else sys.argv[1:])
        if "--json" not in argv:
            return super().main(
                args=argv,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                **extra,
            )
        operation = _operation_from_argv(argv)
        try:
            return super().main(
                args=argv,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
        except PublicCliError as exc:
            emit_error(operation, exc.error_code, exc.message)
            raise SystemExit(exc.exit_code) from None
        except click.BadParameter as exc:
            emit_error(operation, "invalid_value", exc.format_message())
            raise SystemExit(exc.exit_code) from None
        except click.UsageError as exc:
            emit_error(operation, "usage_error", exc.format_message())
            raise SystemExit(exc.exit_code) from None
        except click.ClickException as exc:
            emit_error(operation, "command_error", exc.format_message())
            raise SystemExit(exc.exit_code) from None
        except SystemExit as exc:
            if not exc.code:
                return None
            emit_error(operation, "command_error", "command failed")
            raise
        except Exception:
            emit_error(operation, "internal_error", "internal command failure")
            raise SystemExit(1) from None


class PublicCliError(click.ClickException):
    """A stable public error with a machine-readable code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def json_option(function):
    """Add the per-command machine-output flag without changing legacy syntax."""
    return click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Emit one versioned JSON object.",
    )(function)


def _check_db_writable(db_dir: str) -> bool:
    """Check if we can write to the DB directory."""
    from pathlib import Path
    try:
        Path(db_dir).mkdir(parents=True, exist_ok=True)
        test_file = os.path.join(db_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except OSError:
        return False


def _get_local_db_path() -> str:
    """Get project-local DB path."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            return os.path.join(root, ".msg", "msg.db")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return os.path.join(os.getcwd(), ".msg", "msg.db")


def _get_store(
    db_path: str | None = None,
    local: bool = False,
) -> MsgStore:
    if db_path:
        return MsgStore(db_path)
    if local:
        return MsgStore(_get_local_db_path())
    return MsgStore(DEFAULT_DB_PATH)


def _detect_tmux_pane() -> str | None:
    """Auto-detect current tmux pane ID from env."""
    return os.environ.get("TMUX_PANE")


def _detect_tmux_session(pane: str | None = None) -> str | None:
    """Auto-detect current tmux session name."""
    pane = pane or os.environ.get("TMUX_PANE")
    try:
        cmd = ["tmux"]
        tmux_socket = _detect_tmux_socket(pane)
        if tmux_socket:
            cmd += ["-S", tmux_socket]
        cmd += ["display-message"]
        if pane:
            cmd += ["-t", pane]
        cmd += ["-p", "#{session_name}"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _detect_tmux_socket(pane: str | None = None) -> str | None:
    """Auto-detect tmux socket path."""
    tmux_env = os.environ.get("TMUX", "")
    if tmux_env:
        # TMUX env var format: /path/to/socket,pid,session
        parts = tmux_env.split(",")
        if parts:
            return parts[0]
    try:
        command = ["tmux", "display-message"]
        if pane:
            command += ["-t", pane]
        result = subprocess.run(
                [*command, "-p", "#{socket_path}"],
                capture_output=True, text=True, timeout=5,
            )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _resolve_agent(
    store: MsgStore,
    name: str,
    tmux_session: str | None = None,
    tmux_socket: str | None = None,
) -> dict | None:
    """Resolve agent name to agent, auto-detecting scope."""
    tmux_session = tmux_session or _detect_tmux_session()
    tmux_socket = tmux_socket or _detect_tmux_socket()
    if not tmux_session:
        click.echo("Error: cannot detect tmux session.", err=True)
        return None
    agent = store.get_agent_by_name(
        name, tmux_session, tmux_socket,
    )
    if not agent:
        click.echo(f"Error: agent '{name}' not found.", err=True)
        return None
    return agent


def _get_self_agent(store: MsgStore) -> dict | None:
    """Find the agent registered for this pane."""
    pane_id = _detect_tmux_pane()
    if not pane_id:
        return None
    tmux_session = _detect_tmux_session()
    if not tmux_session:
        return None
    tmux_socket = _detect_tmux_socket(pane_id)
    matches = [
        agent for agent in store.list_agents(tmux_session, tmux_socket)
        if agent.pane_id == pane_id and agent.tmux_socket == tmux_socket
    ]
    return matches[0] if len(matches) == 1 else None


def _get_exact_self_agent(store: MsgStore):
    """Resolve this pane and prove it is still the registered TUI process."""
    registered = _get_self_agent(store)
    if registered is None:
        return None
    target = resolve_pane_agent(registered.pane_id, registered.tmux_socket)
    if target is None:
        return None
    start_identity = process_start_identity(target.pid)
    actual = (
        target.session,
        target.extra.get("pane_id"),
        target.kind,
        target.pid,
        start_identity,
    )
    expected = (
        registered.tmux_session,
        registered.pane_id,
        registered.agent_kind.value,
        registered.pid,
        registered.process_start_identity,
    )
    return registered if start_identity is not None and actual == expected else None


def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        elif secs < 3600:
            return f"{secs // 60}m ago"
        elif secs < 86400:
            return f"{secs // 3600}h ago"
        else:
            return f"{secs // 86400}d ago"
    except (ValueError, TypeError):
        return iso_str


def _ensure_watcher_running(store: MsgStore) -> None:
    """Legacy implicit startup delegates to the exact watcher lifecycle."""
    _start_exact_watcher(store)


def _spawn_watcher(db_path: str) -> int | None:
    """Spawn this exact Python environment's watcher as a detached daemon."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "claude_code_tools.msg.cli",
            "--db",
            str(db_path),
            "watch",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process.pid


def _watcher_health(store: MsgStore):
    watchers = store.get_watcher_info()
    watcher = watchers[0] if watchers else None
    if watcher is None:
        return None, "not_running", []
    actual_db_schema = store.get_schema_version()
    expected = {
        "process_start_identity": process_start_identity(watcher.pid),
        "distribution_version": distribution_version(),
        "module_sha256": watcher_module_sha256(),
        "db_schema_version": actual_db_schema,
    }
    actual = {
        "process_start_identity": watcher.process_start_identity,
        "distribution_version": watcher.distribution_version,
        "module_sha256": watcher.module_sha256,
        "db_schema_version": watcher.db_schema_version,
    }
    mismatches = [
        field for field, value in expected.items()
        if value is None or actual[field] != value
    ]
    if actual_db_schema != CURRENT_SCHEMA_VERSION:
        mismatches.append("supported_db_schema_version")
    if mismatches:
        return watcher, "mismatch", mismatches
    return watcher, ("healthy" if store.is_watcher_alive() else "stale"), []


def _stop_exact_watcher(store: MsgStore) -> bool:
    watcher, _state, _mismatches = _watcher_health(store)
    if watcher is None:
        return False
    current_identity = process_start_identity(watcher.pid)
    if (
        current_identity is None
        or current_identity != watcher.process_start_identity
    ):
        raise click.ClickException(
            "watcher process identity is stale or the PID was reused"
        )
    os.kill(watcher.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process_start_identity(watcher.pid) != current_identity:
            store.remove_watcher(watcher.watcher_id)
            return True
        time.sleep(0.05)
    raise click.ClickException("watcher did not stop before timeout")


def _start_exact_watcher(store: MsgStore):
    """Ensure one matching watcher and return ``(heartbeat, started)``."""
    watcher, state, _mismatches = _watcher_health(store)
    if state == "healthy":
        return watcher, False
    if watcher is not None:
        current = process_start_identity(watcher.pid)
        if current == watcher.process_start_identity and current is not None:
            _stop_exact_watcher(store)
        else:
            store.remove_watcher(watcher.watcher_id)
    _spawn_watcher(store.db_path)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        watcher, state, _mismatches = _watcher_health(store)
        if state == "healthy":
            return watcher, True
        time.sleep(0.05)
    raise click.ClickException("watcher did not publish a matching heartbeat")


@click.group(cls=MachineContractGroup)
@click.option(
    "--db", default=None,
    help="Path to msg database",
)
@click.option(
    "--local", is_flag=True, default=False,
    help="Use project-local DB instead of global ~/.msg/",
)
@click.pass_context
def cli(
    ctx: click.Context,
    db: str | None,
    local: bool,
) -> None:
    """msg: Inter-agent communication for coding agents."""
    ctx.ensure_object(dict)

    if db:
        resolved_db_path = db
    elif local:
        resolved_db_path = _get_local_db_path()
    else:
        resolved_db_path = DEFAULT_DB_PATH
    ctx.obj["db_path"] = resolved_db_path

    if ctx.invoked_subcommand == "maintenance":
        return
    if maintenance_mode.is_active(resolved_db_path):
        raise click.ClickException("msg maintenance mode is active")

    # If no explicit path, check if global DB is writable
    if not db and not local:
        if not _check_db_writable(DEFAULT_DB_DIR):
            home = os.path.expanduser("~")
            click.echo(
                "Cannot write to ~/.msg/ "
                "(sandbox restriction).\n\n"
                "Ask the user which option they prefer:\n\n"
                "Option A: Global messaging "
                "(agents across any project can talk)\n"
                "  User needs to add to "
                "~/.codex/config.toml:\n"
                "    [sandbox_workspace_write]\n"
                f'    writable_roots = ["{home}/.msg"]\n'
                "  Then restart Codex.\n\n"
                "Option B: Local messaging "
                "(this project only)\n"
                "  Works immediately, no config "
                "changes needed.\n\n"
                "If user chooses B, re-run the same "
                "command with --local flag prepended "
                "after 'msg', e.g.: "
                "msg --local register <name>",
                err=True,
            )
            sys.exit(1)

    store = _get_store(db_path=db, local=local)
    ctx.obj["store"] = store
    if ctx.invoked_subcommand != "watch":
        _ensure_watcher_running(store)


@cli.group("maintenance")
def maintenance_group() -> None:
    """Manage the fail-closed schema maintenance sentinel."""


def _maintenance_token(token_fd: int) -> bytes:
    try:
        return maintenance_mode.read_token_fd(token_fd)
    except (OSError, ValueError) as exc:
        raise PublicCliError(
            "maintenance_token_invalid", "maintenance token fd is invalid",
        ) from exc


def _raise_maintenance_error(exc: Exception) -> None:
    if isinstance(exc, maintenance_mode.MaintenanceError):
        raise PublicCliError(exc.code, exc.public_message) from exc
    raise PublicCliError(
        "maintenance_operation_failed", "maintenance operation failed",
    ) from exc


@maintenance_group.command("enter")
@click.option("--token-fd", type=int, required=True)
@json_option
@click.pass_context
def maintenance_enter(ctx: click.Context, token_fd: int, json_output: bool) -> None:
    try:
        data = maintenance_mode.enter(ctx.obj["db_path"], _maintenance_token(token_fd))
    except (OSError, ValueError) as exc:
        _raise_maintenance_error(exc)
    if json_output:
        emit_json("maintenance.enter", data)
    else:
        click.echo("Maintenance mode entered.")


@maintenance_group.command("status")
@json_option
@click.pass_context
def maintenance_status(ctx: click.Context, json_output: bool) -> None:
    try:
        data = maintenance_mode.status(ctx.obj["db_path"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _raise_maintenance_error(exc)
    if json_output:
        emit_json("maintenance.status", data)
    else:
        click.echo("Maintenance: ACTIVE" if data["active"] else "Maintenance: INACTIVE")


@maintenance_group.command("migrate")
@click.option("--token-fd", type=int, required=True)
@json_option
@click.pass_context
def maintenance_migrate(ctx: click.Context, token_fd: int, json_output: bool) -> None:
    try:
        data = maintenance_mode.migrate(ctx.obj["db_path"], _maintenance_token(token_fd))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        _raise_maintenance_error(exc)
    if json_output:
        emit_json("maintenance.migrate", data)
    else:
        click.echo(f"Migrated schema to {data['to_schema_version']}.")


@maintenance_group.command("exit")
@click.option("--token-fd", type=int, required=True)
@click.option("--postcheck-fd", type=int, required=True)
@json_option
@click.pass_context
def maintenance_exit(
    ctx: click.Context,
    token_fd: int,
    postcheck_fd: int,
    json_output: bool,
) -> None:
    try:
        exited = maintenance_mode.exit_mode(
            ctx.obj["db_path"],
            _maintenance_token(token_fd),
            maintenance_mode.read_postcheck_fd(postcheck_fd),
        )
    except (OSError, ValueError) as exc:
        _raise_maintenance_error(exc)
    if json_output:
        emit_json("maintenance.exit", {"exited": exited})
    else:
        click.echo("Maintenance mode exited.")


@cli.command()
@click.argument("name")
@click.option(
    "--pane", default=None,
    help="Tmux pane ID (auto-detected if omitted)",
)
@click.option(
    "--agent", default=None,
    type=click.Choice(["claude", "codex"]),
    help="Agent type (auto-detected if omitted)",
)
@click.option(
    "--consumer-protocol",
    default=ConsumerProtocol.LEGACY.value,
    type=click.Choice([protocol.value for protocol in ConsumerProtocol]),
)
@json_option
@click.pass_context
def register(
    ctx: click.Context,
    name: str,
    pane: str | None,
    agent: str | None,
    consumer_protocol: str,
    json_output: bool,
) -> None:
    """Register this session as a named agent."""
    store: MsgStore = ctx.obj["store"]

    pane_id = pane or _detect_tmux_pane()
    if not pane_id:
        click.echo(
            "Error: not in tmux or cannot detect pane. "
            "Use --pane to specify.",
            err=True,
        )
        sys.exit(1)

    tmux_socket = _detect_tmux_socket(pane_id)
    if not tmux_socket:
        raise click.ClickException(f"cannot resolve tmux socket for pane {pane_id}")
    target = resolve_pane_agent(pane_id, tmux_socket)
    if target is None:
        raise click.ClickException(
            f"cannot resolve one long-lived coding agent in pane {pane_id}"
        )
    tmux_session = target.session
    agent_kind = AgentKind(target.kind)
    if agent is not None and AgentKind(agent) is not agent_kind:
        raise click.ClickException(
            f"requested agent {agent} does not match pane harness {agent_kind.value}"
        )
    start_identity = process_start_identity(target.pid)
    if start_identity is None:
        raise click.ClickException(
            f"cannot prove process-start identity for pane {pane_id}"
        )
    display_addr = target.pane

    try:
        result = store.register_agent(
            name=name,
            pane_id=pane_id,
            tmux_session=tmux_session,
            agent_kind=agent_kind,
            tmux_socket=tmux_socket,
            display_addr=display_addr,
            pid=target.pid,
            cwd=target.cwd,
            consumer_protocol=ConsumerProtocol(consumer_protocol),
            process_start_identity=start_identity,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        emit_json("register", {"agent": agent_payload(result)})
    else:
        click.echo(
            f"Registered as '{name}' "
            f"(session={result.session_id[:8]}..., "
            f"pane={display_addr or pane_id}, "
            f"agent={agent_kind.value})"
        )


@cli.command()
@click.argument("name", required=False)
@click.option("--session-id", help="Retire one exact registration.")
@click.pass_context
def unregister(ctx: click.Context, name: str | None, session_id: str | None) -> None:
    """Retire a named agent, or this pane, without deleting message history."""
    store: MsgStore = ctx.obj["store"]
    if name and session_id:
        raise click.UsageError("use NAME or --session-id, not both")
    if session_id:
        agent = store.get_agent_by_id(session_id)
    elif name:
        session = _detect_tmux_session()
        agent = store.get_agent_by_name(name, session, _detect_tmux_socket()) if session else None
        if not agent:
            click.echo(f"Agent '{name}' is already unregistered.")
            return
    else:
        agent = _get_self_agent(store)
    if not agent:
        click.echo("Error: registration not found.", err=True)
        sys.exit(1)
    try:
        retired = store.retire_agent(agent.session_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not retired:
        click.echo(f"Error: agent '{agent.name}' is already retired.", err=True)
        sys.exit(1)
    click.echo(f"Unregistered '{agent.name}' (history preserved)")


@cli.command()
@click.option("--session-id", required=True, help="Exact active registration to move.")
@click.option("--pane", required=True, help="Destination tmux pane ID.")
@click.option("--replace-candidate", help="Active target registration to replace.")
@json_option
@click.pass_context
def retarget(
    ctx: click.Context,
    session_id: str,
    pane: str,
    replace_candidate: str | None,
    json_output: bool,
) -> None:
    """Move one exact active registration to another pane."""
    store: MsgStore = ctx.obj["store"]
    tmux_socket = _detect_tmux_socket(pane)
    target = resolve_pane_agent(pane, tmux_socket)
    if target is None:
        raise click.ClickException(
            f"cannot resolve one long-lived coding agent in pane {pane}"
        )
    tmux_session = target.session
    start_identity = process_start_identity(target.pid)
    if start_identity is None:
        raise click.ClickException(
            f"cannot prove process-start identity for pane {pane}"
        )
    try:
        agent = store.retarget_agent(
            session_id=session_id,
            pane_id=pane,
            tmux_session=tmux_session,
            tmux_socket=tmux_socket,
            display_addr=target.pane,
            agent_kind=AgentKind(target.kind),
            pid=target.pid,
            cwd=target.cwd,
            process_start_identity=start_identity,
            replace_candidate_session_id=replace_candidate,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        emit_json("retarget", {"agent": agent_payload(agent)})
    else:
        click.echo(f"Retargeted '{agent.name}' to {agent.display_addr or agent.pane_id}")


@cli.command("list")
@json_option
@click.pass_context
def list_agents(ctx: click.Context, json_output: bool) -> None:
    """List registered agents."""
    store: MsgStore = ctx.obj["store"]
    tmux_session = _detect_tmux_session()
    agents = store.list_agents(tmux_session, _detect_tmux_socket())

    if json_output:
        emit_json("list", {"agents": [agent_payload(agent) for agent in agents]})
        return
    if not agents:
        click.echo("No agents registered.")
        return

    click.echo(f"{'NAME':<16} {'AGENT':<8} {'PANE':<16} "
               f"{'LAST SEEN':<12}")
    click.echo("-" * 56)
    for a in agents:
        addr = a.display_addr or a.pane_id
        seen = _relative_time(a.last_seen)
        click.echo(
            f"{a.name:<16} {a.agent_kind.value:<8} "
            f"{addr:<16} {seen:<12}"
        )


@cli.group("thread")
def thread_group() -> None:
    """Thread management commands."""
    pass


@thread_group.command("create")
@click.argument("title")
@click.option(
    "--with", "participants", required=True,
    help="Comma-separated list of participant names",
)
@click.pass_context
def thread_create(
    ctx: click.Context,
    title: str,
    participants: str,
) -> None:
    """Create a new conversation thread."""
    store: MsgStore = ctx.obj["store"]
    tmux_session = _detect_tmux_session()
    tmux_socket = _detect_tmux_socket()

    me = _get_self_agent(store)
    if not me:
        click.echo(
            "Error: you are not registered. "
            "Run 'msg register <name>' first.",
            err=True,
        )
        sys.exit(1)

    # Resolve participant names to session IDs
    participant_names = [
        p.strip() for p in participants.split(",")
    ]
    participant_ids = [me.session_id]

    for pname in participant_names:
        agent = store.get_agent_by_name(
            pname, tmux_session, tmux_socket,
        )
        if not agent:
            click.echo(
                f"Error: agent '{pname}' not found.",
                err=True,
            )
            sys.exit(1)
        if agent.session_id not in participant_ids:
            participant_ids.append(agent.session_id)

    thread = store.create_thread(
        title=title,
        created_by=me.session_id,
        participant_ids=participant_ids,
    )
    all_names = [me.name] + participant_names
    click.echo(
        f"Thread '{title}' created (id={thread.id[:8]}...) "
        f"with: {', '.join(all_names)}"
    )


@cli.command("threads")
@click.pass_context
def list_threads(ctx: click.Context) -> None:
    """List active threads."""
    store: MsgStore = ctx.obj["store"]
    me = _get_self_agent(store)
    agent_id = me.session_id if me else None
    threads = store.list_threads(agent_id=agent_id)

    if not threads:
        click.echo("No threads.")
        return

    for t in threads:
        participants = store.get_thread_participants(t.id)
        names = []
        for pid in participants:
            agent = store.get_agent_by_id(pid)
            if agent:
                names.append(agent.name)
        age = _relative_time(t.created_at)
        click.echo(
            f"  {t.id[:8]}  {t.title:<24} "
            f"({', '.join(names)})  {age}"
        )


@cli.command()
@click.argument("to")
@click.argument("body")
@json_option
@click.pass_context
def send(
    ctx: click.Context,
    to: str,
    body: str,
    json_output: bool,
) -> None:
    """Send a message to one or more agents.

    TO can be a single agent name or comma-separated
    names for a group message.

    Examples:
        msg send my-claude "review auth module"
        msg send my-claude,my-codex "everyone review"
    """
    store: MsgStore = ctx.obj["store"]
    tmux_session = _detect_tmux_session()
    tmux_socket = _detect_tmux_socket()

    me = _get_exact_self_agent(store) if json_output else _get_self_agent(store)
    if not me:
        click.echo(
            "Error: you are not registered. "
            "Run 'msg register <name>' first.",
            err=True,
        )
        sys.exit(1)

    store.touch_agent(me.session_id)

    # Resolve recipient names
    recipient_names = [
        n.strip() for n in to.split(",")
    ]
    participant_ids = [me.session_id]
    for name in recipient_names:
        agent = store.get_agent_by_name(
            name, tmux_session, tmux_socket,
        )
        if not agent:
            click.echo(
                f"Error: agent '{name}' not found.",
                err=True,
            )
            sys.exit(1)
        if agent.session_id not in participant_ids:
            participant_ids.append(agent.session_id)

    # Get or create thread for this group
    thread = store.get_or_create_thread(
        participant_ids=participant_ids,
        created_by=me.session_id,
    )

    try:
        message = store.send_message(
            thread_id=thread.id,
            from_agent=me.session_id,
            body=body,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    watcher_alive = store.is_watcher_alive()
    if not watcher_alive and not json_output:
        click.echo(
            "Warning: no active watcher. "
            "Run 'msg watch' to enable notifications.",
            err=True,
        )

    if json_output:
        deliveries = store.get_deliveries_for_message(message.id)
        emit_json(
            "send",
            {
                "message_id": message.id,
                "thread_id": message.thread_id,
                "delivery_ids": [item["delivery_id"] for item in deliveries],
                "warnings": [] if watcher_alive else ["watcher_not_running"],
            },
        )
    else:
        names_str = ", ".join(recipient_names)
        click.echo(f"Sent to {names_str}: {body}")


@cli.group("continuation")
def continuation_group() -> None:
    """Manage the current registration's armed responsibility."""


def _continuation_identity(store: MsgStore) -> RegistrationIdentity:
    agent = _get_exact_self_agent(store)
    if not agent:
        raise click.ClickException("active registration not found")
    return RegistrationIdentity.from_agent(agent)


def _continuation_call(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        raise PublicCliError(
            "continuation_rejected", "continuation operation was rejected",
        ) from exc


@continuation_group.command("set")
@click.option("--generation", required=True)
@click.option("--ttl", "ttl_secs", type=click.IntRange(1, 120), required=True)
@json_option
@click.pass_context
def continuation_set(
    ctx: click.Context, generation: str, ttl_secs: int, json_output: bool,
) -> None:
    store: MsgStore = ctx.obj["store"]
    status = _continuation_call(
        store.set_continuation,
        _continuation_identity(store), generation, ttl_secs,
    )
    if json_output:
        emit_json("continuation.set", {"continuation": continuation_payload(status)})
    else:
        click.echo(f"Continuation armed: {status.state.value}")


@continuation_group.command("touch")
@click.option("--generation", required=True)
@click.option("--ttl", "ttl_secs", type=click.IntRange(1, 120), required=True)
@json_option
@click.pass_context
def continuation_touch(
    ctx: click.Context, generation: str, ttl_secs: int, json_output: bool,
) -> None:
    store: MsgStore = ctx.obj["store"]
    status = _continuation_call(
        store.touch_continuation,
        _continuation_identity(store), generation, ttl_secs,
    )
    if json_output:
        emit_json("continuation.touch", {"continuation": continuation_payload(status)})
    else:
        click.echo(f"Continuation touched: {status.state.value}")


@continuation_group.command("status")
@json_option
@click.pass_context
def continuation_status(ctx: click.Context, json_output: bool) -> None:
    store: MsgStore = ctx.obj["store"]
    identity = _continuation_identity(store)
    status = store.get_continuation_status(identity.session_id)
    if json_output:
        emit_json("continuation.status", {"continuation": continuation_payload(status)})
    else:
        click.echo(f"Continuation: {status.state.value}")


@continuation_group.command("clear")
@click.option("--generation", required=True)
@json_option
@click.pass_context
def continuation_clear(
    ctx: click.Context, generation: str, json_output: bool,
) -> None:
    store: MsgStore = ctx.obj["store"]
    cleared = _continuation_call(
        store.clear_continuation,
        _continuation_identity(store), generation,
    )
    if json_output:
        emit_json("continuation.clear", {"cleared": cleared})
    else:
        click.echo("Continuation cleared" if cleared else "Continuation unchanged")


@cli.command()
@click.argument("to")
@click.argument("body")
@click.pass_context
def reply(
    ctx: click.Context,
    to: str,
    body: str,
) -> None:
    """Reply to an agent (alias for send)."""
    ctx.invoke(send, to=to, body=body)


@cli.command()
@click.option(
    "--thread", "thread_id", default=None,
    help="Filter by thread ID (prefix match supported)",
)
@click.pass_context
def inbox(
    ctx: click.Context,
    thread_id: str | None,
) -> None:
    """Show unread messages."""
    store: MsgStore = ctx.obj["store"]
    me = _get_self_agent(store)
    if not me:
        click.echo(
            "Error: you are not registered. "
            "Run 'msg register <name>' first.",
            err=True,
        )
        sys.exit(1)

    store.touch_agent(me.session_id)

    resolved_id = None
    if thread_id:
        resolved = _resolve_thread(store, thread_id, me)
        if not resolved:
            return
        resolved_id = resolved.id

    messages = store.get_inbox(
        me.session_id, thread_id=resolved_id,
    )

    if not messages:
        click.echo("No unread messages.")
        return

    # Group by thread
    by_thread: dict[str, list[dict]] = {}
    for m in messages:
        tid = m["thread_id"]
        if tid not in by_thread:
            by_thread[tid] = []
        by_thread[tid].append(m)

    for tid, msgs in by_thread.items():
        title = msgs[0].get("thread_title", tid[:8])
        click.echo(f"\nThread: {title}")
        for m in msgs:
            age = _relative_time(m["created_at"])
            from_name = m.get("from_name", "unknown")
            click.echo(f"  {from_name} ({age}): {m['body']}")

    # Mark as read
    count = store.mark_read(
        me.session_id,
        delivery_ids=[message["delivery_id"] for message in messages],
    )
    click.echo(f"\n({count} message(s) marked as read)")


@cli.command()
@json_option
@click.pass_context
def status(ctx: click.Context, json_output: bool) -> None:
    """Check system status (watcher health, agents)."""
    store: MsgStore = ctx.obj["store"]

    # Watcher health
    watchers = store.get_watcher_info()
    if json_output:
        watcher, watcher_state, mismatches = _watcher_health(store)
        tmux_session = _detect_tmux_session()
        agents = store.list_agents(tmux_session, _detect_tmux_socket())
        emit_json(
            "status",
            {
                "watcher": watcher_payload(watcher) if watcher else None,
                "watcher_state": watcher_state,
                "watcher_mismatches": mismatches,
                "agents": [agent_payload(agent) for agent in agents],
            },
        )
        return
    if not watchers:
        click.echo("Watcher: NOT RUNNING")
    else:
        alive = store.is_watcher_alive()
        w = watchers[0]
        hb = _relative_time(w.last_heartbeat)
        status_str = "ALIVE" if alive else "STALE"
        click.echo(
            f"Watcher: {status_str} "
            f"(pid={w.pid}, last heartbeat={hb})"
        )

    # Agents
    tmux_session = _detect_tmux_session()
    agents = store.list_agents(tmux_session, _detect_tmux_socket())
    click.echo(f"\nAgents: {len(agents)} registered")
    for a in agents:
        seen = _relative_time(a.last_seen)
        click.echo(f"  {a.name} ({a.agent_kind.value}) "
                    f"- last seen {seen}")


@cli.group(invoke_without_command=True)
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Run the legacy watcher, or manage its daemon lifecycle."""
    if ctx.invoked_subcommand is not None:
        return
    from .watcher import run_watcher
    db_path = ctx.obj["store"].db_path
    click.echo("Starting msg watcher daemon...")
    run_watcher(db_path=db_path)


@watch.command("status")
@json_option
@click.pass_context
def watch_status(ctx: click.Context, json_output: bool) -> None:
    store: MsgStore = ctx.obj["store"]
    watcher, state, mismatches = _watcher_health(store)
    data = {
        "state": state,
        "mismatches": mismatches,
        "watcher": watcher_payload(watcher) if watcher else None,
    }
    if json_output:
        emit_json("watch.status", data)
    else:
        click.echo(f"Watcher: {state.upper()}")


@watch.command("stop")
@json_option
@click.pass_context
def watch_stop(ctx: click.Context, json_output: bool) -> None:
    stopped = _stop_exact_watcher(ctx.obj["store"])
    if json_output:
        emit_json("watch.stop", {"stopped": stopped})
    else:
        click.echo("Watcher stopped." if stopped else "Watcher already stopped.")


@watch.command("start")
@json_option
@click.pass_context
def watch_start(ctx: click.Context, json_output: bool) -> None:
    store: MsgStore = ctx.obj["store"]
    watcher, started = _start_exact_watcher(store)
    data = {
        "started": started,
        "watcher": watcher_payload(watcher) if watcher else None,
    }
    if json_output:
        emit_json("watch.start", data)
    else:
        click.echo("Watcher started." if started else "Watcher already running.")


def _resolve_thread(
    store: MsgStore,
    thread_id: str,
    me: object,
) -> object | None:
    """Resolve a thread ID prefix to a Thread object."""
    threads = store.list_threads(
        agent_id=me.session_id,
    )
    matches = [
        t for t in threads if t.id.startswith(thread_id)
    ]
    if len(matches) == 0:
        click.echo(
            f"Error: no thread matching '{thread_id}'.",
            err=True,
        )
        return None
    if len(matches) > 1:
        click.echo(
            f"Error: '{thread_id}' matches multiple "
            f"threads. Be more specific.",
            err=True,
        )
        return None
    return matches[0]


def main() -> None:
    """Entry point for the msg CLI."""
    cli()


if __name__ == "__main__":
    main()
