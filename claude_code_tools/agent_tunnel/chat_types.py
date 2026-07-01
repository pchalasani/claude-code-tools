"""Platform-neutral seam between a chat front-end and the answer pipeline.

``ChatCore`` (in ``chat_core.py``) owns ALL routing and policy and reaches the
outside world only through :class:`ChatTransport` plus the normalized
dataclasses defined here. A front-end supplies an adapter that drops bot/self
events, normalizes each surviving event into an :class:`IncomingMessage`,
implements :class:`ChatTransport`, builds one ``ChatCore``, schedules the
reaper, and forwards each message via ``await core.handle_message(msg)``.

No platform identifier ever appears in this module, and it imports only the
standard library, so it loads without ``discord``/``slack_bolt`` installed.
"""

from __future__ import annotations

import os
import re
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Protocol, Sequence, runtime_checkable

# Kept as module constants because ``discord_bot`` re-exports them (and they are
# the Discord defaults baked into ``TransportLimits``).
DISCORD_MSG_LIMIT = 2000
THREAD_NAME_MAX = 90


@dataclass(frozen=True)
class TransportLimits:
    """Per-platform numeric caps the core honors when chunking/batching/truncating.

    Declared once per adapter instance (read via ``ChatTransport.limits``). These
    replace the hard-coded Discord constants (the 2000-char message limit, the
    literal 10 attachments/message, the 90-char thread name, the 1500-char error
    truncation, the 1900-char ``!list`` truncation). The defaults ARE Discord's,
    so a Discord adapter that omits them reproduces the shipped bot exactly.

    Invariant (enforced by the core): ``list_truncate_len <= max_message_len``.
    ``_list_handles`` truncates to ``min(list_truncate_len, max_message_len)`` so
    the "transport must not re-chunk ``send_text``" contract can never be broken.
    """

    max_message_len: int = 2000
    max_attachments_per_msg: int = 10
    thread_title_max: int = 90
    max_error_len: int = 1500
    list_truncate_len: int = 1900


@dataclass(frozen=True)
class OutgoingFile:
    """A file the core asks the transport to post, by path OR by bytes.

    Exactly one of ``data`` / ``path`` is set. Unifies the two Discord upload
    cases: ``discord.File(BytesIO(bytes), filename=...)`` (the long-answer
    ``answer.md``) and ``discord.File(path, filename=...)`` (a deliverable).

    ``filename`` MUST carry the extension; on Slack the adapter also passes it as
    ``title`` because Slack strips the extension from the filename alone.
    """

    filename: str
    data: Optional[bytes] = None
    path: Optional[Path] = None

    @classmethod
    def from_bytes(cls, data: bytes, filename: str) -> "OutgoingFile":
        """A file delivered from in-memory ``data`` (e.g. ``answer.md``)."""
        return cls(filename=filename, data=data)

    @classmethod
    def from_path(cls, path: Path) -> "OutgoingFile":
        """A file delivered from an on-disk ``path`` (a fork deliverable)."""
        return cls(filename=path.name, path=path)


@asynccontextmanager
async def noop_activity() -> AsyncIterator[None]:
    """A do-nothing ``activity()`` for transports with no typing API (Slack)."""
    yield


class Surface(Enum):
    """Which routing branch an inbound message belongs to.

    The adapter classifies; the core routes on this. Replaces Discord's
    ``isinstance(channel, Thread/DMChannel)`` and Slack's ``channel_type`` switch.
    """

    CHANNEL = "channel"  # watched channel, not in a thread (handle-opens-thread)
    THREAD = "thread"    # follow-up inside an already-bound thread
    DM = "dm"            # a direct message (Slack ``im`` AND ``mpim``)


class Addressee(Enum):
    """Who a message's *leading* mention addresses, if anyone.

    The adapter computes the verdict from its own mention wire-format AND
    pre-strips a SELF mention from :attr:`IncomingMessage.text`. The core never
    parses mentions or compares ids -- it branches on this enum, and only on the
    THREAD surface (CHANNEL uses the handle-token parse; DM is 1:1).
    """

    NONE = "none"    # no leading mention -> answer (in a thread)
    SELF = "self"    # leading mention IS this bot -> (already stripped) answer
    OTHER = "other"  # someone else / broadcast / role -> stay out, silent


@dataclass(frozen=True)
class AttachmentRef:
    """One inbound file, normalized across platforms.

    The core uses only ``filename`` / ``size`` (caps + safe naming) and hands the
    whole ref back to :meth:`ChatTransport.download_attachment`. ``url`` /
    ``_native`` are opaque to the core.

    Discord adapter builds ``AttachmentRef(a.filename, getattr(a, "size", 0) or
    0, _native=a)``. Slack adapter fills ``url`` (``url_private_download``) and
    ``_native`` with the raw file dict.
    """

    filename: str
    size: int            # bytes; 0 if the platform did not report a size
    url: str = ""        # authenticated download URL (Slack); "" on Discord
    _native: Any = None  # platform file object, for the adapter's downloader


@dataclass(frozen=True)
class ChatDest:
    """A place the core sends replies to, with a stable conversation key.

    Neutral replacement for the Discord ``dest`` threaded through
    ``_answer``/``_list_handles``/``_close``. ``thread_key`` is THE universal
    store/backend/uploads/locks key (the ``th:``/``dm:`` scheme), minted by the
    adapter.

    Attributes:
        thread_key: Universal conversation key. Discord ``th:{thread.id}`` /
            ``dm:{channel.id}``; Slack ``th:{channel}-{thread_ts}`` /
            ``dm:{channel}``. For a CHANNEL dest (used by ``!list`` and as
            ``open_thread``'s parent before a thread exists) this is "" and is
            never used as a key.
        surface: Which branch this dest serves.
        channel_id: Normalized channel/conversation id (``str``); addresses Slack
            sends. "" where unused on Discord.
        thread_ts: Slack thread anchor. For a THREAD dest it is the parent ts; for
            a CHANNEL parent dest passed to ``open_thread`` it carries the
            triggering message's ts so the Slack transport can build
            ``th:{channel}-{ts}`` and thread off it. "" on Discord.
        _native: Platform send-target (Discord: the triggering message / thread).
    """

    thread_key: str
    surface: Surface
    channel_id: str = ""
    thread_ts: str = ""
    _native: Any = None


@dataclass(frozen=True)
class IncomingMessage:
    """One inbound chat message, normalized to be wire-format-free.

    Built by the adapter AFTER dropping bot/self events and AFTER resolving the
    leading mention. Discord ids (``int``) and Slack ids (``str``) are both
    normalized to ``str`` so allowlist/cooldown/principal logic is uniform.

    Attributes:
        text: Body, ``.strip()``-ed. For a THREAD message whose leading mention
            was this bot, the adapter has ALSO stripped that mention. May be ""
            when ``attachments`` is non-empty (attachment-only message).
        dest: Where replies go (+ the conversation ``thread_key`` + ``surface``).
            For a CHANNEL message this is the parent dest passed to
            ``open_thread``; on Slack it carries ``thread_ts = ts``.
        surface: CHANNEL / THREAD / DM.
        author_id: Stable per-user principal id (``str``); keys cooldown +
            allowlist.
        author_display: Display name for logs and the ``<name> (via X) says:``
            prefix. Falls back to ``author_id``.
        author_role_ids: Principal's role/usergroup ids (``str``) for the
            allowlist role check. Empty when unavailable.
        addressee: Leading-mention verdict. Meaningful only on THREAD; CHANNEL and
            DM leave it NONE.
        attachments: Normalized inbound files (possibly empty).
        channel_id: Normalized channel/conversation id.
        ts: Platform message id/timestamp -- Slack ``ts`` (needed for reactions +
            threading). Discord leaves "" (it reacts/threads via ``_native``).
        _native: Raw platform message, used only by transport I/O targeting THIS
            message (Discord ``add_reaction``).
    """

    text: str
    dest: ChatDest
    surface: Surface
    author_id: str
    author_display: str = ""
    author_role_ids: frozenset[str] = frozenset()
    addressee: Addressee = Addressee.NONE
    attachments: tuple[AttachmentRef, ...] = ()
    channel_id: str = ""
    ts: str = ""
    _native: Any = None

    @property
    def has_content(self) -> bool:
        """True if there is text or at least one attachment (the empty-drop rule)."""
        return bool(self.text) or bool(self.attachments)


@runtime_checkable
class ChatTransport(Protocol):
    """Every chat I/O the answer pipeline needs, abstracted per platform.

    ONE instance per running bot (NOT per turn): the core passes a
    :class:`ChatDest` to every send, so one instance serves all destinations.
    All methods are async except :meth:`activity` (which returns an async context
    manager). Implementations must be safe to call concurrently for distinct
    ``dest`` values; the core serializes per conversation, never globally.
    """

    @property
    def limits(self) -> TransportLimits:
        """Numeric caps the core honors (message length, batch size, ...)."""
        ...

    async def open_thread(self, parent: ChatDest, title: str) -> ChatDest:
        """Open (or identify) the thread a conversation lives in.

        Discord anchors a real sub-thread to the triggering message; Slack mints
        no object and threads off the triggering ts. ``title`` is already
        truncated by the core to ``limits.thread_title_max`` (ignored by Slack).
        Returns the THREAD dest carrying the conversation ``thread_key``.
        """
        ...

    async def send_text(self, dest: ChatDest, text: str) -> None:
        """Post one plain-text message to ``dest``.

        The core has already chunked to ``limits.max_message_len`` (one call per
        chunk); the adapter MUST NOT re-chunk. The adapter applies platform
        formatting fixups (Discord passes through; Slack escapes ``& < >`` then
        translates markdown to mrkdwn before sending).
        """
        ...

    async def send_files(
        self, dest: ChatDest, caption: str, files: Sequence[OutgoingFile]
    ) -> None:
        """Post one message carrying ``caption`` plus one or more files.

        The core has already filtered by the size cap and split into batches of at
        most ``limits.max_attachments_per_msg``. ``caption`` is formatted by the
        adapter exactly like :meth:`send_text`.
        """
        ...

    async def download_attachment(
        self, attachment: AttachmentRef, target: Path
    ) -> None:
        """Download one inbound attachment to local ``target`` (parent exists).

        MUST raise on failure (the core catches it and marks the file skipped).
        Discord saves via the file object; Slack GETs ``attachment.url`` with a
        bearer header that survives the file-host redirect, and raises on an
        HTML login-page response before writing bytes.
        """
        ...

    async def add_reaction(self, message: IncomingMessage, emoji: str) -> None:
        """React to ``message`` with the cooldown 'busy' signal. Best-effort.

        The core passes the NEUTRAL name ``"hourglass"``; each adapter maps it to
        a platform glyph/shortcode (Discord ``⏳``; Slack
        ``hourglass_flowing_sand``).
        """
        ...

    def activity(self, dest: ChatDest) -> AbstractAsyncContextManager[None]:
        """An async context manager showing 'working' for its duration.

        Discord uses the channel typing indicator; Slack has none, so it returns
        :func:`noop_activity`. Non-async (returns a CM) so the core writes
        ``async with transport.activity(dest):``.
        """
        ...


def format_relayed_message(
    sender: str, question: str, platform: str = "Discord"
) -> str:
    """Prefix a relayed chat message with its sender for the fork.

    The daemon knows who sent each message; the forked Claude does not, so we
    prepend ``<name> (via <platform>) says:``. The persona explains this
    convention so Claude reads the prefix as the asker's identity. ``platform``
    comes from config (``TunnelConfig.platform``).
    """
    who = sender.strip() or "A teammate"
    return f"{who} (via {platform}) says:\n{question}"


def resolve_token(env_name: str, file_path: str = "") -> str:
    """Resolve a token: env var first, then an optional file (env wins).

    Generalized from the original ``resolve_token(cfg)`` so both the Discord bot
    token and the two Slack tokens share one resolver. An empty ``file_path``
    skips the file read; the path is ``expanduser()``-ed and read only if it
    exists.
    """
    token = os.environ.get(env_name, "").strip()
    if not token and file_path:
        path = Path(file_path).expanduser()
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

    Long names are shortened but keep their extension -- downstream code decides
    type/conversion from the suffix, so chopping `.docx` off the end would skip
    conversion and hand the fork an unreadable path.
    """
    base = os.path.basename(name or "").strip() or "file"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    if len(cleaned) <= 120:
        return cleaned
    ext = Path(cleaned).suffix
    if 1 < len(ext) <= 12:  # plausible extension -- keep it, trim the stem
        return cleaned[: -len(ext)][: 120 - len(ext)] + ext
    return cleaned[:120]


def _unique_name(name: str, used: set[str]) -> str:
    """`name` unless already in `used`, else suffixed `-2`/`-3`/... before the
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


def split_chunks(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """Split text into <=limit chunks, preferring newline boundaries.

    ``limit`` stays a defaulted keyword (2000) so the existing callers/tests and
    the ``discord_bot`` re-export keep working; core callers pass
    ``limit=transport.limits.max_message_len`` (Slack chunks to 4000).
    """
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
