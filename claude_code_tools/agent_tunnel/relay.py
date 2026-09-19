"""Platform-neutral chat relay shared by the Discord and Mattermost bots.

A front-end (``discord_bot``, ``mattermost_bot``) only routes platform
events: it decides which thread a message belongs to and hands the turn to a
:class:`Relay`, passing a :class:`Destination` (where replies go) and the
message's :class:`Upload` s (files to fetch). Everything else — per-thread
locking, the global concurrency cap, cooldowns, attachment ingest, the
backend call, chunking, deliverables, closing, idle reaping — lives here once,
so both platforms behave the same.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, AsyncContextManager, Optional, Protocol, Sequence

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
from .registry import PublishRecord, Registry
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")

REAP_INTERVAL_S = 300
# Inline preview posted above an answer that is sent as a file. Kept short so
# a platform with a large message limit doesn't post the whole answer twice.
PREVIEW_CHARS = 1500
CLOSE_COMMANDS = {"!done", "!close", "!end"}
LIST_COMMANDS = {"!list", "!handles"}


def format_relayed_message(
    sender: str, question: str, platform: str = "Discord"
) -> str:
    """Prefix a relayed chat message with its sender for the fork.

    The daemon knows who sent each message; the forked Claude does not, so we
    prepend ``<name> (via <platform>) says:``. The persona explains this
    convention so Claude reads the prefix as the asker's identity.
    """
    who = sender.strip() or "A teammate"
    return f"{who} (via {platform}) says:\n{question}"


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


def split_chunks(text: str, limit: int = 2000) -> list[str]:
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


class Destination(Protocol):
    """Where a turn's replies go (a Discord thread, a Mattermost thread)."""

    #: Longest single message the platform accepts.
    max_len: int
    #: Most files one message may carry.
    max_files: int

    async def send(self, text: str) -> None:
        """Post one message (already within ``max_len``)."""
        ...

    async def send_text_file(self, preview: str, name: str, data: bytes) -> None:
        """Post ``preview`` with ``data`` attached as file ``name``."""
        ...

    async def send_files(self, caption: str, paths: Sequence[Path]) -> None:
        """Post ``caption`` with local files attached."""
        ...

    def typing(self) -> AsyncContextManager[Any]:
        """Show a typing indicator while the block runs."""
        ...


class Upload(Protocol):
    """A file a colleague attached to a message."""

    filename: str
    size: int

    async def save(self, path: str) -> None:
        """Download the file to ``path``."""
        ...


class Relay:
    """Runs chat turns against the backends; shared by every front-end."""

    def __init__(
        self, cfg: TunnelConfig, store: TunnelStore, registry: Registry
    ) -> None:
        """Keep references; asyncio primitives are created on first use."""
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sem: Optional[asyncio.Semaphore] = None
        self._last_ask: dict[Any, float] = {}
        self._notices: set[asyncio.Task[None]] = set()

    @property
    def sem(self) -> asyncio.Semaphore:
        """Global cap on concurrent backend turns (across platforms)."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.cfg.limits.max_concurrent)
        return self._sem

    def cooldown_ok(self, user_key: Any) -> bool:
        """False if this user asked less than the cooldown ago."""
        now = time.time()
        last = self._last_ask.get(user_key, 0)
        if now - last < self.cfg.limits.per_user_cooldown_s:
            return False
        self._last_ask[user_key] = now
        return True

    def bind(
        self,
        thread_key: str,
        rec: PublishRecord,
        asker: str,
        platform: str,
    ) -> None:
        """Bind a new chat thread to a published session."""
        self.store.bind(
            thread_key,
            handle=rec.handle,
            expert_session_id=rec.session_id,
            project_dir=rec.cwd,
            config_dir=rec.config_dir,
            access=rec.access,
            backend=self.cfg.backend,
            asker=asker,
            platform=platform,
        )

    async def reaper(self) -> None:
        """Periodically release idle backend resources (runs forever)."""
        while True:
            await asyncio.sleep(REAP_INTERVAL_S)
            try:
                reaped = await asyncio.to_thread(self.reap_all)
                if reaped:
                    logger.info("Reaped %d idle window(s)", reaped)
            except Exception:
                logger.exception("Reaper error")

    def reap_all(self) -> int:
        """Reap idle windows across every backend present in the store.

        Records from earlier tmux runs may still own live windows even when
        the daemon defaults to headless; ``reap_idle`` filters by its own
        backend name, so calling it once per distinct record backend covers
        them all.
        """
        cache: dict[str, Backend] = {}
        names = {
            effective_backend(r, self.cfg.backend)
            for r in self.store.all_records()
        }
        return sum(
            backend_by_name(self.cfg, self.store, name, cache).reap_idle()
            for name in names
        )

    def handles_text(self) -> str:
        """Chat text listing the currently shared handles."""
        recs = self.registry.active()
        if not recs:
            return "No sessions are shared right now."
        lines = ["**Available handles** — post `<handle> your question`:"]
        for rec in recs:
            proj = Path(rec.cwd).name
            label = (
                f" ({rec.label})"
                if rec.label and rec.label != rec.handle
                else ""
            )
            lines.append(f"• `{rec.handle}`{label} — {proj}")
        return "\n".join(lines)[:1900]

    async def close(self, dest: Destination, thread_key: str) -> None:
        """Close a thread: tear down its fork and confirm."""
        try:
            # Hold the thread lock so we don't delete a turn's upload/
            # outbox dirs (or kill its window) while it is mid-answer.
            async with self.locks[thread_key]:
                rec = self.store.get(thread_key)
                await asyncio.to_thread(
                    backend_for_record(self.cfg, self.store, rec).forget,
                    thread_key,
                )
        except Exception:
            logger.exception("Error closing %s", thread_key)
        logger.info("Closed thread %s on request", thread_key)
        await dest.send(
            "✅ Closed and cleaned up. Post the handle in the channel "
            "to start a fresh thread anytime."
        )

    async def answer(
        self,
        dest: Destination,
        thread_key: str,
        question: str,
        uploads: Sequence[Upload] = (),
        sender: str = "",
    ) -> None:
        """Answer one message in a bound thread and post the reply."""
        # Per-question log line so an unattended daemon shows live activity
        # plus an audit trail of who asked what.
        rec = self.store.get(thread_key)
        handle = rec.handle if rec else "?"
        # This message's author is more accurate than the bind-time asker
        # for follow-ups by other people.
        asker = sender or (rec.asker if rec else "?")
        n_att = len(uploads)
        logger.info(
            "Q [%s] %s ← %s%s: %r",
            thread_key,
            handle,
            asker,
            f" +{n_att} file(s)" if n_att else "",
            (question or "").replace("\n", " ")[:120],
        )
        lock = self.locks[thread_key]
        if lock.locked():
            # Fire-and-forget: awaiting here would let a later message queue
            # on the lock ahead of this one and be answered out of order.
            notice = asyncio.create_task(
                dest.send(
                    "⏳ Still working on the previous question here — "
                    "I'll take this one next."
                )
            )
            self._notices.add(notice)
            notice.add_done_callback(self._notice_done)
        async with lock, self.sem:
            # The thread may have been rebound (even to the same session,
            # which resets the fork) or closed while this turn waited for the
            # lock. A fresh bind stamps a new created_at, so compare the full
            # binding identity and don't answer a queued question against a
            # binding it was not asked under.
            current = self.store.get(thread_key)
            if current is None or (
                rec is not None
                and (
                    current.expert_session_id != rec.expert_session_id
                    or current.created_at != rec.created_at
                )
            ):
                await dest.send(
                    "↪️ This conversation was restarted before I got to "
                    "your message — please resend it."
                )
                return
            start = time.time()
            try:
                async with dest.typing():
                    question = await self._ingest(
                        dest, thread_key, question, uploads
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
                    # Tell the fork who sent this (it can't see chat); the
                    # persona explains the "<name> (via X) says:" convention.
                    platform = (
                        current.platform if current.platform else self.cfg.platform
                    )
                    question = format_relayed_message(sender, question, platform)
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
                await dest.send(f"⚠️ {str(exc)[:1500]}")
                return
            except Exception:
                logger.exception(
                    "A [%s] %s: unexpected backend failure", thread_key, handle
                )
                await dest.send(
                    "⚠️ Unexpected error — the owner can check the "
                    "agent-tunnel logs."
                )
                return

        self._log_answer(thread_key, handle, answer, start)
        text = answer.text
        if len(text) > self.cfg.limits.max_inline_chars:
            preview = split_chunks(text, min(dest.max_len, PREVIEW_CHARS))[0]
            await dest.send_text_file(preview, "answer.md", text.encode("utf-8"))
        else:
            for chunk in split_chunks(text, dest.max_len):
                await dest.send(chunk)
        await self._post_deliverables(dest, answer)

    def _notice_done(self, task: "asyncio.Task[None]") -> None:
        self._notices.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Queue notice failed: %s", task.exception())

    @staticmethod
    def _log_answer(
        thread_key: str, handle: str, answer: Answer, start: float
    ) -> None:
        if answer.refreshed:
            kind = "follow-up (re-forked from latest session)"
        else:
            kind = "new" if answer.new_thread else "follow-up"
        deliverables = (
            f", {len(answer.attachments)} deliverable(s)"
            if answer.attachments
            else ""
        )
        logger.info(
            "A [%s] %s: %s in %.1fs, %d chars%s, fork %s",
            thread_key,
            handle,
            kind,
            time.time() - start,
            len(answer.text),
            deliverables,
            answer.fork_session_id[:8],
        )

    async def _ingest(
        self,
        dest: Destination,
        thread_key: str,
        question: str,
        uploads: Sequence[Upload],
    ) -> str:
        """Download a colleague's attachments and point the fork at them.

        Saves each (within size/count caps) into the thread's upload dir —
        which the backend exposes to the fork via ``--add-dir`` — and
        prepends the absolute paths to the question. Oversized or excess
        files are skipped with a heads-up. Returns the (possibly
        preamble-prefixed) question.
        """
        if not uploads:
            return question
        cfg = self.cfg
        cap = int(cfg.limits.max_attachment_mb * 1024 * 1024)
        limit = cfg.limits.max_attachments
        # A unique per-turn subdir (plus per-turn dedup of basenames) keeps
        # same-named files from silently overwriting each other.
        turn_dir = (
            uploads_dir_for(cfg.state_path.parent, thread_key)
            / uuid.uuid4().hex[:8]
        )
        turn_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        skipped: list[str] = []
        unreadable: list[str] = []
        used: set[str] = set()
        for att in list(uploads)[:limit]:
            # Optional hook, run under the thread lock: an upload may learn
            # its real name/size here (a network call) without letting a
            # later message overtake this one.
            prepare = getattr(att, "prepare", None)
            if prepare is not None:
                try:
                    await prepare()
                except Exception:
                    # Fail closed: without its real name the file could skip
                    # Office conversion and reach the fork unreadable.
                    logger.warning("Could not look up %s", att.filename)
                    skipped.append(f"{att.filename} (lookup failed)")
                    continue
            size = getattr(att, "size", 0) or 0
            if size > cap:
                skipped.append(f"{att.filename} ({size / 1048576:.1f} MB)")
                continue
            target = turn_dir / _unique_name(_safe_filename(att.filename), used)
            try:
                await att.save(str(target))
            except Exception:
                logger.exception("Download failed: %s", att.filename)
                skipped.append(att.filename)
                continue
            # Office files the Read tool can't open: best-effort convert to a
            # readable format; if nothing handled it, mark the file
            # unreadable rather than point the fork at a binary original.
            if target.suffix.lower() in CONVERTIBLE_EXTS:
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
        if len(uploads) > limit:
            skipped.append(
                f"+{len(uploads) - limit} more (max {limit} per message)"
            )
        if skipped:
            await dest.send("⚠️ Skipped: " + ", ".join(skipped))
        if unreadable:
            why = (
                "Office conversion is turned off"
                if cfg.attachments.convert == "off"
                else "no converter is available"
            )
            await dest.send(
                f"⚠️ Couldn't open {', '.join(unreadable)} here ({why}) "
                "— attach a PDF or paste the text."
            )
        return attachment_preamble(saved, question)

    async def _post_deliverables(
        self, dest: Destination, answer: Answer
    ) -> None:
        """Post files the fork wrote to its outbox back to the thread."""
        files = list(getattr(answer, "attachments", None) or [])
        if not files:
            return
        cap = int(self.cfg.limits.max_attachment_mb * 1024 * 1024)
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
        step = max(1, dest.max_files)
        for start in range(0, len(sendable), step):
            caption = (
                "📎 Deliverable(s) from the agent:"
                if start == 0
                else "📎 More deliverables:"
            )
            try:
                await dest.send_files(caption, sendable[start : start + step])
            except Exception:
                logger.exception("Failed to post deliverables batch")
        if skipped:
            await dest.send(
                "⚠️ Produced but too large to post (raise "
                "limits.max_attachment_mb): " + ", ".join(skipped)
            )
