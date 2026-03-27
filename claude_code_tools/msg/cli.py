"""CLI for the msg inter-agent communication system."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import click

from .models import AgentKind
from .store import MsgStore, DEFAULT_DB_PATH, DEFAULT_DB_DIR


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


def _detect_tmux_session() -> str | None:
    """Auto-detect current tmux session name."""
    pane = os.environ.get("TMUX_PANE")
    try:
        cmd = ["tmux", "display-message", "-p",
               "#{session_name}"]
        if pane:
            cmd = ["tmux", "display-message",
                   "-t", pane, "-p", "#{session_name}"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _detect_tmux_socket() -> str | None:
    """Auto-detect tmux socket path."""
    tmux_env = os.environ.get("TMUX", "")
    if tmux_env:
        # TMUX env var format: /path/to/socket,pid,session
        parts = tmux_env.split(",")
        if parts:
            return parts[0]
    return None


def _detect_display_addr() -> str | None:
    """Auto-detect full pane address (session:window.pane)."""
    pane = os.environ.get("TMUX_PANE")
    try:
        fmt = ("#{session_name}:#{window_index}."
               "#{pane_index}")
        cmd = ["tmux", "display-message", "-p", fmt]
        if pane:
            cmd = ["tmux", "display-message",
                   "-t", pane, "-p", fmt]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _detect_agent_kind() -> AgentKind:
    """Guess agent kind from parent process or env."""
    # Check if we're inside a Codex session
    ppid_cmdline = ""
    try:
        ppid = os.getppid()
        cmdline_path = f"/proc/{ppid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "r") as f:
                ppid_cmdline = f.read()
    except (OSError, PermissionError):
        pass

    if "codex" in ppid_cmdline.lower():
        return AgentKind.CODEX

    # macOS: use ps
    if not ppid_cmdline:
        try:
            result = subprocess.run(
                ["ps", "-p", str(os.getppid()), "-o",
                 "command="],
                capture_output=True, text=True, timeout=5,
            )
            if "codex" in result.stdout.lower():
                return AgentKind.CODEX
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return AgentKind.CLAUDE


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
    agents = store.list_agents(tmux_session=tmux_session)
    for a in agents:
        if a.pane_id == pane_id:
            return a
    return None


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
    """Auto-start the watcher if not already running."""
    if store.is_watcher_alive():
        return
    # Spawn watcher as a detached background process
    import shutil
    msg_bin = shutil.which("msg")
    if not msg_bin:
        # Try uv run
        uv_bin = shutil.which("uv")
        if uv_bin:
            subprocess.Popen(
                [uv_bin, "run", "msg", "watch"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=os.path.expanduser("~"),
            )
            return
    if msg_bin:
        subprocess.Popen(
            [msg_bin, "watch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


@click.group()
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

    store = _get_store(db=db, local=local)
    ctx.obj["store"] = store
    if ctx.invoked_subcommand != "watch":
        _ensure_watcher_running(store)


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
@click.pass_context
def register(
    ctx: click.Context,
    name: str,
    pane: str | None,
    agent: str | None,
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

    tmux_session = _detect_tmux_session()
    if not tmux_session:
        click.echo(
            "Error: cannot detect tmux session.",
            err=True,
        )
        sys.exit(1)

    agent_kind = (
        AgentKind(agent) if agent
        else _detect_agent_kind()
    )
    tmux_socket = _detect_tmux_socket()
    display_addr = _detect_display_addr()

    result = store.register_agent(
        name=name,
        pane_id=pane_id,
        tmux_session=tmux_session,
        agent_kind=agent_kind,
        tmux_socket=tmux_socket,
        display_addr=display_addr,
        pid=os.getpid(),
        cwd=os.getcwd(),
    )
    click.echo(
        f"Registered as '{name}' "
        f"(session={result.session_id[:8]}..., "
        f"pane={display_addr or pane_id}, "
        f"agent={agent_kind.value})"
    )


@cli.command("list")
@click.pass_context
def list_agents(ctx: click.Context) -> None:
    """List registered agents."""
    store: MsgStore = ctx.obj["store"]
    tmux_session = _detect_tmux_session()
    agents = store.list_agents(tmux_session=tmux_session)

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
@click.pass_context
def send(
    ctx: click.Context,
    to: str,
    body: str,
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

    me = _get_self_agent(store)
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

    msg = store.send_message(
        thread_id=thread.id,
        from_agent=me.session_id,
        body=body,
    )

    if not store.is_watcher_alive():
        click.echo(
            "Warning: no active watcher. "
            "Run 'msg watch' to enable notifications.",
            err=True,
        )

    names_str = ", ".join(recipient_names)
    click.echo(f"Sent to {names_str}: {body}")


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
        me.session_id, thread_id=resolved_id,
    )
    click.echo(f"\n({count} message(s) marked as read)")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Check system status (watcher health, agents)."""
    store: MsgStore = ctx.obj["store"]

    # Watcher health
    watchers = store.get_watcher_info()
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
    agents = store.list_agents(tmux_session=tmux_session)
    click.echo(f"\nAgents: {len(agents)} registered")
    for a in agents:
        seen = _relative_time(a.last_seen)
        click.echo(f"  {a.name} ({a.agent_kind.value}) "
                    f"- last seen {seen}")


@cli.command()
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Start the watcher daemon for notifications."""
    from .watcher import run_watcher
    db_path = ctx.obj["store"].db_path
    click.echo("Starting msg watcher daemon...")
    run_watcher(db_path=db_path)


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
