"""Platform-neutral routing + answer/ingest/deliver pipeline + reaper.

``ChatCore`` owns every routing and policy decision for agent-tunnel's chat
front-ends. A front-end adapter (``discord_bot``, ``slack_bot``) normalizes its
platform events into :class:`~.chat_types.IncomingMessage`, implements
:class:`~.chat_types.ChatTransport`, constructs ONE ``ChatCore``, schedules
:meth:`ChatCore.run_reaper`, and forwards each accepted message via
``await core.handle_message(msg)``. Because all policy lives here, the Discord
and Slack front-ends are behavior-identical by construction.

The bodies below are ports of the original ``discord_bot`` handlers with the
``discord.*`` I/O replaced by ``ChatTransport`` calls, the conversation key read
from ``dest.thread_key``, the leading-mention decision read from
``msg.addressee``, the cooldown map keyed on the string principal id, and the
numeric caps read from ``transport.limits``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from pathlib import Path

from .backends import (
    Answer,
    Backend,
    BackendError,
    backend_by_name,
    backend_for_record,
    effective_backend,
)
from .chat_types import (  # re-exported for adapters/tests via this module
    Addressee,
    AttachmentRef,
    ChatDest,
    ChatTransport,
    IncomingMessage,
    OutgoingFile,
    Surface,
    format_relayed_message,
    is_close_command,
    is_list_command,
    split_chunks,
    _safe_filename,
    _unique_name,
)
from .config import TunnelConfig
from .convert import CONVERTIBLE_EXTS, convert_attachment
from .paths import attachment_preamble, uploads_dir_for
from .registry import HANDLE_RE, Registry
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")

REAP_INTERVAL_S = 300


class ChatCore:
    """Platform-neutral routing + answer/ingest/deliver pipeline + reaper.

    ONE instance per running bot. The adapter builds
    ``core = ChatCore(cfg, store, registry, transport)``, schedules
    ``core.run_reaper()`` on its loop, and for each accepted, normalized event
    awaits ``core.handle_message(msg)``.
    """

    def __init__(
        self,
        cfg: TunnelConfig,
        store: TunnelStore,
        registry: Registry,
        transport: ChatTransport,
    ) -> None:
        """Keep references and create the per-run concurrency primitives."""
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self.tx = transport
        self._sem = asyncio.Semaphore(cfg.limits.max_concurrent)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_ask: dict[str, float] = {}

    async def handle_message(self, msg: IncomingMessage) -> None:
        """Drop empty messages, then dispatch on the surface the adapter set."""
        # An attachment-only message has empty text but still carries a file for
        # the agent to read -- ``has_content`` keeps it.
        if not msg.has_content:
            return
        if msg.surface is Surface.THREAD:
            await self._on_thread(msg)
        elif msg.surface is Surface.DM:
            await self._on_direct(msg)
        else:
            await self._on_channel(msg)

    # -- policy ----------------------------------------------------------------

    def _allowed(self, msg: IncomingMessage) -> bool:
        """True if this principal may ask (empty allowlists = anyone allowed)."""
        uids, rids = self.cfg.principal_allowlists()
        if not uids and not rids:
            return True
        if msg.author_id in uids:
            return True
        return bool(msg.author_role_ids & rids)

    def _cooldown_ok(self, author_id: str) -> bool:
        """True (and stamps now) unless this principal asked within the cooldown."""
        now = time.time()
        if now - self._last_ask.get(author_id, 0) < self.cfg.limits.per_user_cooldown_s:
            return False
        self._last_ask[author_id] = now
        return True

    # -- routing ---------------------------------------------------------------

    async def _on_channel(self, msg: IncomingMessage) -> None:
        """A message in a watched channel: try to open a handle thread."""
        content = msg.text
        if is_list_command(content):
            if self._allowed(msg):
                await self._list_handles(msg.dest)
            return
        token, _, remainder = content.partition(" ")
        handle = token.strip().lower()
        rec = self.registry.get(handle)
        if rec is None:
            # Only complain if it clearly looks like a handle attempt (before the
            # allowlist gate, matching the shipped bot).
            if HANDLE_RE.match(handle) and not remainder:
                await self.tx.send_text(
                    msg.dest,
                    f"No live session for handle `{handle}`. "
                    "Ask the owner to `>share` it.",
                )
            return
        if not self._allowed(msg):
            return

        question = remainder.strip()
        label = rec.label or rec.handle
        # Lead with the handle (recognizable), then the question so multiple
        # threads for the same handle stay distinguishable.
        thread_name = (f"{label}: {question}" if question else label)[
            : self.tx.limits.thread_title_max
        ]
        dest = await self.tx.open_thread(msg.dest, thread_name)
        logger.info(
            "Opened thread for handle %s (session %s) asked by %s",
            rec.handle,
            rec.session_id[:8],
            msg.author_display,
        )
        self.store.bind(
            dest.thread_key,
            handle=rec.handle,
            expert_session_id=rec.session_id,
            project_dir=rec.cwd,
            config_dir=rec.config_dir,
            access=rec.access,
            backend=self.cfg.backend,
            asker=msg.author_display,
        )
        if question or msg.attachments:
            await self._answer(dest, question, msg)
        else:
            await self.tx.send_text(
                dest,
                f"Connected to **{label}**. Ask your question here; "
                "follow-ups stay in this thread.",
            )

    async def _on_thread(self, msg: IncomingMessage) -> None:
        """A follow-up inside a bound thread."""
        thread_key = msg.dest.thread_key
        if self.store.get(thread_key) is None:
            return
        if not self._allowed(msg):
            return
        # A message that opens with @someone-else (or a broadcast/role) is
        # teammates talking among themselves -- stay out silently. A leading
        # @bot was already stripped by the adapter (Addressee.SELF). No mention
        # = answer (in a thread you never need to address the bot).
        if msg.addressee is Addressee.OTHER:
            return
        content = msg.text
        if is_list_command(content):
            await self._list_handles(msg.dest)
            return
        if is_close_command(content):
            await self._close(msg.dest)
            return
        if not self._cooldown_ok(msg.author_id):
            await self.tx.add_reaction(msg, "hourglass")
            return
        await self._answer(msg.dest, content, msg)

    async def _on_direct(self, msg: IncomingMessage) -> None:
        """DM handling: `<handle> ...` (re)binds; bare text follows up."""
        thread_key = msg.dest.thread_key
        content = msg.text
        if is_list_command(content):
            if self._allowed(msg):
                await self._list_handles(msg.dest)
            return
        if is_close_command(content) and self.store.get(thread_key) is not None:
            if self._allowed(msg):
                await self._close(msg.dest)
            return
        token, _, remainder = content.partition(" ")
        handle = token.strip().lower()
        rec = self.registry.get(handle)
        if rec is not None:
            # Rebinding this DM starts a fresh thread; fully tear down any
            # previous binding first so its uploads/outbox (and live tmux window)
            # don't leak into the new handle's fork -- the upload dir is keyed
            # only by the DM channel and would otherwise be reused across handles.
            #
            # Hold the thread lock around forget+bind so the rebind can't race a
            # still-running turn on the OLD binding: that turn holds this same
            # lock, and its trailing upsert() would otherwise attach the old fork
            # to the new handle. The lock is released here before _answer
            # re-acquires it below -- it is not reentrant -- so the new turn
            # simply queues behind the old one.
            async with self._locks[thread_key]:
                existing = self.store.get(thread_key)
                if existing is not None:
                    await asyncio.to_thread(
                        backend_for_record(
                            self.cfg, self.store, existing
                        ).forget,
                        thread_key,
                    )
                self.store.bind(
                    thread_key,
                    handle=rec.handle,
                    expert_session_id=rec.session_id,
                    project_dir=rec.cwd,
                    config_dir=rec.config_dir,
                    access=rec.access,
                    backend=self.cfg.backend,
                    asker=msg.author_display,
                )
            content = remainder.strip()
            if not content and not msg.attachments:
                await self.tx.send_text(
                    msg.dest, f"Connected to **{rec.label or rec.handle}**."
                )
                return
        elif self.store.get(thread_key) is None:
            await self.tx.send_text(
                msg.dest, "Start with a handle, e.g. `pay-7Q2 your question`."
            )
            return
        if not self._allowed(msg):
            return
        if not self._cooldown_ok(msg.author_id):
            await self.tx.add_reaction(msg, "hourglass")
            return
        await self._answer(msg.dest, content, msg)

    async def _list_handles(self, dest: ChatDest) -> None:
        """Post the list of currently shared handles."""
        recs = self.registry.active()
        if not recs:
            await self.tx.send_text(dest, "No sessions are shared right now.")
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
        # Single message (not paginated); the invariant list_truncate_len <=
        # max_message_len keeps this within the no-re-chunk send_text contract.
        limit = min(
            self.tx.limits.list_truncate_len, self.tx.limits.max_message_len
        )
        await self.tx.send_text(dest, "\n".join(lines)[:limit])

    async def _close(self, dest: ChatDest) -> None:
        """Close a thread: tear down its fork and confirm."""
        thread_key = dest.thread_key
        try:
            # Hold the thread lock so we don't delete a turn's upload/outbox dirs
            # (or kill its window) while it is mid-answer.
            async with self._locks[thread_key]:
                rec = self.store.get(thread_key)
                await asyncio.to_thread(
                    backend_for_record(self.cfg, self.store, rec).forget,
                    thread_key,
                )
        except Exception:
            logger.exception("Error closing %s", thread_key)
        logger.info("Closed thread %s on request", thread_key)
        await self.tx.send_text(
            dest,
            "✅ Closed and cleaned up. Post the handle in the channel "
            "to start a fresh thread anytime.",
        )

    # -- the turn --------------------------------------------------------------

    async def _answer(
        self, dest: ChatDest, question: str, msg: IncomingMessage
    ) -> None:
        """Run one turn in the thread's fork and deliver the answer + files."""
        thread_key = dest.thread_key
        sender = msg.author_display
        attachments = msg.attachments
        # Per-question log line so an unattended (esp. headless) daemon shows
        # live activity + an audit trail of who-asked-what.
        rec = self.store.get(thread_key)
        handle = rec.handle if rec else "?"
        # The per-message sender is more accurate than the bind-time asker for
        # follow-ups by other people.
        asker = sender or (rec.asker if rec else "?")
        n_att = len(attachments or [])
        logger.info(
            "Q [%s] %s ← %s%s: %r",
            thread_key,
            handle,
            asker,
            f" +{n_att} file(s)" if n_att else "",
            (question or "").replace("\n", " ")[:120],
        )
        lock = self._locks[thread_key]
        if lock.locked():
            await self.tx.send_text(
                dest,
                "⏳ Still working on the previous question here — "
                "I'll take this one next.",
            )
        async with lock, self._sem:
            # The thread may have been rebound (even to the same session, which
            # resets the fork) or closed while this turn waited for the lock. A
            # fresh bind stamps a new created_at, so compare the full binding
            # identity (session + created_at) and don't answer a queued question
            # against a binding it was not asked under.
            current = self.store.get(thread_key)
            if current is None or (
                rec is not None
                and (
                    current.expert_session_id != rec.expert_session_id
                    or current.created_at != rec.created_at
                )
            ):
                await self.tx.send_text(
                    dest,
                    "↪️ This conversation was restarted before I got to "
                    "your message — please resend it.",
                )
                return
            start = time.time()
            try:
                async with self.tx.activity(dest):
                    question = await self._ingest_attachments(
                        dest, thread_key, question, attachments or ()
                    )
                    if not question.strip():
                        await self.tx.send_text(
                            dest,
                            "⚠️ Nothing to act on — add a question, or a "
                            "(smaller/readable) file.",
                        )
                        logger.info(
                            "A [%s] %s: skipped (no usable content)",
                            thread_key,
                            handle,
                        )
                        return
                    # Tell the fork who sent this (it can't see chat); the persona
                    # explains the "<name> (via X) says:" convention.
                    question = format_relayed_message(
                        sender, question, self.cfg.platform
                    )
                    answer = await asyncio.to_thread(
                        backend_for_record(self.cfg, self.store, rec).ask,
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
                await self.tx.send_text(
                    dest, f"⚠️ {str(exc)[: self.tx.limits.max_error_len]}"
                )
                return
            except Exception:
                logger.exception(
                    "A [%s] %s: unexpected backend failure",
                    thread_key,
                    handle,
                )
                await self.tx.send_text(
                    dest,
                    "⚠️ Unexpected error — the owner can check the "
                    "agent-tunnel logs.",
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
        await self._deliver_answer(dest, answer.text)
        await self._post_deliverables(dest, answer)

    async def _deliver_answer(self, dest: ChatDest, text: str) -> None:
        """Post the answer text: inline chunks, or a preview + answer.md if long."""
        limit = self.tx.limits.max_message_len
        if len(text) > self.cfg.limits.max_inline_chars:
            preview = split_chunks(text, limit)[0]
            await self.tx.send_files(
                dest,
                preview,
                [OutgoingFile.from_bytes(text.encode("utf-8"), "answer.md")],
            )
        else:
            for chunk in split_chunks(text, limit):
                await self.tx.send_text(dest, chunk)

    async def _ingest_attachments(
        self,
        dest: ChatDest,
        thread_key: str,
        question: str,
        attachments: tuple[AttachmentRef, ...],
    ) -> str:
        """Download a colleague's attachments and point the fork at them.

        Saves each (within size/count caps) into the thread's upload dir -- which
        the backend exposes to the fork via ``--add-dir`` -- and prepends the
        absolute paths to the question. Oversized or excess files are skipped
        with a heads-up. Returns the (possibly preamble-prefixed) question.
        """
        attlist = list(attachments)
        if not attlist:
            return question
        cap = int(self.cfg.limits.max_attachment_mb * 1024 * 1024)
        limit = self.cfg.limits.max_attachments
        # A unique per-turn subdir (plus per-turn dedup of basenames) keeps
        # same-named files from silently overwriting each other.
        turn_dir = (
            uploads_dir_for(self.cfg.state_path.parent, thread_key)
            / uuid.uuid4().hex[:8]
        )
        turn_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        skipped: list[str] = []
        unreadable: list[str] = []
        used: set[str] = set()
        for att in attlist[:limit]:
            size = att.size or 0
            if size > cap:
                skipped.append(f"{att.filename} ({size / 1048576:.1f} MB)")
                continue
            target = turn_dir / _unique_name(_safe_filename(att.filename), used)
            try:
                await self.tx.download_attachment(att, target)
            except Exception:
                logger.exception("Download failed: %s", att.filename)
                skipped.append(att.filename)
                continue
            # Office files the Read tool can't open: best-effort convert to a
            # readable format. convert_attachment returns path=None when "off" or
            # no converter handled it -- then mark unreadable rather than point
            # the fork at a binary it can't Read.
            ext = target.suffix.lower()
            if ext in CONVERTIBLE_EXTS:
                conv = await asyncio.to_thread(
                    convert_attachment,
                    target,
                    turn_dir,
                    self.cfg.attachments.convert,
                    self.cfg.attachments.convert_command,
                )
                if conv.path is not None:
                    saved.append(conv.path)
                else:
                    unreadable.append(att.filename)
            else:
                saved.append(target)
        if len(attlist) > limit:
            extra = len(attlist) - limit
            skipped.append(f"+{extra} more (max {limit} per message)")
        if skipped:
            await self.tx.send_text(dest, "⚠️ Skipped: " + ", ".join(skipped))
        if unreadable:
            why = (
                "Office conversion is turned off"
                if self.cfg.attachments.convert == "off"
                else "no converter is available"
            )
            await self.tx.send_text(
                dest,
                f"⚠️ Couldn't open {', '.join(unreadable)} here ({why}) "
                "— attach a PDF or paste the text.",
            )
        return attachment_preamble(saved, question)

    async def _post_deliverables(self, dest: ChatDest, answer: Answer) -> None:
        """Post files the fork wrote to its outbox back to the conversation."""
        files = list(getattr(answer, "attachments", None) or [])
        if not files:
            return
        cap = int(self.cfg.limits.max_attachment_mb * 1024 * 1024)
        batch_size = self.tx.limits.max_attachments_per_msg
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
        for start in range(0, len(sendable), batch_size):
            batch = sendable[start : start + batch_size]
            caption = (
                "📎 Deliverable(s) from the agent:"
                if start == 0
                else "📎 More deliverables:"
            )
            try:
                await self.tx.send_files(
                    dest, caption, [OutgoingFile.from_path(p) for p in batch]
                )
            except Exception:
                logger.exception("Failed to post deliverables batch")
        if skipped:
            await self.tx.send_text(
                dest,
                "⚠️ Produced but too large to post (raise "
                "limits.max_attachment_mb): " + ", ".join(skipped),
            )

    # -- reaper ----------------------------------------------------------------

    async def run_reaper(self) -> None:
        """Periodically reap idle backend resources (a backstop loop)."""
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

        The daemon defaults to headless, but records from earlier tmux runs still
        own live windows; reaping only the configured backend would leak them.
        ``reap_idle`` filters by its own backend name, so calling it once per
        distinct record backend covers them all.
        """
        cache: dict[str, Backend] = {}
        total = 0
        names = {
            effective_backend(r, self.cfg.backend)
            for r in self.store.all_records()
        }
        for name in names:
            total += backend_by_name(self.cfg, self.store, name, cache).reap_idle()
        return total
