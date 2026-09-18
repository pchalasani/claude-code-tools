"""Discord front-end for agent-tunnel (handle-opens-a-thread model).

Flow:

- A teammate posts ``<handle> [question]`` in a watched channel. If the
  handle is live in the registry, the bot opens a public thread, binds that
  thread to the published session, and answers (or posts a ready notice).
- Subsequent messages *inside that thread* are follow-ups to the same fork —
  no handle needed.
- Each handle/thread = its own fork; different teammates and different
  sessions never collide.

This module only routes Discord events; the turn itself (locking, attachment
ingest, the backend call, chunking, deliverables) runs in the shared
:class:`~.relay.Relay`. Other bots are always ignored (no loops with a
co-resident bot such as openclaw). ``discord`` is imported lazily so the rest
of the package works without the dependency installed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Optional, Sequence

from .backends import backend_for_record
from .config import TunnelConfig
from .registry import HANDLE_RE
from .relay import (  # noqa: F401  (re-exported for callers and tests)
    Relay,
    _safe_filename,
    _unique_name,
    format_relayed_message,
    is_close_command,
    is_list_command,
    split_chunks,
)

logger = logging.getLogger("agent_tunnel")

DISCORD_MSG_LIMIT = 2000
DISCORD_MAX_FILES = 10
THREAD_NAME_MAX = 90
PLATFORM = "Discord"


def resolve_token(cfg: TunnelConfig) -> str:
    """Resolve the Discord bot token: env var first, then token_file."""
    token = os.environ.get(cfg.discord.token_env, "").strip()
    if not token and cfg.discord.token_file:
        path = Path(cfg.discord.token_file).expanduser()
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
    return token


def _leading_mention_id(content: str) -> Optional[int]:
    """The id of a *leading* Discord mention, or None if there isn't one.

    Returns the user/role id of a leading ``<@id>`` (also ``<@!id>`` nickname
    or ``<@&id>`` role) mention; ``-1`` for a leading ``@everyone``/``@here``
    broadcast; ``None`` when the message doesn't start with a mention. Used in
    threads to silently skip messages addressed to someone other than the bot.
    """
    text = content.lstrip()
    if text.startswith("@everyone") or text.startswith("@here"):
        return -1
    match = re.match(r"<@[!&]?(\d+)>", text)
    return int(match.group(1)) if match else None


class DiscordDest:
    """A Discord channel/thread as a relay :class:`~.relay.Destination`."""

    max_len = DISCORD_MSG_LIMIT
    max_files = DISCORD_MAX_FILES

    def __init__(self, channel: Any) -> None:
        """Wrap a discord.py messageable (thread, DM, text channel)."""
        self.channel = channel

    async def send(self, text: str) -> None:
        """Post one message."""
        await self.channel.send(text)

    async def send_text_file(self, preview: str, name: str, data: bytes) -> None:
        """Post a preview with the full text attached as a file."""
        import discord

        await self.channel.send(
            preview, file=discord.File(io.BytesIO(data), filename=name)
        )

    async def send_files(self, caption: str, paths: Sequence[Path]) -> None:
        """Post local files as attachments."""
        import discord

        files = [discord.File(str(p), filename=p.name) for p in paths]
        await self.channel.send(caption, files=files)

    def typing(self) -> AbstractAsyncContextManager[Any]:
        """Discord's own typing indicator."""
        return self.channel.typing()


def discord_ready(cfg: TunnelConfig) -> Optional[str]:
    """None if the Discord front-end can run, else why it can't."""
    if not resolve_token(cfg):
        return (
            f"No Discord token found (set {cfg.discord.token_env} or "
            "discord.token_file in the config)"
        )
    if not cfg.discord.channel_ids and not cfg.discord.respond_to_dms:
        return (
            "No discord.channel_ids configured and DMs are disabled — "
            "the bot would never respond."
        )
    return None


async def run_discord(cfg: TunnelConfig, relay: Relay) -> None:
    """Run the Discord bot until cancelled.

    Raises:
        RuntimeError: No token, or nothing to watch (see ``discord_ready``).
    """
    import discord  # deferred: keep core importable without discord.py

    problem = discord_ready(cfg)
    if problem:
        raise RuntimeError(problem)
    token = resolve_token(cfg)
    store = relay.store
    registry = relay.registry

    intents = discord.Intents.default()
    intents.message_content = True

    class TunnelClient(discord.Client):
        """Routes channel/thread/DM messages to the relay."""

        async def on_ready(self) -> None:
            logger.info(
                "Discord: logged in as %s; watching channels %s",
                self.user,
                cfg.discord.channel_ids,
            )

        def _allowed(self, author: Any) -> bool:
            uids = cfg.discord.allowed_user_ids
            rids = set(cfg.discord.allowed_role_ids)
            if not uids and not rids:
                return True
            if getattr(author, "id", None) in uids:
                return True
            roles = getattr(author, "roles", []) or []
            return any(role.id in rids for role in roles)

        async def on_message(self, message: discord.Message) -> None:
            if message.author.bot:
                return
            content = (message.content or "").strip()
            # An attachment-only message has empty content but still carries a
            # file for the agent to read — don't drop it.
            if not content and not message.attachments:
                return
            channel = message.channel

            if isinstance(channel, discord.Thread):
                await self._on_thread_message(message, channel, content)
            elif isinstance(channel, discord.DMChannel):
                if cfg.discord.respond_to_dms:
                    await self._on_direct(message, content)
            elif channel.id in cfg.discord.channel_ids:
                await self._on_channel_message(message, content)

        async def _on_channel_message(
            self, message: discord.Message, content: str
        ) -> None:
            """A message in a watched channel: try to open a handle thread."""
            if is_list_command(content):
                if self._allowed(message.author):
                    await message.channel.send(relay.handles_text())
                return
            token, _, remainder = content.partition(" ")
            handle = token.strip().lower()
            rec = registry.get(handle)
            if rec is None:
                # Only complain if it clearly looks like a handle attempt.
                if HANDLE_RE.match(handle) and not remainder:
                    await message.reply(
                        f"No live session for handle `{handle}`. "
                        "Ask the owner to `>share` it."
                    )
                return
            if not self._allowed(message.author):
                return

            question = remainder.strip()
            label = rec.label or rec.handle
            # Lead with the handle (recognizable), then the question so that
            # multiple threads for the same handle stay distinguishable.
            thread_name = (
                f"{label}: {question}" if question else label
            )[:THREAD_NAME_MAX]
            thread = await message.create_thread(name=thread_name)
            logger.info(
                "Opened thread for handle %s (session %s) asked by %s",
                rec.handle,
                rec.session_id[:8],
                message.author.display_name,
            )
            key = f"th:{thread.id}"
            relay.bind(key, rec, message.author.display_name, PLATFORM)
            if question or message.attachments:
                await relay.answer(
                    DiscordDest(thread),
                    key,
                    question,
                    message.attachments,
                    sender=message.author.display_name,
                )
            else:
                await thread.send(
                    f"Connected to **{label}**. Ask your question here; "
                    "follow-ups stay in this thread."
                )

        async def _on_thread_message(
            self, message: discord.Message, thread: Any, content: str
        ) -> None:
            """A follow-up inside a bound thread."""
            thread_key = f"th:{thread.id}"
            if store.get(thread_key) is None:
                return
            if not self._allowed(message.author):
                return
            # A message that opens with @someone-else (or @everyone/@here/a
            # role) is teammates talking among themselves — stay out silently.
            # A leading @bot is fine: strip it and answer. No mention = answer
            # (in a thread you never need to address the bot).
            mention_id = _leading_mention_id(content)
            if mention_id is not None:
                if mention_id != getattr(self.user, "id", None):
                    return
                content = re.sub(r"^\s*<@[!&]?\d+>\s*", "", content)
            if is_list_command(content):
                await thread.send(relay.handles_text())
                return
            if is_close_command(content):
                await relay.close(DiscordDest(thread), thread_key)
                return
            if not relay.cooldown_ok(("discord", message.author.id)):
                await message.add_reaction("⏳")
                return
            await relay.answer(
                DiscordDest(thread),
                thread_key,
                content,
                message.attachments,
                sender=message.author.display_name,
            )

        async def _on_direct(
            self, message: discord.Message, content: str
        ) -> None:
            """DM handling: `<handle> ...` (re)binds; bare text follows up."""
            thread_key = f"dm:{message.channel.id}"
            dest = DiscordDest(message.channel)
            if is_list_command(content):
                if self._allowed(message.author):
                    await dest.send(relay.handles_text())
                return
            if is_close_command(content) and store.get(thread_key) is not None:
                if self._allowed(message.author):
                    await relay.close(dest, thread_key)
                return
            token, _, remainder = content.partition(" ")
            handle = token.strip().lower()
            rec = registry.get(handle)
            if rec is not None:
                # Rebinding this DM starts a fresh thread; fully tear down any
                # previous binding first so its uploads/outbox (and live tmux
                # window) don't leak into the new handle's fork — the upload
                # dir is keyed only by the DM channel and would otherwise be
                # reused across handles.
                #
                # Hold the thread lock around forget+bind so the rebind can't
                # race a still-running turn on the OLD binding: that turn holds
                # this same lock, and its trailing upsert() (which merges fork/
                # window into whatever record now owns thread_key) would
                # otherwise attach the old fork to the new handle. The lock is
                # released here before answer() re-acquires it below — it is
                # not reentrant — so the new turn simply queues behind the old.
                async with relay.locks[thread_key]:
                    existing = store.get(thread_key)
                    if existing is not None:
                        await asyncio.to_thread(
                            backend_for_record(cfg, store, existing).forget,
                            thread_key,
                        )
                    relay.bind(
                        thread_key, rec, message.author.display_name, PLATFORM
                    )
                content = remainder.strip()
                if not content and not message.attachments:
                    await dest.send(
                        f"Connected to **{rec.label or rec.handle}**."
                    )
                    return
            elif store.get(thread_key) is None:
                await dest.send(
                    "Start with a handle, e.g. `pay-7Q2 your question`."
                )
                return
            if not self._allowed(message.author):
                return
            if not relay.cooldown_ok(("discord", message.author.id)):
                await message.add_reaction("⏳")
                return
            await relay.answer(
                dest,
                thread_key,
                content,
                message.attachments,
                sender=message.author.display_name,
            )

    client = TunnelClient(intents=intents)
    async with client:
        await client.start(token)
