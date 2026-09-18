"""Run every configured chat front-end (Discord, Mattermost) in one daemon.

Both front-ends share one :class:`~.relay.Relay`, so the concurrency cap,
per-thread locks and idle reaper are global across platforms. A front-end
runs when it is configured: Discord when its token resolves, Mattermost when
``[mattermost] url`` is set.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from .config import TunnelConfig
from .discord_bot import discord_ready, run_discord
from .mattermost_bot import mattermost_ready, run_mattermost
from .registry import Registry
from .relay import Relay
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")


def plan_frontends(cfg: TunnelConfig) -> list[str]:
    """Names of the front-ends ``serve`` will run.

    A configured-but-incomplete Mattermost is skipped with a warning while
    Discord runs; it is an error only when nothing else can run.

    Raises:
        RuntimeError: No front-end can run at all.
    """
    names: list[str] = []
    discord_problem = discord_ready(cfg)
    if discord_problem is None:
        names.append("discord")
    mm_problem = mattermost_ready(cfg) if cfg.mattermost.url else None
    if cfg.mattermost.url and mm_problem is None:
        names.append("mattermost")
    if not names:
        raise RuntimeError(
            mm_problem or discord_problem or "No chat front-end configured"
        )
    if mm_problem:
        # Configured but incomplete (e.g. token not issued yet): keep the
        # working front-end up rather than refuse to serve, but say so loudly.
        logger.warning("Mattermost NOT started: %s", mm_problem)
    return names


async def serve_all(
    cfg: TunnelConfig, store: TunnelStore, registry: Registry
) -> None:
    """Run the configured front-ends until one fails or the task is cancelled."""
    names = plan_frontends(cfg)
    if "discord" not in names:
        logger.info("Discord not started: %s", discord_ready(cfg))
    relay = Relay(cfg, store, registry)
    runners: list[Coroutine[Any, Any, None]] = []
    if "discord" in names:
        runners.append(run_discord(cfg, relay))
    if "mattermost" in names:
        runners.append(run_mattermost(cfg, relay))
    reaper = asyncio.create_task(relay.reaper())
    try:
        await asyncio.gather(*runners)
    finally:
        reaper.cancel()


def run_serve(cfg: TunnelConfig, store: TunnelStore, registry: Registry) -> None:
    """Blocking entry point for ``agent-tunnel serve``."""
    try:
        asyncio.run(serve_all(cfg, store, registry))
    except KeyboardInterrupt:
        pass
