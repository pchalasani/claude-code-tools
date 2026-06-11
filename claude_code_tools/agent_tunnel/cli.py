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
    """agent-tunnel: let teammates talk to your local Claude sessions."""


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
@click.argument("question")
def ask(
    config: Optional[str],
    backend: Optional[str],
    handle: Optional[str],
    session: Optional[str],
    project: Optional[str],
    thread: str,
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
        expert_id, project_dir, hname = _resolve_target(
            cfg, store, handle, session, project
        )
        store.bind(
            thread_key,
            handle=hname,
            expert_session_id=expert_id,
            project_dir=str(project_dir),
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
) -> tuple[str, Path, str]:
    """Resolve (expert_session_id, project_dir, handle_name) for `ask`."""
    if handle:
        rec = Registry(cfg.registry_path).get(handle)
        if rec is None:
            raise click.ClickException(f"No live handle {handle!r}.")
        return rec.session_id, Path(rec.cwd), rec.handle
    project_dir = Path(project or os.getcwd()).expanduser().resolve()
    if session:
        return session, project_dir, "cli"
    latest = find_latest_session(
        project_dir, exclude=store.known_fork_ids(), claude_home=cfg.claude_home
    )
    if latest is None:
        raise click.ClickException(
            f"No Claude session found in {project_dir}. Pass --session, "
            "--handle, or run from a project with a session."
        )
    return latest.stem, project_dir, "cli"


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
        label = f" ({rec.label})" if rec.label and rec.label != rec.handle else ""
        click.echo(f"{rec.handle}{label}: {rec.session_id[:8]} @ {rec.cwd}")


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


def main() -> None:
    """Console-script entry point."""
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
