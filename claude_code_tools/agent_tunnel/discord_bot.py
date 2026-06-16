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
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .backends import (
    Answer,
    Backend,
    BackendError,
    backend_by_name,
    backend_for_record,
    effective_backend,
)
from .config import TunnelConfig
from .convert import CONVERTIBLE_EXTS, convert_attachment
from .paths import attachment_preamble, uploads_dir_for
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
LIST_COMMANDS = {"!list", "!handles"}


def is_close_command(text: str) -> bool:
    """True if a thread/DM message is a close-out command (e.g. !done)."""
    return text.strip().lower() in CLOSE_COMMANDS


def is_list_command(text: str) -> bool:
    """True if a message asks for the list of shared handles (!list)."""
    return text.strip().lower() in LIST_COMMANDS


def _safe_filename(name: str) -> str:
    """Basename of an uploaded file, stripped to safe chars (no traversal).

    Long names are shortened but keep their extension — downstream code decides
    type/conversion from the suffix, so chopping `.docx` off the end would skip
    conversion and hand the fork an unreadable path.
    """
    base = os.path.basename(name or "").strip() or "file"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    if len(cleaned) <= 120:
        return cleaned
    ext = Path(cleaned).suffix
    if 1 < len(ext) <= 12:  # plausible extension — keep it, trim the stem
        return cleaned[: -len(ext)][: 120 - len(ext)] + ext
    return cleaned[:120]


def _unique_name(name: str, used: set[str]) -> str:
    """`name` unless already in `used`, else suffixed `-2`/`-3`/… before the
    extension. Records the chosen name in `used`."""
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.partition(".")
    i = 2
    while f"{stem}-{i}{dot}{ext}" in used:
        i += 1
    chosen = f"{stem}-{i}{dot}{ext}"
    used.add(chosen)
    return chosen


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
                    reaped = await asyncio.to_thread(self._reap_all)
                    if reaped:
                        logger.info("Reaped %d idle window(s)", reaped)
                except Exception:
                    logger.exception("Reaper error")

        def _reap_all(self) -> int:
            """Reap idle windows across every backend present in the store.

            The daemon now defaults to headless, but records from earlier tmux
            runs still own live windows; reaping only the configured backend
            would leak them. ``reap_idle`` filters by its own backend name, so
            calling it once per distinct record backend covers them all.
            """
            cache: dict[str, Backend] = {}
            total = 0
            names = {
                effective_backend(r, cfg.backend)
                for r in store.all_records()
            }
            for name in names:
                total += backend_by_name(cfg, store, name, cache).reap_idle()
            return total

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
                    await self._list_handles(message.channel)
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
            store.bind(
                f"th:{thread.id}",
                handle=rec.handle,
                expert_session_id=rec.session_id,
                project_dir=rec.cwd,
                config_dir=rec.config_dir,
                access=rec.access,
                backend=cfg.backend,
                asker=message.author.display_name,
            )
            if question or message.attachments:
                await self._answer(
                    thread, f"th:{thread.id}", question, message.attachments
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
                await self._list_handles(thread)
                return
            if is_close_command(content):
                await self._close(thread, thread_key)
                return
            if not self._cooldown_ok(message.author.id):
                await message.add_reaction("⏳")
                return
            await self._answer(thread, thread_key, content, message.attachments)

        async def _on_direct(
            self, message: discord.Message, content: str
        ) -> None:
            """DM handling: `<handle> ...` (re)binds; bare text follows up."""
            thread_key = f"dm:{message.channel.id}"
            if is_list_command(content):
                if self._allowed(message.author):
                    await self._list_handles(message.channel)
                return
            if is_close_command(content) and store.get(thread_key) is not None:
                if self._allowed(message.author):
                    await self._close(message.channel, thread_key)
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
                # released here before _answer re-acquires it below — it is not
                # reentrant — so the new turn simply queues behind the old one.
                async with locks[thread_key]:
                    existing = store.get(thread_key)
                    if existing is not None:
                        await asyncio.to_thread(
                            backend_for_record(cfg, store, existing).forget,
                            thread_key,
                        )
                    store.bind(
                        thread_key,
                        handle=rec.handle,
                        expert_session_id=rec.session_id,
                        project_dir=rec.cwd,
                        config_dir=rec.config_dir,
                        access=rec.access,
                        backend=cfg.backend,
                        asker=message.author.display_name,
                    )
                content = remainder.strip()
                if not content and not message.attachments:
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
            await self._answer(
                message.channel, thread_key, content, message.attachments
            )

        async def _list_handles(self, dest: Any) -> None:
            """Post the list of currently shared handles."""
            recs = registry.active()
            if not recs:
                await dest.send("No sessions are shared right now.")
                return
            lines = ["**Available handles** — post `<handle> your question`:"]
            for rec in recs:
                proj = Path(rec.cwd).name
                label = (
                    f" ({rec.label})"
                    if rec.label and rec.label != rec.handle
                    else ""
                )
                lines.append(f"• `{rec.handle}`{label} — {proj}")
            await dest.send("\n".join(lines)[:1900])

        async def _close(self, dest: Any, thread_key: str) -> None:
            """Close a thread: tear down its fork and confirm."""
            try:
                # Hold the thread lock so we don't delete a turn's upload/
                # outbox dirs (or kill its window) while it is mid-answer.
                async with locks[thread_key]:
                    rec = store.get(thread_key)
                    await asyncio.to_thread(
                        backend_for_record(cfg, store, rec).forget, thread_key
                    )
            except Exception:
                logger.exception("Error closing %s", thread_key)
            logger.info("Closed thread %s on request", thread_key)
            await dest.send(
                "✅ Closed and cleaned up. Post the handle in the channel "
                "to start a fresh thread anytime."
            )

        async def _answer(
            self,
            dest: Any,
            thread_key: str,
            question: str,
            attachments: Any = None,
        ) -> None:
            # Per-question log line so an unattended (esp. headless) daemon
            # shows live activity + an audit trail of who-asked-what.
            rec = store.get(thread_key)
            handle = rec.handle if rec else "?"
            asker = rec.asker if rec else "?"
            n_att = len(attachments or [])
            logger.info(
                "Q [%s] %s ← %s%s: %r",
                thread_key,
                handle,
                asker,
                f" +{n_att} file(s)" if n_att else "",
                (question or "").replace("\n", " ")[:120],
            )
            lock = locks[thread_key]
            if lock.locked():
                await dest.send(
                    "⏳ Still working on the previous question here — "
                    "I'll take this one next."
                )
            async with lock, sem:
                # The thread may have been rebound to a different session (or
                # closed) while this turn waited for the lock — e.g. a queued
                # follow-up parked on the "still working" send above while a
                # new-handle rebind jumped the lock queue. Don't answer it
                # against the wrong binding.
                current = store.get(thread_key)
                if current is None or (
                    rec is not None
                    and current.expert_session_id != rec.expert_session_id
                ):
                    await dest.send(
                        "↪️ This conversation was switched to a different "
                        "session before I got to your message — please "
                        "resend it."
                    )
                    return
                start = time.time()
                try:
                    async with dest.typing():
                        question = await self._ingest_attachments(
                            dest, thread_key, question, attachments or []
                        )
                        if not question.strip():
                            await dest.send(
                                "⚠️ Nothing to act on — add a question, or a "
                                "(smaller/readable) file."
                            )
                            logger.info(
                                "A [%s] %s: skipped (no usable content)",
                                thread_key,
                                handle,
                            )
                            return
                        answer = await asyncio.to_thread(
                            backend_for_record(cfg, store, rec).ask,
                            thread_key,
                            question,
                        )
                except BackendError as exc:
                    logger.warning(
                        "A [%s] %s: error after %.1fs — %s",
                        thread_key,
                        handle,
                        time.time() - start,
                        str(exc)[:200],
                    )
                    await dest.send(f"⚠️ {str(exc)[:1500]}")
                    return
                except Exception:
                    logger.exception(
                        "A [%s] %s: unexpected backend failure",
                        thread_key,
                        handle,
                    )
                    await dest.send(
                        "⚠️ Unexpected error — the owner can check the "
                        "agent-tunnel logs."
                    )
                    return

            deliverables = (
                f", {len(answer.attachments)} deliverable(s)"
                if answer.attachments
                else ""
            )
            logger.info(
                "A [%s] %s: %s in %.1fs, %d chars%s, fork %s",
                thread_key,
                handle,
                "new" if answer.new_thread else "follow-up",
                time.time() - start,
                len(answer.text),
                deliverables,
                answer.fork_session_id[:8],
            )
            text = answer.text
            if len(text) > cfg.limits.max_inline_chars:
                preview = split_chunks(text)[0]
                file = discord.File(
                    io.BytesIO(text.encode("utf-8")), filename="answer.md"
                )
                await dest.send(preview, file=file)
            else:
                for chunk in split_chunks(text):
                    await dest.send(chunk)
            await self._post_deliverables(dest, answer)

        async def _ingest_attachments(
            self,
            dest: Any,
            thread_key: str,
            question: str,
            attachments: Any,
        ) -> str:
            """Download a colleague's attachments and point the fork at them.

            Saves each (within size/count caps) into the thread's upload dir —
            which the backend exposes to the fork via ``--add-dir`` — and
            prepends the absolute paths to the question. Oversized or excess
            files are skipped with a heads-up. Returns the (possibly
            preamble-prefixed) question.
            """
            if not attachments:
                return question
            cap = int(cfg.limits.max_attachment_mb * 1024 * 1024)
            limit = cfg.limits.max_attachments
            # A unique per-turn subdir (plus per-turn dedup of basenames) keeps
            # same-named files — two `report.pdf` in one message, or one reused
            # across turns — from silently overwriting each other.
            turn_dir = (
                uploads_dir_for(cfg.state_path.parent, thread_key)
                / uuid.uuid4().hex[:8]
            )
            turn_dir.mkdir(parents=True, exist_ok=True)
            saved: list[Path] = []
            skipped: list[str] = []
            unreadable: list[str] = []
            used: set[str] = set()
            for att in list(attachments)[:limit]:
                size = getattr(att, "size", 0) or 0
                if size > cap:
                    skipped.append(f"{att.filename} ({size / 1048576:.1f} MB)")
                    continue
                target = turn_dir / _unique_name(
                    _safe_filename(att.filename), used
                )
                try:
                    await att.save(str(target))
                except Exception:
                    logger.exception("Download failed: %s", att.filename)
                    skipped.append(att.filename)
                    continue
                # Office files the Read tool can't open: best-effort convert to
                # a readable format (PDF/Markdown/text). convert_attachment
                # returns path=None when conversion is "off" or no converter
                # handled it — then mark the file unreadable rather than point
                # the fork at a binary original it can't Read.
                ext = target.suffix.lower()
                if ext in CONVERTIBLE_EXTS:
                    conv = await asyncio.to_thread(
                        convert_attachment,
                        target,
                        turn_dir,
                        cfg.attachments.convert,
                        cfg.attachments.convert_command,
                    )
                    if conv.path is not None:
                        saved.append(conv.path)
                    else:
                        unreadable.append(att.filename)
                else:
                    saved.append(target)
            if len(list(attachments)) > limit:
                extra = len(list(attachments)) - limit
                skipped.append(f"+{extra} more (max {limit} per message)")
            if skipped:
                await dest.send("⚠️ Skipped: " + ", ".join(skipped))
            if unreadable:
                await dest.send(
                    "⚠️ Couldn't open " + ", ".join(unreadable) + " here (no "
                    "converter available) — attach a PDF or paste the text."
                )
            return attachment_preamble(saved, question)

        async def _post_deliverables(self, dest: Any, answer: Answer) -> None:
            """Post files the fork wrote to its outbox back to the thread."""
            files = list(getattr(answer, "attachments", None) or [])
            if not files:
                return
            cap = int(cfg.limits.max_attachment_mb * 1024 * 1024)
            sendable: list[Path] = []
            skipped: list[str] = []
            for path in files:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > cap:
                    skipped.append(f"{path.name} ({size / 1048576:.1f} MB)")
                else:
                    sendable.append(path)
            # Discord caps a single message at 10 attachments.
            for start in range(0, len(sendable), 10):
                batch = sendable[start : start + 10]
                dfiles = [
                    discord.File(str(p), filename=p.name) for p in batch
                ]
                caption = (
                    "📎 Deliverable(s) from the agent:"
                    if start == 0
                    else "📎 More deliverables:"
                )
                try:
                    await dest.send(caption, files=dfiles)
                except Exception:
                    logger.exception("Failed to post deliverables batch")
            if skipped:
                await dest.send(
                    "⚠️ Produced but too large to post (raise "
                    "limits.max_attachment_mb): " + ", ".join(skipped)
                )

    TunnelClient(intents=intents).run(token, log_handler=None)
