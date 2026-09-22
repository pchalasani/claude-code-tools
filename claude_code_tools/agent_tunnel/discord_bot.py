"""Discord front-end for agent-tunnel (handle-opens-a-thread model).

This module is now a thin **adapter** over the platform-neutral
:class:`~.chat_core.ChatCore`: it owns only the Discord wire-format. It drops
bot messages, classifies each message into a :class:`~.chat_types.Surface`,
normalizes it into an :class:`~.chat_types.IncomingMessage` (resolving the
leading ``<@id>`` mention into an :class:`~.chat_types.Addressee` and stripping a
leading self-mention), implements :class:`~.chat_types.ChatTransport` over
``discord.py``, and forwards each message to the core. All routing/policy/
pipeline logic lives in ``chat_core``.

``discord`` is imported lazily so the rest of the package (and the re-exported
pure helpers below) work without the dependency installed.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional

from .chat_core import ChatCore
from .chat_types import (
    DISCORD_MSG_LIMIT as DISCORD_MSG_LIMIT,
    THREAD_NAME_MAX as THREAD_NAME_MAX,
    Addressee,
    AttachmentRef,
    ChatDest,
    ChatTransport,
    IncomingMessage,
    Surface,
    TransportLimits,
    format_relayed_message as format_relayed_message,
    is_close_command as is_close_command,
    is_list_command as is_list_command,
    resolve_token as _resolve_token,
    split_chunks as split_chunks,
    _safe_filename as _safe_filename,
    _unique_name as _unique_name,
)
from .config import TunnelConfig
from .registry import Registry
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")


def resolve_token(cfg: TunnelConfig) -> str:
    """Resolve the Discord bot token: env var first, then token_file.

    A thin wrapper over the generalized :func:`chat_types.resolve_token` so the
    daemon/doctor and the existing test keep their one-argument call.
    """
    return _resolve_token(cfg.discord.token_env, cfg.discord.token_file)


def _leading_mention_id(content: str) -> Optional[int]:
    """The id of a *leading* Discord mention, or None if there isn't one.

    Returns the user/role id of a leading ``<@id>`` (also ``<@!id>`` nickname or
    ``<@&id>`` role) mention; ``-1`` for a leading ``@everyone``/``@here``
    broadcast; ``None`` when the message doesn't start with a mention. Used in
    threads to silently skip messages addressed to someone other than the bot.
    """
    text = content.lstrip()
    if text.startswith("@everyone") or text.startswith("@here"):
        return -1
    match = re.match(r"<@[!&]?(\d+)>", text)
    return int(match.group(1)) if match else None


def _discord_target(native: Any) -> Any:
    """The Discord object to ``.send``/``.typing`` on for a dest's native.

    A channel/thread/DM channel is Messageable (has ``.send``) and is used
    directly; a ``Message`` (carried by an incoming dest) routes through its
    ``.channel`` (the watched channel, the thread, or the DM channel).
    """
    return native if hasattr(native, "send") else native.channel


class DiscordTransport(ChatTransport):
    """``ChatTransport`` over a live ``discord.py`` client/objects."""

    def __init__(self, client: Any) -> None:
        """Keep the client (its objects carry the real send targets)."""
        self._client = client

    @property
    def limits(self) -> TransportLimits:
        """Discord's caps (the defaults reproduce the shipped numbers)."""
        return TransportLimits()

    async def open_thread(self, parent: ChatDest, title: str) -> ChatDest:
        """Open a public thread anchored to the triggering message."""
        thread = await parent._native.create_thread(name=title)
        return ChatDest(
            thread_key=f"th:{thread.id}",
            surface=Surface.THREAD,
            channel_id=str(thread.id),
            _native=thread,
        )

    async def send_text(self, dest: ChatDest, text: str) -> None:
        """Send plain text (Discord renders markdown; no translation)."""
        await _discord_target(dest._native).send(text)

    async def send_files(
        self, dest: ChatDest, caption: str, files: Any
    ) -> None:
        """Send ``caption`` plus one or more files in a single message."""
        import discord

        dfiles = [
            discord.File(io.BytesIO(of.data), filename=of.filename)
            if of.data is not None
            else discord.File(str(of.path), filename=of.filename)
            for of in files
        ]
        await _discord_target(dest._native).send(caption, files=dfiles)

    async def download_attachment(
        self, attachment: AttachmentRef, target: Any
    ) -> None:
        """Save an inbound Discord attachment to ``target``."""
        await attachment._native.save(str(target))

    async def add_reaction(
        self, message: IncomingMessage, emoji: str
    ) -> None:
        """React to the triggering message (maps ``hourglass`` -> ⏳)."""
        glyph = {"hourglass": "⏳"}.get(emoji, emoji)
        await message._native.add_reaction(glyph)

    def activity(self, dest: ChatDest) -> Any:
        """The Discord typing indicator for the dest's channel/thread."""
        return _discord_target(dest._native).typing()


def run_bot(
    cfg: TunnelConfig,
    store: TunnelStore,
    registry: Registry,
) -> None:
    """Run the Discord bot until interrupted (blocking).

    Raises:
        RuntimeError: Token env var unset, or no channels configured.
    """
    import discord  # deferred: keep core importable without discord.py

    token = resolve_token(cfg)
    if not token:
        raise RuntimeError(
            f"No Discord token found (set {cfg.discord.token_env} or "
            "discord.token_file in the config)"
        )
    if not cfg.discord.channel_ids and not cfg.discord.respond_to_dms:
        raise RuntimeError(
            "No discord.channel_ids configured and DMs are disabled — "
            "the bot would never respond."
        )

    intents = discord.Intents.default()
    intents.message_content = True

    class TunnelClient(discord.Client):
        """Normalizes Discord events and forwards them to the ChatCore."""

        async def setup_hook(self) -> None:
            self.tx = DiscordTransport(self)
            self.core = ChatCore(cfg, store, registry, self.tx)
            self.loop.create_task(self.core.run_reaper())

        async def on_ready(self) -> None:
            logger.info(
                "Logged in as %s; watching channels %s",
                self.user,
                cfg.discord.channel_ids,
            )

        async def on_message(self, m: discord.Message) -> None:
            if m.author.bot:
                return
            ch = m.channel
            if isinstance(ch, discord.Thread):
                surface, key = Surface.THREAD, f"th:{ch.id}"
            elif isinstance(ch, discord.DMChannel):
                if not cfg.discord.respond_to_dms:
                    return
                surface, key = Surface.DM, f"dm:{ch.id}"
            elif ch.id in cfg.discord.channel_ids:
                surface, key = Surface.CHANNEL, ""
            else:
                return
            await self.core.handle_message(
                self._to_incoming(m, surface, key)
            )

        def _to_incoming(
            self, m: discord.Message, surface: Surface, key: str
        ) -> IncomingMessage:
            """Normalize a Discord message into a neutral IncomingMessage."""
            content = (m.content or "").strip()
            addressee, text = Addressee.NONE, content
            # In a thread, a leading mention of someone else means teammates are
            # talking among themselves; a leading @bot is stripped and answered.
            if surface is Surface.THREAD:
                mid = _leading_mention_id(content)
                if mid is not None:
                    if mid != getattr(self.user, "id", None):
                        addressee = Addressee.OTHER
                    else:
                        addressee = Addressee.SELF
                        text = re.sub(r"^\s*<@[!&]?\d+>\s*", "", content)
            ch = m.channel
            # _native is the Message; the transport routes sends through
            # message.channel and anchors open_thread to the message.
            dest = ChatDest(
                thread_key=key,
                surface=surface,
                channel_id=str(ch.id),
                _native=m,
            )
            return IncomingMessage(
                text=text,
                dest=dest,
                surface=surface,
                author_id=str(m.author.id),
                author_display=m.author.display_name,
                author_role_ids=frozenset(
                    str(r.id) for r in getattr(m.author, "roles", []) or []
                ),
                addressee=addressee,
                attachments=tuple(
                    AttachmentRef(
                        a.filename, getattr(a, "size", 0) or 0, _native=a
                    )
                    for a in m.attachments
                ),
                channel_id=str(ch.id),
                _native=m,
            )

    TunnelClient(intents=intents).run(token, log_handler=None)
