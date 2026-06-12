"""CLI for agent-tunnel: serve, ask, status, published, forget, init."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click

from .backends import BackendError, make_backend
from .config import (
    DEFAULT_CONFIG_PATH,
    TunnelConfig,
    load_config,
    sample_config,
)
from .registry import Registry
from .session import find_latest_session
from .store import TunnelStore


def _build(
    config: Optional[str],
    backend: Optional[str] = None,
    channel: tuple[int, ...] = (),
    token_env: Optional[str] = None,
) -> TunnelConfig:
    try:
        return load_config(
            path=Path(config) if config else None,
            backend=backend,
            channel_ids=list(channel) or None,
            token_env=token_env,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


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
    "--backend", type=click.Choice(["tmux", "headless"]), default=None
)
@click.option(
    "--channel",
    "channels",
    multiple=True,
    type=int,
    help="Discord channel id to watch (repeatable).",
)
@click.option("--token-env", help="Env var holding the Discord bot token.")
def serve(
    config: Optional[str],
    backend: Optional[str],
    channels: tuple[int, ...],
    token_env: Optional[str],
) -> None:
    """Run the Discord daemon (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = _build(config, backend, channels, token_env)
    store = TunnelStore(cfg.state_path)
    registry = Registry(cfg.registry_path)
    bk = make_backend(cfg, store)
    try:
        from .discord_bot import run_bot
    except ImportError as exc:
        raise click.ClickException(
            f"discord.py is required for serve: {exc}"
        ) from exc
    click.echo(
        f"agent-tunnel: backend={cfg.backend} "
        f"registry={cfg.registry_path} channels={cfg.discord.channel_ids}"
    )
    try:
        run_bot(cfg, bk, store, registry)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


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
@click.argument("question")
def ask(
    config: Optional[str],
    backend: Optional[str],
    handle: Optional[str],
    session: Optional[str],
    project: Optional[str],
    thread: str,
    write: bool,
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
            cfg, store, handle, session, project, write
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
) -> tuple[str, Path, str, str, str]:
    """Resolve (expert_session_id, project_dir, handle, config_dir, access)."""
    if handle:
        rec = Registry(cfg.registry_path).get(handle)
        if rec is None:
            raise click.ClickException(f"No live handle {handle!r}.")
        return rec.session_id, Path(rec.cwd), rec.handle, rec.config_dir, (
            rec.access
        )
    access = "write" if write else "read"
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
        wr = "  ✍️ write" if rec.access == "write" else ""
        click.echo(
            f"{rec.handle}{label}: {rec.session_id[:8]} @ {rec.cwd}{cfgdir}{wr}"
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


@cli.command()
@click.option("--config", type=click.Path(), help="Config TOML path.")
def doctor(config: Optional[str]) -> None:
    """Check that agent-tunnel is configured and ready to serve."""
    import shutil

    from .discord_bot import resolve_token

    cfg = _build(config)
    checks: list[tuple[bool, str]] = [
        (
            bool(resolve_token(cfg)),
            f"Discord token ({cfg.discord.token_env} or token_file)",
        ),
        (
            bool(cfg.discord.channel_ids),
            f"Watched channel(s): {cfg.discord.channel_ids or 'none set'}",
        ),
        (
            shutil.which(cfg.claude.binary) is not None,
            f"claude binary on PATH ({cfg.claude.binary})",
        ),
    ]
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
    click.echo(f"\nbackend={cfg.backend}  registry={cfg.registry_path}")
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
    """Drop thread mappings (and kill their tmux windows)."""
    if bool(thread) == forget_all:
        raise click.ClickException("Use exactly one of --thread or --all.")
    cfg = _build(config, backend)
    store = TunnelStore(cfg.state_path)
    bk = make_backend(cfg, store)
    if forget_all:
        keys = [r.thread_key for r in store.all_records()]
    else:
        keys = [thread] if thread else []
    for key in keys:
        bk.forget(key)
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
  ask / published / status / doctor / forget / init
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
