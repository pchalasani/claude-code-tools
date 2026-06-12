"""Discord front-end for agent-tunnel (handle-opens-a-thread model).

Flow:

- A teammate posts ``<handle> [question]`` in a watched channel. If the
  handle is live in the registry, the bot opens a public thread, binds that
  thread to the published session, and answers (or posts a ready notice).
- Subsequent messages *inside that thread* are follow-ups to the same fork —
  no handle needed.
- Each handle/thread = its own fork; different teammates and different
  sessions never collide.

Other bots are always ignored (no loops with a co-resident bot such as
openclaw). ``discord`` is imported lazily so the rest of the package works
without the dependency installed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .backends import Backend, BackendError
from .config import TunnelConfig
from .registry import HANDLE_RE, Registry
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")

DISCORD_MSG_LIMIT = 2000
REAP_INTERVAL_S = 300
THREAD_NAME_MAX = 90


def resolve_token(cfg: TunnelConfig) -> str:
    """Resolve the Discord bot token: env var first, then token_file."""
    token = os.environ.get(cfg.discord.token_env, "").strip()
    if not token and cfg.discord.token_file:
        path = Path(cfg.discord.token_file).expanduser()
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
    return token


CLOSE_COMMANDS = {"!done", "!close", "!end"}


def is_close_command(text: str) -> bool:
    """True if a thread/DM message is a close-out command (e.g. !done)."""
    return text.strip().lower() in CLOSE_COMMANDS


def split_chunks(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """Split text into <=limit chunks, preferring newline boundaries."""
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            head, line = line[:limit], line[limit:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def run_bot(
    cfg: TunnelConfig,
    backend: Backend,
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

    sem = asyncio.Semaphore(cfg.limits.max_concurrent)
    locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    last_ask: dict[int, float] = {}

    class TunnelClient(discord.Client):
        """Routes channel/thread/DM messages to the backend."""

        async def setup_hook(self) -> None:
            self.loop.create_task(self._reaper())

        async def _reaper(self) -> None:
            while True:
                await asyncio.sleep(REAP_INTERVAL_S)
                try:
                    reaped = await asyncio.to_thread(backend.reap_idle)
                    if reaped:
                        logger.info("Reaped %d idle window(s)", reaped)
                except Exception:
                    logger.exception("Reaper error")

        async def on_ready(self) -> None:
            logger.info(
                "Logged in as %s; watching channels %s",
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

        def _cooldown_ok(self, user_id: int) -> bool:
            now = time.time()
            if now - last_ask.get(user_id, 0) < cfg.limits.per_user_cooldown_s:
                return False
            last_ask[user_id] = now
            return True

        async def on_message(self, message: discord.Message) -> None:
            if message.author.bot:
                return
            content = (message.content or "").strip()
            if not content:
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
            # Name the thread after the question (readable) — fall back to
            # the label/handle when the opener carried no question.
            thread_name = (question or label)[:THREAD_NAME_MAX]
            thread = await message.create_thread(name=thread_name)
            logger.info(
                "Opened thread for handle %s (session %s) asked by %s",
                rec.handle,
                rec.session_id[:8],
                message.author.display_name,
            )
            store.bind(
                f"th:{thread.id}",
                handle=rec.handle,
                expert_session_id=rec.session_id,
                project_dir=rec.cwd,
                backend=cfg.backend,
                asker=message.author.display_name,
            )
            if question:
                await self._answer(thread, f"th:{thread.id}", question)
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
            if is_close_command(content):
                await self._close(thread, thread_key)
                return
            if not self._cooldown_ok(message.author.id):
                await message.add_reaction("⏳")
                return
            await self._answer(thread, thread_key, content)

        async def _on_direct(
            self, message: discord.Message, content: str
        ) -> None:
            """DM handling: `<handle> ...` (re)binds; bare text follows up."""
            thread_key = f"dm:{message.channel.id}"
            if is_close_command(content) and store.get(thread_key) is not None:
                if self._allowed(message.author):
                    await self._close(message.channel, thread_key)
                return
            token, _, remainder = content.partition(" ")
            handle = token.strip().lower()
            rec = registry.get(handle)
            if rec is not None:
                store.remove(thread_key)
                store.bind(
                    thread_key,
                    handle=rec.handle,
                    expert_session_id=rec.session_id,
                    project_dir=rec.cwd,
                    backend=cfg.backend,
                    asker=message.author.display_name,
                )
                content = remainder.strip()
                if not content:
                    await message.channel.send(
                        f"Connected to **{rec.label or rec.handle}**."
                    )
                    return
            elif store.get(thread_key) is None:
                await message.channel.send(
                    "Start with a handle, e.g. `pay-7Q2 your question`."
                )
                return
            if not self._allowed(message.author):
                return
            if not self._cooldown_ok(message.author.id):
                await message.add_reaction("⏳")
                return
            await self._answer(message.channel, thread_key, content)

        async def _close(self, dest: Any, thread_key: str) -> None:
            """Close a thread: tear down its fork and confirm."""
            try:
                await asyncio.to_thread(backend.forget, thread_key)
            except Exception:
                logger.exception("Error closing %s", thread_key)
            logger.info("Closed thread %s on request", thread_key)
            await dest.send(
                "✅ Closed and cleaned up. Post the handle in the channel "
                "to start a fresh thread anytime."
            )

        async def _answer(
            self, dest: Any, thread_key: str, question: str
        ) -> None:
            lock = locks[thread_key]
            if lock.locked():
                await dest.send(
                    "⏳ Still working on the previous question here — "
                    "I'll take this one next."
                )
            async with lock, sem:
                try:
                    async with dest.typing():
                        answer = await asyncio.to_thread(
                            backend.ask, thread_key, question
                        )
                except BackendError as exc:
                    await dest.send(f"⚠️ {str(exc)[:1500]}")
                    return
                except Exception:
                    logger.exception("Unexpected backend failure")
                    await dest.send(
                        "⚠️ Unexpected error — the owner can check the "
                        "agent-tunnel logs."
                    )
                    return

            text = answer.text
            if len(text) > cfg.limits.max_inline_chars:
                preview = split_chunks(text)[0]
                file = discord.File(
                    io.BytesIO(text.encode("utf-8")), filename="answer.md"
                )
                await dest.send(preview, file=file)
                return
            for chunk in split_chunks(text):
                await dest.send(chunk)

    TunnelClient(intents=intents).run(token, log_handler=None)
