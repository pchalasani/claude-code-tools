"""CLI for agent-tunnel: serve, ask, published, forks, resume, status, watch,
doctor, forget, init, help."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click

from .backends import (
    Backend,
    BackendError,
    _window_name,
    backend_for_record,
    make_backend,
)
from .config import (
    DEFAULT_CONFIG_PATH,
    TunnelConfig,
    load_config,
    sample_config,
)
from .registry import Registry
from .session import count_turns, find_latest_session, transcript_dir
from .store import ThreadRecord, TunnelStore


def _build(
    config: Optional[str],
    backend: Optional[str] = None,
    channel: tuple[str, ...] = (),
    token_env: Optional[str] = None,
    chat: Optional[str] = None,
) -> TunnelConfig:
    """Load config + apply CLI overrides, coercing --channel per front-end.

    ``--channel`` is a string on the CLI; Slack ids stay strings while Discord
    ids are cast to int (snowflakes) with a clear error. ``--token-env`` is
    Discord-only (Slack uses two tokens, set in config).
    """
    try:
        cfg = load_config(
            path=Path(config) if config else None,
            backend=backend,
            chat=chat,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if channel:
        if cfg.chat == "slack":
            cfg.slack.channel_ids = list(channel)
        else:
            try:
                cfg.discord.channel_ids = [int(c) for c in channel]
            except ValueError as exc:
                raise click.ClickException(
                    f"Discord --channel ids must be integers: {exc}"
                ) from exc
    if token_env:
        cfg.discord.token_env = token_env  # Discord-only (Slack has two tokens)
    return cfg


@click.group()
def cli() -> None:
    """agent-tunnel: let teammates talk to your local Claude sessions.

    Publish a session from inside it by typing `>share` (the agent-tunnel
    plugin), hand the resulting handle to colleagues, and run
    `agent-tunnel serve` so they can ask it questions in Discord — each
    answered against a read-only fork. Run `agent-tunnel help` for the full
    rundown of every command and where to run it.
    """


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
@click.option(
    "--backend",
    type=click.Choice(["tmux", "headless"]),
    default=None,
    help="Server mode: headless (default) or tmux. Overrides config.",
)
@click.option(
    "--chat",
    type=click.Choice(["discord", "slack"]),
    default=None,
    help="Chat front-end (default from config; falls back to discord).",
)
@click.option(
    "--channel",
    "channels",
    multiple=True,
    type=str,
    help="Channel id to watch (repeatable; int for discord, string for slack).",
)
@click.option("--token-env", help="Env var holding the Discord bot token.")
def serve(
    config: Optional[str],
    backend: Optional[str],
    chat: Optional[str],
    channels: tuple[str, ...],
    token_env: Optional[str],
) -> None:
    """Run the chat daemon (blocking) for Discord or Slack.

    Pick the front-end with --chat or [tunnel] chat (default discord). Two
    server modes, set with --backend or [tunnel] backend (default headless):

    \b
    - headless: a `claude -p` subprocess per question — clean JSON I/O, more
      reliable, no tmux. Launch with `agent-tunnel serve`.
    - tmux: an interactive `claude` per thread in a private tmux server you can
      watch live (`agent-tunnel watch`). Launch:
      `agent-tunnel serve --backend tmux`.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = _build(config, backend, channels, token_env, chat)
    store = TunnelStore(cfg.state_path)
    registry = Registry(cfg.registry_path)
    if cfg.chat == "slack":
        try:
            from .slack_bot import run_slack_bot as run
        except ImportError as exc:
            raise click.ClickException(
                f"slack_bolt is required for `serve --chat slack`: {exc}\n"
                "Install it with:  uv tool install 'claude-code-tools[slack]'"
            ) from exc
        watched = cfg.slack.channel_ids
    else:
        try:
            from .discord_bot import run_bot as run
        except ImportError as exc:
            raise click.ClickException(
                f"discord.py is required for serve: {exc}"
            ) from exc
        watched = cfg.discord.channel_ids
    click.echo(
        f"agent-tunnel: chat={cfg.chat} backend={cfg.backend} "
        f"registry={cfg.registry_path} channels={watched}"
    )
    try:
        run(cfg, store, registry)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    except ImportError as exc:
        # slack_bolt is imported lazily INSIDE run_slack_bot, so a missing
        # `slack` extra surfaces here at call time (not at the import above) —
        # convert it to the actionable install hint, not a raw traceback (P2).
        if cfg.chat == "slack":
            raise click.ClickException(
                f"slack_bolt is required for `serve --chat slack`: {exc}\n"
                "Install it with:  uv tool install 'claude-code-tools[slack]'"
            ) from exc
        raise click.ClickException(
            f"A required dependency for serve is missing: {exc}"
        ) from exc


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
@click.option(
    "--backend", type=click.Choice(["tmux", "headless"]), default=None
)
@click.option("--handle", help="Published handle to target (via registry).")
@click.option("--session", help="Expert session id (bypasses the registry).")
@click.option(
    "--project",
    type=click.Path(),
    help="Project dir of that session (default: cwd).",
)
@click.option(
    "--thread",
    default="local-test",
    show_default=True,
    help="Thread key (reuse to test follow-up continuity).",
)
@click.option(
    "--write", is_flag=True, help="Grant write access (with --session/auto)."
)
@click.option(
    "--dangerously-allow-bash",
    "allow_bash",
    is_flag=True,
    help="Grant write + shell execution (with --session/auto).",
)
@click.option(
    "--dangerously-skip-permissions",
    "skip_perms",
    is_flag=True,
    help="Grant FULL access (skip-permissions; needs allow_skip_permissions).",
)
@click.argument("question")
def ask(
    config: Optional[str],
    backend: Optional[str],
    handle: Optional[str],
    session: Optional[str],
    project: Optional[str],
    thread: str,
    write: bool,
    allow_bash: bool,
    skip_perms: bool,
    question: str,
) -> None:
    """Ask one question through the full pipeline (no Discord).

    Target resolution: --handle (registry) > --session/--project >
    auto (newest session in the project dir / cwd).
    """
    cfg = _build(config, backend)
    store = TunnelStore(cfg.state_path)
    bk = make_backend(cfg, store)
    thread_key = f"cli:{thread}"

    if store.get(thread_key) is None:
        expert_id, project_dir, hname, config_dir, access = _resolve_target(
            cfg, store, handle, session, project, write, allow_bash, skip_perms
        )
        store.bind(
            thread_key,
            handle=hname,
            expert_session_id=expert_id,
            project_dir=str(project_dir),
            config_dir=config_dir,
            access=access,
            backend=cfg.backend,
            asker="cli",
        )
    try:
        answer = bk.ask(thread_key, question)
    except BackendError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(answer.text)
    click.echo(
        f"\n[fork={answer.fork_session_id} "
        f"{'new' if answer.new_thread else 'follow-up'}]",
        err=True,
    )


def _resolve_target(
    cfg: TunnelConfig,
    store: TunnelStore,
    handle: Optional[str],
    session: Optional[str],
    project: Optional[str],
    write: bool = False,
    allow_bash: bool = False,
    skip_perms: bool = False,
) -> tuple[str, Path, str, str, str]:
    """Resolve (expert_session_id, project_dir, handle, config_dir, access)."""
    if handle:
        rec = Registry(cfg.registry_path).get(handle)
        if rec is None:
            raise click.ClickException(f"No live handle {handle!r}.")
        return rec.session_id, Path(rec.cwd), rec.handle, rec.config_dir, (
            rec.access
        )
    access = (
        "all"
        if skip_perms
        else "bash" if allow_bash else "write" if write else "read"
    )
    project_dir = Path(project or os.getcwd()).expanduser().resolve()
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if session:
        return session, project_dir, "cli", env_dir, access
    latest = find_latest_session(
        project_dir, exclude=store.known_fork_ids(), claude_home=cfg.claude_home
    )
    if latest is None:
        raise click.ClickException(
            f"No Claude session found in {project_dir}. Pass --session, "
            "--handle, or run from a project with a session."
        )
    sp = str(latest)
    cfg_dir = sp.split("/projects/")[0] if "/projects/" in sp else env_dir
    return latest.stem, project_dir, "cli", cfg_dir, access


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
def published(config: Optional[str]) -> None:
    """List live published handles (from the registry)."""
    cfg = _build(config)
    recs = Registry(cfg.registry_path).active()
    if not recs:
        click.echo("No live handles. Type >share inside a session to add one.")
        return
    for rec in recs:
        label = (
            f" ({rec.label})" if rec.label and rec.label != rec.handle else ""
        )
        cfgdir = f"  [{Path(rec.config_dir).name}]" if rec.config_dir else ""
        access = {
            "write": "  ✍️ write",
            "bash": "  💥 bash",
            "all": "  🚨 all",
        }.get(rec.access, "")
        click.echo(
            f"{rec.handle}{label}: {rec.session_id[:8]} @ {rec.cwd}{cfgdir}"
            f"{access}"
        )


def _relative_time(ts: float) -> str:
    """Human 'time ago' for an epoch timestamp."""
    secs = max(0.0, time.time() - ts)
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs / 60)}m ago"
    if secs < 129600:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


def _forks(
    store: TunnelStore, handle: Optional[str] = None
) -> list[ThreadRecord]:
    """Bound threads that have a fork, newest activity first."""
    recs = [
        r
        for r in store.all_records()
        if r.fork_session_id and (handle is None or r.handle == handle)
    ]
    recs.sort(key=lambda r: r.last_used, reverse=True)
    return recs


def _fork_path(rec: ThreadRecord) -> Optional[Path]:
    """Locate a fork's transcript file (under its own config dir)."""
    home = Path(rec.config_dir) if rec.config_dir else None
    path = transcript_dir(Path(rec.project_dir), home) / (
        f"{rec.fork_session_id}.jsonl"
    )
    return path if path.exists() else None


def _fork_line(rec: ThreadRecord, marker: str = "") -> str:
    fp = _fork_path(rec)
    turns = count_turns(fp) if fp else 0
    cfgdir = f" [{Path(rec.config_dir).name}]" if rec.config_dir else ""
    return (
        f"{marker}{rec.handle}{cfgdir}  asker={rec.asker or '?'}  "
        f"{_relative_time(rec.last_used)}  {turns} turns  "
        f"fork={rec.fork_session_id[:8]}"
    )


# Per-access-level (label, rich style) for the forks table / picker.
_ACCESS_DISPLAY = {
    "read": ("read", "dim"),
    "write": ("✍️ write", "yellow"),
    "bash": ("💥 bash", "red"),
    "all": ("🚨 all", "bold red"),
}


def _fork_status(backend: Backend, rec: ThreadRecord) -> str:
    """'live' if the fork's tmux window is up, 'idle' if reaped, '-' if n/a."""
    tmux = getattr(backend, "tmux", None)
    if tmux is None or rec.backend != "tmux":
        return "-"
    if not rec.tmux_window:
        return "idle"
    try:
        if tmux.window_alive(rec.tmux_window) and not tmux.pane_dead(
            rec.tmux_window
        ):
            return "live"
    except Exception:
        pass
    return "idle"


def _fork_row(rec: ThreadRecord, status: str) -> dict[str, str]:
    """Display fields for one fork (shared by the table, JSON, and picker)."""
    fp = _fork_path(rec)
    turns = count_turns(fp) if fp else 0
    cfgdir = Path(rec.config_dir).name if rec.config_dir else ""
    proj = Path(rec.project_dir).name if rec.project_dir else "?"
    return {
        "thread_key": rec.thread_key,
        "handle": rec.handle or "?",
        "access": rec.access or "read",
        "asker": rec.asker or "?",
        "last_active": _relative_time(rec.last_used),
        "turns": str(turns),
        "status": status,
        "project": f"{proj} [{cfgdir}]" if cfgdir else proj,
        "fork": rec.fork_session_id[:8],
    }


def _status_cell(status: str) -> str:
    if status == "live":
        return "[green]● live[/]"
    if status == "idle":
        return "[dim]○ idle[/]"
    return "[dim]—[/]"


def _render_forks_table(rows: list[dict[str, str]]) -> None:
    """Pretty-print fork rows as a rich table."""
    from rich.console import Console
    from rich.table import Table

    table = Table(
        title="agent-tunnel forks",
        title_justify="left",
        title_style="bold",
        header_style="bold",
    )
    table.add_column("Handle", style="cyan", no_wrap=True)
    table.add_column("Access", no_wrap=True)
    table.add_column("Asker")
    table.add_column("Last active", no_wrap=True)
    table.add_column("Turns", justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Project", style="dim")
    table.add_column("Fork", style="dim", no_wrap=True)
    for r in rows:
        label, style = _ACCESS_DISPLAY.get(r["access"], (r["access"], ""))
        access = f"[{style}]{label}[/]" if style else label
        table.add_row(
            r["handle"],
            access,
            r["asker"],
            r["last_active"],
            r["turns"],
            _status_cell(r["status"]),
            r["project"],
            r["fork"],
        )
    Console().print(table)


def _manage_forks(
    cfg: TunnelConfig, store: TunnelStore, rows: list[dict[str, str]]
) -> None:
    """Interactively select forks and clear (forget) them."""
    import questionary

    choices = []
    for r in rows:
        label = _ACCESS_DISPLAY.get(r["access"], (r["access"], ""))[0]
        title = (
            f"{r['handle']:<16} {label:<9} {r['asker']:<12} "
            f"{r['last_active']:<10} {r['turns']:>3}t  {r['status']:<4} "
            f"{r['fork']}"
        )
        choices.append(questionary.Choice(title=title, value=r["thread_key"]))
    selected = questionary.checkbox(
        "Select forks to clear (space toggles, enter confirms):",
        choices=choices,
    ).ask()
    if not selected:
        click.echo("Nothing selected.")
        return
    if not questionary.confirm(
        f"Clear {len(selected)} fork(s)? This kills their windows and drops "
        "the bindings (the transcripts stay on disk).",
        default=False,
    ).ask():
        click.echo("Cancelled.")
        return
    cache: dict[str, Backend] = {}
    for key in selected:
        try:
            rec = store.get(key)
            backend_for_record(cfg, store, rec, cache).forget(key)
            click.echo(f"Cleared {key}")
        except Exception as exc:
            click.echo(f"Failed to clear {key}: {exc}", err=True)


@cli.command()
@click.argument("handle", required=False)
@click.option("--config", type=click.Path(), help="Config TOML path.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON.")
@click.option(
    "--manage",
    "-m",
    is_flag=True,
    help="Interactively select forks to clear (needs a TTY).",
)
def forks(
    handle: Optional[str], config: Optional[str], as_json: bool, manage: bool
) -> None:
    """List fork sessions (one per Discord thread) as a table.

    Optionally filter by HANDLE. `--json` prints machine-readable rows;
    `--manage` lets you select forks to clear. Resume one with
    `agent-tunnel resume`.
    """
    cfg = _build(config)
    store = TunnelStore(cfg.state_path)
    recs = _forks(store, handle)
    if not recs:
        where = f" for handle {handle!r}" if handle else ""
        click.echo("[]" if as_json else f"No fork sessions{where} yet.")
        return
    cache: dict[str, Backend] = {}
    rows = [
        _fork_row(
            rec, _fork_status(backend_for_record(cfg, store, rec, cache), rec)
        )
        for rec in recs
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if manage:
        if not sys.stdin.isatty():
            raise click.ClickException(
                "--manage needs an interactive terminal."
            )
        _manage_forks(cfg, store, rows)
        return
    _render_forks_table(rows)


@cli.command()
@click.argument("handle")
@click.option(
    "--fork",
    "fork_id",
    default=None,
    help="Resume a specific fork id (a prefix is fine).",
)
@click.option("--config", type=click.Path(), help="Config TOML path.")
def resume(
    handle: str, fork_id: Optional[str], config: Optional[str]
) -> None:
    """Resume a colleague-accumulated fork for HANDLE in Claude Code.

    One fork: resumes it. Several: resumes the most recent and lists the
    alternatives (pick one with --fork <id>).
    """
    cfg = _build(config)
    store = TunnelStore(cfg.state_path)
    recs = _forks(store, handle)
    if not recs:
        raise click.ClickException(
            f"No fork sessions for handle {handle!r}."
        )

    if fork_id:
        matches = [r for r in recs if r.fork_session_id.startswith(fork_id)]
        if not matches:
            raise click.ClickException(
                f"No fork id starting with {fork_id!r} for {handle!r}."
            )
        chosen = matches[0]
    else:
        chosen = recs[0]
        if len(recs) > 1:
            click.echo(
                f"handle {handle!r} has {len(recs)} forks "
                "(resuming the most recent, ←):",
                err=True,
            )
            for rec in recs:
                mark = "  ← " if rec is chosen else "    "
                click.echo(_fork_line(rec, marker=mark), err=True)
            click.echo(
                f"  pick another:  agent-tunnel resume {handle} --fork <id>",
                err=True,
            )

    env = {**os.environ}
    if chosen.config_dir:
        env["CLAUDE_CONFIG_DIR"] = chosen.config_dir
    try:
        os.chdir(chosen.project_dir)
    except OSError as exc:
        raise click.ClickException(
            f"Cannot enter {chosen.project_dir}: {exc}"
        )
    os.execvpe(
        cfg.claude.binary,
        [cfg.claude.binary, "--resume", chosen.fork_session_id],
        env,
    )


@cli.command()
@click.argument("old")
@click.argument("new")
@click.option("--config", type=click.Path(), help="Config TOML path.")
def rename(old: str, new: str, config: Optional[str]) -> None:
    """Rename a shared handle OLD to NEW.

    Updates the registry, the bound fork records, and any live tmux windows,
    so colleagues address NEW and `published`/`forks`/`resume` all agree. Works
    out-of-session (no need to be in the Claude session).
    """
    cfg = _build(config)
    registry = Registry(cfg.registry_path)
    ok, msg = registry.rename(old, new)
    if not ok:
        raise click.ClickException(msg)
    old_h, new_h = old.strip().lower(), new.strip().lower()
    store = TunnelStore(cfg.state_path)
    renamed = store.rename_handle(old_h, new_h)
    windows = 0
    # Rename live tmux windows for records that actually run on the tmux
    # backend — keyed on each record's own backend, not the current config
    # (which defaults to headless even when `serve --backend tmux` is live).
    tmux_recs = [r for r in renamed if r.backend == "tmux" and r.tmux_window]
    if tmux_recs:
        from .tmux import TmuxSession

        tmux = TmuxSession(cfg.tmux_session)
        for rec in tmux_recs:
            new_win = _window_name(new_h, rec.thread_key)
            if tmux.rename_window(rec.tmux_window, new_win):
                rec.tmux_window = new_win
                store.upsert(rec)
                windows += 1
    suffix = f", {windows} live window(s)" if windows else ""
    click.echo(
        f"Renamed '{old_h}' → '{new_h}' "
        f"(registry, {len(renamed)} fork(s){suffix})."
    )


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
def watch(config: Optional[str]) -> None:
    """Attach to the private tmux server to watch live fork sessions.

    Each colleague thread is a window. Works even from inside your main tmux
    (it attaches to agent-tunnel's separate server). Detach with prefix-d.
    """
    import subprocess

    cfg = _build(config)
    socket = cfg.tmux_session
    probe = subprocess.run(
        ["tmux", "-L", socket, "has-session", "-t", f"={socket}"],
        capture_output=True,
    )
    if probe.returncode != 0:
        raise click.ClickException(
            f"No live fork sessions on tmux server '{socket}' yet — they "
            "appear after the first question (and only with the tmux backend)."
        )
    if os.environ.get("TMUX"):
        click.echo(
            "Note: you're inside tmux, so this attaches NESTED. Detach with "
            "your prefix then 'd'. For a cleaner view, run `agent-tunnel "
            "watch` from a plain terminal tab instead.",
            err=True,
        )
    # Drop $TMUX so tmux allows attaching from within your main session.
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    os.execvpe(
        "tmux", ["tmux", "-L", socket, "attach", "-t", socket], env
    )


def _slack_auth_ready(bot_token: str) -> tuple[bool, str]:
    """Best-effort Slack auth.test readiness line (never raises).

    Returns (ok, label). Reports the resolved bot_user_id on success, or a
    short reason (missing token, slack_sdk absent, or the API error) otherwise.
    """
    if not bot_token:
        return False, "Slack auth.test (no bot token to check)"
    try:
        # slack_sdk is the optional `slack` extra (not installed in the
        # type-check env, so `# type: ignore`); the except handles its real
        # absence at runtime.
        from slack_sdk import WebClient  # type: ignore
    except ImportError:
        return False, "Slack auth.test (install claude-code-tools[slack])"
    try:
        resp = WebClient(token=bot_token).auth_test()
        return True, f"Slack auth.test ok (bot user {resp['user_id']})"
    except Exception as exc:  # network / invalid token / scope issues
        return False, f"Slack auth.test failed ({exc})"


def _reach_check(
    channel_ids: list, respond_to_dms: bool
) -> tuple[bool, str]:
    """Readiness of the 'where can it answer' check (channels OR DMs).

    DM-only (``respond_to_dms`` with no channels) is a valid serve config for
    both front-ends -- it mirrors ``run_bot`` / ``run_slack_bot``'s startup
    guard -- so doctor must not fail solely on an empty channel list (Codex P3).
    """
    where = (
        str(list(channel_ids))
        if channel_ids
        else ("DMs only" if respond_to_dms else "none set")
    )
    return (
        bool(channel_ids) or bool(respond_to_dms),
        f"Watched channel(s) / DMs: {where}",
    )


def _doctor_chat_checks(cfg: TunnelConfig) -> list[tuple[bool, str]]:
    """Front-end-specific doctor checks (tokens + watched channels).

    Slack reports BOTH tokens (bot + app) as separate lines plus an auth.test
    readiness line; Discord keeps its single-token + channels pair.
    """
    if cfg.chat == "slack":
        from .chat_types import resolve_token

        bot_token = resolve_token(
            cfg.slack.bot_token_env, cfg.slack.bot_token_file
        )
        app_token = resolve_token(
            cfg.slack.app_token_env, cfg.slack.app_token_file
        )
        return [
            (
                bool(bot_token),
                f"Slack bot token ({cfg.slack.bot_token_env} or "
                "bot_token_file)",
            ),
            (
                bool(app_token),
                f"Slack app-level token ({cfg.slack.app_token_env} or "
                "app_token_file)",
            ),
            _slack_auth_ready(bot_token),
            _reach_check(cfg.slack.channel_ids, cfg.slack.respond_to_dms),
        ]
    from .discord_bot import resolve_token

    return [
        (
            bool(resolve_token(cfg)),
            f"Discord token ({cfg.discord.token_env} or token_file)",
        ),
        _reach_check(cfg.discord.channel_ids, cfg.discord.respond_to_dms),
    ]


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
def doctor(config: Optional[str]) -> None:
    """Check that agent-tunnel is configured and ready to serve."""
    import shutil

    from .convert import detect_converter

    cfg = _build(config)
    checks: list[tuple[bool, str]] = _doctor_chat_checks(cfg)
    checks.append(
        (
            shutil.which(cfg.claude.binary) is not None,
            f"claude binary on PATH ({cfg.claude.binary})",
        )
    )
    if cfg.backend == "tmux":
        checks.append(
            (shutil.which("tmux") is not None, "tmux on PATH (tmux backend)")
        )
    ok_all = True
    for ok, label in checks:
        click.echo(f"  {'✓' if ok else '✗'} {label}")
        ok_all = ok_all and ok
    n = len(Registry(cfg.registry_path).active())
    click.echo(f"  • {n} published session(s) live")
    if cfg.attachments.convert == "off":
        conv = "off (config)"
    else:
        conv = detect_converter() or "none — colleagues should attach PDFs"
    click.echo(f"  • Office-attachment converter: {conv}")
    click.echo(
        f"\nchat={cfg.chat}  backend={cfg.backend}  "
        f"registry={cfg.registry_path}"
    )
    if not ok_all:
        raise click.ClickException("Some checks failed — see above.")


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
def status(config: Optional[str]) -> None:
    """Show known threads and their forks."""
    cfg = _build(config)
    store = TunnelStore(cfg.state_path)
    records = sorted(store.all_records(), key=lambda r: r.last_used)
    if not records:
        click.echo("No threads yet.")
        return
    now = time.time()
    for rec in records:
        idle_min = (now - rec.last_used) / 60
        fork = rec.fork_session_id[:8] if rec.fork_session_id else "pending"
        window = f" window={rec.tmux_window}" if rec.tmux_window else ""
        click.echo(
            f"{rec.thread_key}: handle={rec.handle or '-'} fork={fork} "
            f"backend={rec.backend} asker={rec.asker or '-'} "
            f"idle={idle_min:.0f}m{window}"
        )


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
@click.option(
    "--backend", type=click.Choice(["tmux", "headless"]), default=None
)
@click.option("--thread", help="Thread key to forget.")
@click.option("--all", "forget_all", is_flag=True, help="Forget everything.")
def forget(
    config: Optional[str],
    backend: Optional[str],
    thread: Optional[str],
    forget_all: bool,
) -> None:
    """Drop thread mappings (and kill their tmux windows).

    Known limitation: this runs in a separate process from the daemon, so it
    does not coordinate with an in-flight turn. Running it while the daemon is
    mid-answer for a thread can delete that thread's upload/outbox dirs or kill
    its window before the turn finishes — the in-process per-thread lock that
    guards the Discord ``!done`` path does not span processes. Prefer ``!done``
    in-thread, or run ``forget`` when the thread is idle; fully closing this
    would need a cross-process per-turn lock.
    """
    if bool(thread) == forget_all:
        raise click.ClickException("Use exactly one of --thread or --all.")
    cfg = _build(config, backend)
    store = TunnelStore(cfg.state_path)
    if forget_all:
        keys = [r.thread_key for r in store.all_records()]
    else:
        keys = [thread] if thread else []
    cache: dict[str, Backend] = {}
    for key in keys:
        rec = store.get(key)
        backend_for_record(cfg, store, rec, cache).forget(key)
        click.echo(f"Forgot {key}")
    if not keys:
        click.echo("Nothing to forget.")


@cli.command()
@click.option(
    "--path",
    type=click.Path(),
    default=str(DEFAULT_CONFIG_PATH),
    show_default=True,
)
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
def init(path: str, force: bool) -> None:
    """Write a commented sample config file."""
    dest = Path(path)
    if dest.exists() and not force:
        raise click.ClickException(
            f"{dest} exists — use --force to overwrite."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sample_config(), encoding="utf-8")
    click.echo(f"Wrote {dest}")


WHERE_TO_RUN = """\
Where to run each command:
  serve      anywhere it stays alive (a tmux pane is convenient; it drives
             its own private tmux server, so it never touches your main one)
  watch      best from a PLAIN terminal tab (outside tmux) to avoid nesting;
             works inside tmux too, just with awkward prefix keys
  >share     inside the Claude Code session you want to publish (it's a hook
             from the agent-tunnel plugin, not a subcommand)
  resume     a normal terminal where you want the Claude session — it execs
             `claude --resume` and drops you into the fork
  ask / published / status / forks / rename / doctor / forget / init
             plain CLI — run anywhere, tmux context does not matter
"""


@cli.command(name="help")
@click.pass_context
def help_cmd(ctx: click.Context) -> None:
    """Show extensive help: overview, where to run each command, and details."""
    parent = ctx.parent or ctx
    click.echo(parent.get_help())
    click.echo("\n" + WHERE_TO_RUN)
    click.echo("=" * 70)
    click.echo("Per-command details (also: agent-tunnel <command> --help)")
    click.echo("=" * 70)
    for name, cmd in sorted(cli.commands.items()):
        if name == "help":
            continue
        sub = click.Context(cmd, info_name=name, parent=parent)
        click.echo("\n" + cmd.get_help(sub))


def main() -> None:
    """Console-script entry point."""
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
