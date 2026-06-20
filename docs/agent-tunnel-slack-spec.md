# agent-tunnel Slack bot — Implementation-Ready Specification

HYBRID shared-core refactor with FULL Discord parity. An engineer implements
directly from this. Every line reference is `claude_code_tools/agent_tunnel/<file>:<n>`,
verified against the live source. Every gap in the gap list is resolved inline and
collected in Section 11.

---

## 1. Goal and locked decisions

Add a Slack front-end to agent-tunnel with **full Discord parity**, by extracting a
platform-neutral **shared core** that both front-ends drive. No behavior change to
Discord.

**Locked decisions:**

- **Hybrid shared core.** Extract `chat_core.py` (routing + policy + the
  answer/ingest/deliver pipeline + the reaper) behind a `ChatTransport` Protocol.
  `discord_bot.py` and the new `slack_bot.py` become thin adapters: each normalizes
  its platform events into a neutral `IncomingMessage`, implements `ChatTransport`,
  constructs ONE `ChatCore`, and forwards messages. All policy lives in `chat_core`,
  so the two front-ends are behavior-identical by construction.
- **Full parity.** Every Discord behavior (allowlist ordering, cooldown-on-follow-ups,
  per-thread lock + global semaphore, stale-binding guard, mention-only-in-thread,
  DM rebind, attachment round-trip, deliverables, reaper) is reproduced for Slack via
  the shared core. The two cosmetic differences (reply-quote on the unknown-handle
  complaint; Discord-markdown vs Slack mrkdwn) are handled in the adapters and called
  out explicitly (Section 11, G-04 / G-03).
- **Slack Socket Mode, two tokens, no public endpoint.** Slack runs over Socket Mode
  (an outbound WebSocket — like Discord's Gateway). No Request URL, no open port, no
  cloud. Two tokens: a **bot token** (`xoxb-…`, all Web API calls) and an
  **app-level token** (`xapp-…`, scope `connections:write`, opens the socket). Each
  resolves from its own env var or file.
- **Phasing.** PR1 extracts `chat_core` and refactors Discord onto it (Discord stays
  green, zero behavior change). PR2 adds `slack_bot` + `SlackConfig` + `serve --chat`
  + tests. PR3 is docs.

---

## 2. Architecture: the `chat_core` seam

New file `claude_code_tools/agent_tunnel/chat_core.py`. Because the full module with
google-style docstrings + verbatim pipeline bodies would approach the 1000-line limit
(G-22), it is **split into two files**:

- `claude_code_tools/agent_tunnel/chat_types.py` — the Protocol + all value objects +
  `TransportLimits` + `OutgoingFile` + `noop_activity` + the neutral module helpers
  (`format_relayed_message`, `is_close_command`, `is_list_command`, `CLOSE_COMMANDS`,
  `LIST_COMMANDS`, `_safe_filename`, `_unique_name`, `split_chunks`, `resolve_token`).
  Estimate ~430 lines.
- `claude_code_tools/agent_tunnel/chat_core.py` — `ChatCore` (routing + pipeline +
  reaper), importing from `chat_types`. Estimate ~560 lines (moved bodies ~330 +
  docstrings/wiring ~230).

`chat_core.py` re-exports the `chat_types` names so callers can `from .chat_core
import IncomingMessage, ChatCore, split_chunks, …` uniformly.

### 2a. `ChatTransport` Protocol + value objects (`chat_types.py`)

```python
"""Platform-neutral seam between a chat front-end and the answer pipeline.

``ChatCore`` (in chat_core.py) owns ALL routing and policy and reaches the
outside world only through :class:`ChatTransport` plus the normalized
dataclasses defined here. A front-end supplies an adapter that drops bot/self
events, normalizes each surviving event into an :class:`IncomingMessage`,
implements :class:`ChatTransport`, builds one ``ChatCore``, schedules the
reaper, and forwards each message via ``await core.handle_message(msg)``. No
platform identifier ever appears in this module.
"""

from __future__ import annotations

import os
import re
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Protocol, Sequence, runtime_checkable

DISCORD_MSG_LIMIT = 2000        # kept for the discord_bot re-export
THREAD_NAME_MAX = 90            # kept for the discord_bot re-export


@dataclass(frozen=True)
class TransportLimits:
    """Per-platform numeric caps the core honors when chunking/batching/truncating.

    Declared once per adapter instance (read via ``ChatTransport.limits``).
    Replaces the hard-coded Discord constants (``DISCORD_MSG_LIMIT``, the literal
    10 attachments/msg, ``THREAD_NAME_MAX``, the ``[:1500]`` error truncation, the
    ``[:1900]`` list truncation). Defaults are Discord's, so a Discord adapter that
    omits them reproduces the shipped bot byte-for-byte.

    INVARIANT (enforced by the core, G-08): ``list_truncate_len <= max_message_len``.
    ``_list_handles`` truncates to ``min(list_truncate_len, max_message_len)`` so the
    'transport must not re-chunk send_text' contract can never be violated.

    NOTE on Slack max_message_len (G-19): Slack's chat.postMessage recommends 4000
    and HARD-truncates at 40000. Use 4000; do NOT raise toward 40000 (silent
    truncation). The answer.md preview is ``split_chunks(text, limit)[0]`` so it is
    also <= max_message_len.
    """

    max_message_len: int = 2000
    max_attachments_per_msg: int = 10
    thread_title_max: int = 90
    max_error_len: int = 1500
    list_truncate_len: int = 1900


@dataclass(frozen=True)
class OutgoingFile:
    """A file the core asks the transport to post, by path OR by bytes.

    Exactly one of ``data`` / ``path`` is set. Unifies the two Discord upload cases:
    ``discord.File(BytesIO(bytes), filename=...)`` (the long-answer ``answer.md``)
    and ``discord.File(path, filename=...)`` (a deliverable).

    ``filename`` MUST carry the extension; on Slack the adapter also passes it as
    ``title`` because Slack strips the extension from the filename alone (G-13).
    """

    filename: str
    data: Optional[bytes] = None
    path: Optional[Path] = None

    @classmethod
    def from_bytes(cls, data: bytes, filename: str) -> "OutgoingFile":
        return cls(filename=filename, data=data)

    @classmethod
    def from_path(cls, path: Path) -> "OutgoingFile":
        return cls(filename=path.name, path=path)


@asynccontextmanager
async def noop_activity() -> AsyncIterator[None]:
    """A do-nothing ``activity()`` for transports with no typing API (Slack)."""
    yield


@runtime_checkable
class ChatTransport(Protocol):
    """Every chat I/O the answer pipeline needs, abstracted per platform.

    ONE instance per running bot (NOT per turn): the core passes a
    :class:`ChatDest` to every send, so one instance serves all destinations and
    the reaper needs none. All methods are async except :meth:`activity` (returns
    an async context manager). Implementations must be safe to call concurrently for
    distinct ``dest`` values; the core serializes per conversation, never globally.
    """

    @property
    def limits(self) -> TransportLimits: ...

    async def open_thread(self, parent: "ChatDest", title: str) -> "ChatDest":
        """Open (or identify) the thread a conversation lives in.

        Discord: ``await parent._native.create_thread(name=title)`` -> a real
        sub-thread; returned dest carries ``thread_key=f"th:{thread.id}"`` and
        ``_native=thread``.

        Slack (G-18): no object is created. The triggering message's ts is the
        parent. Returned dest carries ``thread_key=f"th:{parent.channel_id}-{ts}"``,
        ``thread_ts=ts``, ``channel_id=parent.channel_id``; the thread materializes
        on the first send that passes ``thread_ts``. The transport reads ``ts`` from
        ``parent.thread_ts`` (the channel-surface parent dest carries the triggering
        message ts there — see ChatDest). ``title`` is ignored.

        ``title`` is already truncated by the core to ``limits.thread_title_max``.
        """
        ...

    async def send_text(self, dest: "ChatDest", text: str) -> None:
        """Post one plain-text message to ``dest``.

        The core has already chunked to ``limits.max_message_len`` (one call per
        chunk); the adapter MUST NOT re-chunk. The adapter applies platform
        formatting fixups (G-03): Discord passes through; Slack escapes ``& < >``
        FIRST, then translates ``**bold**``->``*bold*``, ``[t](u)``->``<u|t>``, all
        BEFORE any platform send. Discord: ``dest._native.send(text)``. Slack:
        ``chat_postMessage(channel=dest.channel_id, thread_ts=dest.thread_ts or
        None, text=<translated>)`` — the SAME thread_ts on every send (G-25).
        """
        ...

    async def send_files(
        self, dest: "ChatDest", caption: str, files: "Sequence[OutgoingFile]"
    ) -> None:
        """Post one message carrying ``caption`` plus one or more files.

        The core has already filtered by the size cap and split into batches of at
        most ``limits.max_attachments_per_msg``. ``caption`` is markdown-translated
        by the adapter exactly like ``send_text`` (G-03).

        Discord: a ``discord.File`` per entry (``BytesIO(data)`` or ``str(path)``),
        ``dest._native.send(caption, files=[...])``.

        Slack (G-13): one ``files_upload_v2(channel=dest.channel_id,
        thread_ts=dest.thread_ts or None, initial_comment=<translated caption>,
        file_uploads=[...])`` where each entry is
        ``{"content": of.data, "filename": of.filename, "title": of.filename}`` when
        ``of.data is not None`` else
        ``{"file": str(of.path), "filename": of.filename, "title": of.filename}``.
        ``channel`` is SINGULAR in v2. ``title`` carries the extension. Same
        thread_ts as every other send in the conversation (G-25).
        """
        ...

    async def download_attachment(
        self, attachment: "AttachmentRef", target: Path
    ) -> None:
        """Download one inbound attachment to local ``target`` (parent exists).

        MUST raise on failure (the core catches it and marks the file skipped).

        Discord: ``await attachment._native.save(str(target))``.

        Slack (G-15): GET ``attachment.url`` (which is ``url_private_download``)
        with ``Authorization: Bearer <bot_token>``. Slack file URLs REDIRECT to a
        CDN host and naive clients STRIP the auth header on the cross-origin hop,
        landing on a 200 + text/html LOGIN PAGE. The transport MUST (a) keep the
        bearer header across the file-host redirect (disable auto-redirect and
        re-attach, or verify the client forwards it), and (b) raise if the response
        Content-Type is ``text/html`` (auth/scope failure), BEFORE writing bytes.
        """
        ...

    async def add_reaction(self, message: "IncomingMessage", emoji: str) -> None:
        """React to ``message`` with the cooldown 'busy' signal. Best-effort.

        The core passes the NEUTRAL name ``"hourglass"``; each adapter maps it to a
        platform glyph/shortcode (G-02). Discord: ``{"hourglass": "⏳"}`` then
        ``message._native.add_reaction("⏳")``. Slack:
        ``reactions_add(channel=message.channel_id, timestamp=message.ts,
        name="hourglass_flowing_sand")`` (the ⏳ flowing-sand glyph, matching
        Discord's U+23F3 — verified, not ⌛).
        """
        ...

    def activity(self, dest: "ChatDest") -> AbstractAsyncContextManager[None]:
        """An async context manager showing 'working' for its duration.

        Discord: ``dest._native.typing()``. Slack: :func:`noop_activity` (Slack has
        no generic typing API over Socket Mode). Non-async (returns a CM) so the core
        writes ``async with transport.activity(dest):``.
        """
        ...
```

### 2b. Normalized dataclasses (`chat_types.py`)

```python
class Surface(Enum):
    """Which routing branch an inbound message belongs to.

    The adapter classifies; the core routes on this. Replaces Discord's
    ``isinstance(channel, Thread/DMChannel)`` and Slack's ``channel_type`` switch.
    """

    CHANNEL = "channel"  # watched channel, not in a thread (handle-opens-thread)
    THREAD = "thread"    # follow-up inside an already-bound thread
    DM = "dm"            # a 1:1 direct message (Slack ``im`` AND ``mpim``)


class Addressee(Enum):
    """Who a message's *leading* mention addresses, if anyone.

    The adapter computes the FINAL verdict from its own mention wire-format AND
    pre-strips a SELF mention from ``IncomingMessage.text``. The core never parses
    mentions or compares ids — it branches on this enum, and ONLY on the THREAD
    surface (CHANNEL uses the handle-token parse; DM is 1:1).
    """

    NONE = "none"    # no leading mention -> answer (in a thread)
    SELF = "self"    # leading mention IS this bot -> (already stripped) answer
    OTHER = "other"  # someone else / broadcast / role/usergroup -> stay out silent


@dataclass(frozen=True)
class AttachmentRef:
    """One inbound file, normalized across platforms.

    The core uses only ``filename`` / ``size`` (caps + safe naming) and hands the
    whole ref back to :meth:`ChatTransport.download_attachment`. ``url`` / ``_native``
    are opaque to the core.

    Discord adapter builds: ``AttachmentRef(a.filename, getattr(a,"size",0) or 0,
    _native=a)``.

    Slack adapter builds (G-16): ``filename = f.get("name") or f"file-{f.get('id','x')}"``;
    ``size = int(f.get("size") or 0)``; ``url = f.get("url_private_download") or
    f.get("url_private") or ""``; ``_native = f`` (the raw file dict).
    """

    filename: str
    size: int            # bytes; 0 if the platform did not report a size
    url: str = ""        # authenticated download URL (Slack); "" on Discord
    _native: Any = None  # platform file object, for the adapter's downloader


@dataclass(frozen=True)
class ChatDest:
    """A place the core sends replies to, with a stable conversation key.

    Neutral replacement for the Discord ``dest`` threaded through
    ``_answer``/``_list_handles``/``_close``. The ``thread_key`` is THE universal
    store/backend/uploads/locks key (the ``th:``/``dm:`` scheme), minted by the
    adapter. ``paths.safe_key``/``backends._window_name`` already sanitize arbitrary
    key text, so the Slack key form needs no downstream change (G-05).

    Attributes:
        thread_key: Universal conversation key. Discord ``th:{thread.id}`` /
            ``dm:{channel.id}``; Slack ``th:{channel}-{thread_ts}`` / ``dm:{channel}``.
            For a CHANNEL dest (used by ``!list`` and as ``open_thread``'s parent
            before a thread exists) this is "" and is never used as a key.
        surface: Which branch this dest serves.
        channel_id: Normalized channel/conversation id (``str``); addresses Slack
            sends. "" where unused on Discord.
        thread_ts: Slack thread anchor. For a THREAD dest it is the parent ts. For a
            CHANNEL parent dest passed to ``open_thread`` it carries the TRIGGERING
            message's ts so the Slack transport can build ``th:{channel}-{ts}`` and
            thread off it (G-18). "" on Discord.
        _native: Platform send-target (Discord channel/thread/DMChannel); unused on
            Slack.
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
        text: Body, ``.strip()``-ed. For a THREAD message whose leading mention was
            this bot, the adapter has ALSO stripped that mention. May be "" when
            ``attachments`` is non-empty (attachment-only message — NOT dropped).
        dest: Where replies go (+ the conversation ``thread_key`` + ``surface``).
            For a CHANNEL message this is the parent dest passed to ``open_thread``;
            on Slack it carries ``thread_ts = ts`` (the triggering ts).
        surface: CHANNEL / THREAD / DM.
        author_id: Stable per-user principal id (``str``); keys cooldown + allowlist.
        author_display: Display name for logs and the ``<name> (via X) says:`` prefix.
            Discord ``display_name``; Slack defaults to the raw user id on the pre-ack
            path and is resolved via cached ``users_info`` INSIDE the background task
            (G-14). Falls back to ``author_id``.
        author_role_ids: Principal's role/usergroup ids (``str``) for the allowlist
            role check. Discord role ids; Slack usergroup (subteam ``S…``) ids, or
            empty when ``usergroups:read`` is absent/usergroups unavailable (G-12).
        addressee: Leading-mention verdict. Meaningful only on THREAD; CHANNEL and DM
            leave it NONE.
        attachments: Normalized inbound files (possibly empty).
        channel_id: Normalized channel/conversation id.
        ts: Platform message id/timestamp — Slack ``ts`` (needed for reactions +
            threading). Discord leaves "" (it reacts/threads via ``_native``).
        _native: Raw platform message, used only by transport I/O targeting THIS
            message (Discord ``add_reaction``).
    """

    text: str
    dest: ChatDest
    surface: Surface
    author_id: str
    author_display: str = ""
    author_role_ids: "frozenset[str]" = frozenset()
    addressee: Addressee = Addressee.NONE
    attachments: "tuple[AttachmentRef, ...]" = ()
    channel_id: str = ""
    ts: str = ""
    _native: Any = None

    @property
    def has_content(self) -> bool:
        """True if there is text or at least one attachment (the empty-drop rule)."""
        return bool(self.text) or bool(self.attachments)
```

### 2c. The pipeline API (`chat_types.py` helpers + `chat_core.ChatCore`)

Module-level neutral helpers move verbatim from `discord_bot.py` into `chat_types.py`:
`format_relayed_message` (`:52-64`), `is_close_command`/`is_list_command` + their sets
(`:77-88`), `_safe_filename` (`:91-105`), `_unique_name` (`:108-120`). Two are
generalized:

```python
def split_chunks(text: str, limit: int = 2000) -> list[str]:
    """Split ``text`` into <=``limit`` chunks, preferring newline boundaries.

    Behavior-IDENTICAL to the shipped ``discord_bot.split_chunks`` — ``limit`` stays
    a DEFAULTED keyword (G-21). The shipped default was ``DISCORD_MSG_LIMIT`` (2000)
    and 2000 is correct; the existing test ``split_chunks("")`` and the
    ``discord_bot`` re-export both keep working. Core callers always pass
    ``limit=self.tx.limits.max_message_len`` so Slack chunks to 4000.
    """
    # ... body verbatim from discord_bot.py:140-159 ...


def resolve_token(env_name: str, file_path: str = "") -> str:
    """Resolve a token: env var first, then an optional file (env wins).

    Generalized from ``discord_bot.resolve_token(cfg)`` (``:67-74``). The
    short-circuit is preserved EXACTLY (G-06): an empty ``file_path`` skips the file
    read; the path is ``expanduser()``-ed and read only ``if it exists``.
    """
    token = os.environ.get(env_name, "").strip()
    if not token and file_path:
        p = Path(file_path).expanduser()
        if p.exists():
            token = p.read_text(encoding="utf-8").strip()
    return token
```

The `ChatCore` engine (`chat_core.py`) holds the per-run concurrency primitives and
the cooldown map (formerly the `run_bot` closure), runs every policy decision against
the `ChatTransport` + neutral `store`/`registry`/backends, and owns the reaper loop.

```python
class ChatCore:
    """Platform-neutral routing + answer/ingest/deliver pipeline + reaper.

    ONE instance per running bot. The adapter builds
    ``core = ChatCore(cfg, store, registry, transport)``, schedules
    ``core.run_reaper()`` on its loop, and for each accepted, normalized event awaits
    ``core.handle_message(msg)``.
    """

    def __init__(self, cfg, store, registry, transport: ChatTransport) -> None:
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self.tx = transport
        self._sem = asyncio.Semaphore(cfg.limits.max_concurrent)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_ask: dict[str, float] = {}

    async def handle_message(self, msg: IncomingMessage) -> None: ...
    def _allowed(self, msg: IncomingMessage) -> bool: ...
    def _cooldown_ok(self, author_id: str) -> bool: ...
    async def _on_channel(self, msg: IncomingMessage) -> None: ...
    async def _on_thread(self, msg: IncomingMessage) -> None: ...
    async def _on_direct(self, msg: IncomingMessage) -> None: ...
    async def _list_handles(self, dest: ChatDest) -> None: ...
    async def _close(self, dest: ChatDest) -> None: ...
    async def _answer(self, dest, question, msg) -> None: ...
    async def _deliver_answer(self, dest, text) -> None: ...
    async def _ingest_attachments(self, dest, thread_key, question, attachments) -> str: ...
    async def _post_deliverables(self, dest, answer) -> None: ...
    async def run_reaper(self) -> None: ...
    def _reap_all(self) -> int: ...
```

Method bodies are specified below (Section 3 maps source → core; the behaviors that
matter for correctness are quoted in Sections 3–4 and 11). Two policy points the core
must implement, made explicit:

- `_allowed` reads `cfg.principal_allowlists()` (NOT `cfg.discord.*` directly), so
  Slack usergroups are honored (G-02-allowlist / Section 6):
  ```python
  def _allowed(self, msg: IncomingMessage) -> bool:
      uids, rids = self.cfg.principal_allowlists()   # both set[str]
      if not uids and not rids:
          return True
      if msg.author_id in uids:
          return True
      return bool(msg.author_role_ids & rids)
  ```
- `_list_handles` truncates to `min(list_truncate_len, max_message_len)` and sends ONE
  message (preserves the shipped `[:1900]` single-message semantics — NOT paginated,
  G-08):
  ```python
  limit = min(self.tx.limits.list_truncate_len, self.tx.limits.max_message_len)
  await self.tx.send_text(dest, "\n".join(lines)[:limit])
  ```

The `_answer` body is the verbatim port of `discord_bot.py:470-595` with these exact
substitutions, preserving every watch-list invariant (Section 11):

1. audit log (per-message `msg.author_display` preferred over bind-time `rec.asker`);
2. `lock.locked()` -> "still working" `send_text` notice, THEN `async with lock, sem`;
3. stale-binding guard (`store.get` re-read; compare `expert_session_id` +
   `created_at`; mismatch -> "restarted" notice + return) — inside the lock;
4. `async with self.tx.activity(dest):` wraps `_ingest_attachments` + the empty-after-
   ingest guard + `format_relayed_message(msg.author_display, question, cfg.platform)`
   + `to_thread(backend_for_record(...).ask, key, question)` — ingest-time skipped/
   unreadable notices fire INSIDE this block, under the lock/sem (G-09);
5. `BackendError` -> `send_text(dest, f"⚠️ {str(exc)[:limits.max_error_len]}")`; other
   `Exception` -> generic "Unexpected error" `send_text`; both return inside lock/sem;
6. lock+sem RELEASED before delivery;
7. `_deliver_answer` (long -> `OutgoingFile.from_bytes(text.encode(), "answer.md")` +
   preview; else `send_text` per `split_chunks(text, limits.max_message_len)`), then
   `_post_deliverables`.

`_ingest_attachments` is the verbatim port of `:597-677` with: `attlist =
list(attachments)` materialized ONCE (G-05); `att.size or 0`; the single download call
routed through `await self.tx.download_attachment(att, target)` (raises -> skipped);
`uploads_dir_for(self.cfg.state_path.parent, thread_key) / uuid.uuid4().hex[:8]`.

`_post_deliverables` is the verbatim port of `:679-715` with batching by
`self.tx.limits.max_attachments_per_msg` (was the literal 10) and each batch via
`send_files` with `OutgoingFile.from_path`.

`run_reaper` / `_reap_all` are the verbatim ports of `:199-225`; only the SCHEDULING
moves to the adapter.

---

## 3. `chat_core` extraction map (function by function)

### MOVES out of `discord_bot.py`

| From `discord_bot.py` | To | Form |
|---|---|---|
| `format_relayed_message` `:52-64` | `chat_types.py` | verbatim |
| `CLOSE_COMMANDS`/`LIST_COMMANDS` `:77-78`, `is_close_command`/`is_list_command` `:81-88` | `chat_types.py` | verbatim |
| `_safe_filename` `:91-105`, `_unique_name` `:108-120` | `chat_types.py` | verbatim |
| `split_chunks` `:138-159` | `chat_types.py` | verbatim (`limit` stays defaulted, G-21) |
| `resolve_token(cfg)` `:67-74` | `chat_types.py` as `resolve_token(env_name, file_path)` | generalized (G-06); Discord wrapper STAYS in `discord_bot.py`, G-23 |
| `DISCORD_MSG_LIMIT` `:47`, `THREAD_NAME_MAX` `:49` | `chat_types.TransportLimits` defaults (+ kept as module consts for the Discord re-export) | constants |
| `sem`/`locks`/`last_ask` closure `:189-191` | `ChatCore._sem`/`_locks`/`_last_ask` | `last_ask` keyed on `str` |
| `on_message` body `:251-267` | `ChatCore.handle_message` | the empty-drop + surface dispatch; the bot-drop and channel-type classification STAY in the adapter |
| `_on_channel_message` `:269-327` | `ChatCore._on_channel` | verbatim logic; `create_thread`/`reply`/`send`/`attachments` -> transport |
| `_on_thread_message` `:329-362` | `ChatCore._on_thread` | verbatim; mention now `msg.addressee`; `add_reaction` -> transport |
| `_on_direct` `:364-433` | `ChatCore._on_direct` | verbatim incl. the forget+bind-under-lock rebind dance |
| `_list_handles` `:435-450` | `ChatCore._list_handles` | verbatim; `min(...)` truncation (G-08) |
| `_close` `:452-468` | `ChatCore._close` | verbatim |
| `_answer` `:470-595` | `ChatCore._answer` + `_deliver_answer` | verbatim (Section 2c substitutions) |
| `_ingest_attachments` `:597-677` | `ChatCore._ingest_attachments` | verbatim; download via transport |
| `_post_deliverables` `:679-715` | `ChatCore._post_deliverables` | verbatim; batch by `limits` |
| `_allowed` `:234-242`, `_cooldown_ok` `:244-249` | `ChatCore._allowed` (via `principal_allowlists`), `_cooldown_ok` (str key) | adapted |
| `_reaper` `:199-207`, `_reap_all` `:209-225` | `ChatCore.run_reaper`, `_reap_all` | verbatim |

### STAYS in `discord_bot.py` (the Discord adapter)

- `import discord` (lazy), `intents.message_content = True`, `class TunnelClient(
  discord.Client)`, `on_ready`, `TunnelClient(...).run(token, log_handler=None)`, and
  `run_bot(cfg, store, registry)` (same signature — the reusable seam).
- `_leading_mention_id` `:123-135` + the SELF-strip regex `:346` — Discord mention
  wire-format, now adapter-private, folded into computing the 3-valued `Addressee`.
- A thin `resolve_token(cfg)` wrapper (G-23) delegating to
  `chat_types.resolve_token(cfg.discord.token_env, cfg.discord.token_file)` so
  `test_resolve_token` and `doctor` keep their 1-arg call.
- `on_message` reduced to a normalizer + dispatcher (Section 4).
- `DiscordTransport(ChatTransport)` (Section 4).

**`cli.ask` is unaffected** — it binds `cli:{thread}` keys and calls `backend.ask`
directly, NOT through `ChatCore` (it never went through `_answer`). The moved
ingest/deliver code is therefore covered SOLELY by `test_chat_core` (G-26); note this
in the spec/tests.

---

## 4. `discord_bot.py` refactor (behavior-preserving)

Steps to delegate to the core so Discord stays green:

1. Add `from .chat_types import (...)` and `from .chat_core import ChatCore`. Keep
   `import discord` lazy inside `run_bot`. Keep the `resolve_token(cfg)` wrapper.
2. In `run_bot`, keep the two startup gates verbatim (`:174-184`): token-missing
   `RuntimeError` (message still names `cfg.discord.token_env`/`discord.token_file`)
   and the channels-or-DMs gate.
3. In `setup_hook`: build `self.tx = DiscordTransport(self)`, `self.core =
   ChatCore(cfg, store, registry, self.tx)`, `self.loop.create_task(
   self.core.run_reaper())`.
4. In `on_ready`: set `self.tx.bot_user_id = self.user.id` (used by the Addressee
   SELF/OTHER comparison), keep the existing log.
5. Replace `on_message` with a drop + classify + normalize + forward:

```python
async def on_message(self, m: discord.Message) -> None:
    if m.author.bot:                       # self-drop relies on author.bot (G-10)
        return
    ch = m.channel
    if isinstance(ch, discord.Thread):
        surface, key = Surface.THREAD, f"th:{ch.id}"
    elif isinstance(ch, discord.DMChannel):
        if not cfg.discord.respond_to_dms:
            return
        surface, key = Surface.DM, f"dm:{ch.id}"
    elif ch.id in cfg.discord.channel_ids:
        surface, key = Surface.CHANNEL, ""    # thread minted on open
    else:
        return
    await self.core.handle_message(self._to_incoming(m, surface, key))

def _to_incoming(self, m, surface, key) -> IncomingMessage:
    content = (m.content or "").strip()
    addressee, text = Addressee.NONE, content
    if surface is Surface.THREAD:
        mid = _leading_mention_id(content)
        if mid is not None:
            if mid != getattr(self.user, "id", None):
                addressee = Addressee.OTHER
            else:
                addressee = Addressee.SELF
                text = re.sub(r"^\s*<@[!&]?\d+>\s*", "", content)
    ch = m.channel
    dest = ChatDest(thread_key=key, surface=surface,
                    channel_id=str(ch.id), _native=ch)
    return IncomingMessage(
        text=text, dest=dest, surface=surface,
        author_id=str(m.author.id),
        author_display=m.author.display_name,
        author_role_ids=frozenset(
            str(r.id) for r in getattr(m.author, "roles", []) or []),
        addressee=addressee,
        attachments=tuple(
            AttachmentRef(a.filename, getattr(a, "size", 0) or 0, _native=a)
            for a in m.attachments),
        channel_id=str(ch.id), _native=m,
    )
```

6. `DiscordTransport(ChatTransport)` holding the client:

- `limits` -> `TransportLimits()` (Discord defaults; reproduces the shipped numbers).
- `open_thread(parent, title)` -> `t = await parent._native.create_thread(name=title)`;
  return `ChatDest(thread_key=f"th:{t.id}", surface=Surface.THREAD,
  channel_id=str(t.id), _native=t)`.
- `send_text(dest, text)` -> `await dest._native.send(text)` (Discord renders markdown;
  no translation).
- `send_files(dest, caption, files)` -> build `discord.File(io.BytesIO(of.data),
  filename=of.filename)` when `of.data is not None` else `discord.File(str(of.path),
  filename=of.filename)`; `await dest._native.send(caption, files=[...])`.
- `download_attachment(att, target)` -> `await att._native.save(str(target))`.
- `add_reaction(message, emoji)` -> `await message._native.add_reaction(
  {"hourglass": "⏳"}.get(emoji, emoji))`.
- `activity(dest)` -> `dest._native.typing()`.

7. The **unknown-handle reply** (`:283`, `message.reply(...)`) now routes through
   `send_text(msg.dest, ...)` in `_on_channel` (G-04). The complaint text is
   identical; the reply-quote affordance is dropped (documented).

**Net:** every routing/policy decision is now in `ChatCore`; `discord_bot.py` is the
adapter shape above. The existing `tests/test_discord_bot.py` (which imports
`format_relayed_message` from `discord_bot`) keeps passing because `discord_bot`
re-exports it from `chat_types`.

---

## 5. `slack_bot.py`

New file `claude_code_tools/agent_tunnel/slack_bot.py`. `slack_bolt` is imported
LAZILY inside `run_slack_bot` (exactly like `discord_bot` defers `discord`), so
`import slack_bot` for the pure helpers never requires the dep.

### 5a. Socket Mode setup (two tokens) + lifecycle + ordering

```python
def run_slack_bot(cfg, store, registry) -> None:
    """Run the Slack bot until interrupted (blocking). Mirrors run_bot's signature.

    Raises:
        RuntimeError: a token is missing, auth.test fails, or no channels+no DMs.
    """
    import asyncio
    from slack_bolt.app.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    bot_token = resolve_token(cfg.slack.bot_token_env, cfg.slack.bot_token_file)
    app_token = resolve_token(cfg.slack.app_token_env, cfg.slack.app_token_file)
    if not bot_token:
        raise RuntimeError(
            f"No Slack bot token (set {cfg.slack.bot_token_env} or "
            "slack.bot_token_file)")
    if not app_token:
        raise RuntimeError(
            f"No Slack app-level token (set {cfg.slack.app_token_env} or "
            "slack.app_token_file)")
    if not cfg.slack.channel_ids and not cfg.slack.respond_to_dms:
        raise RuntimeError(
            "No slack.channel_ids configured and DMs are disabled — "
            "the bot would never respond.")

    app = AsyncApp(token=bot_token)
    transport = SlackTransport(app, bot_token, cfg)
    core = ChatCore(cfg, store, registry, transport)
    channel_ids = {str(c) for c in cfg.slack.channel_ids}
    seen = _SeenCache(cap=4096)            # bounded TTL dedup (G-24)
    state = {"bot_user_id": "", "bot_id": ""}
    inflight: set[asyncio.Task] = set()    # bounded fan-out tracking (G-27)

    @app.event("message")                  # NO app_mention listener (G-double-answer)
    async def _on_message(event, body):
        # SYNCHRONOUS pre-ack work only (G-14): drop, classify, dedup, build a
        # lightweight IncomingMessage (author_display = raw id), then spawn + return.
        msg = slack_event_to_incoming(
            event, bot_user_id=state["bot_user_id"], bot_id=state["bot_id"],
            channel_ids=channel_ids, respond_to_dms=cfg.slack.respond_to_dms,
        )
        if msg is None:
            return
        key = (event.get("client_msg_id")
               or f'{event.get("channel")}-{event.get("ts")}')
        if not seen.add(key):              # already seen -> retry/dup, skip (G-24)
            return
        if len(inflight) >= _MAX_INFLIGHT:             # fan-out bound (G-27)
            # The busy notice is a chat_postMessage round-trip, so it is NOT
            # awaited on the pre-ack path (G-14): spawn it fire-and-forget
            # (tracked so it isn't GC'd) and return immediately.
            notice = asyncio.create_task(transport.send_text(msg.dest, BUSY))
            inflight.add(notice)
            notice.add_done_callback(inflight.discard)
            return
        task = asyncio.create_task(_run_turn(core, transport, msg))
        inflight.add(task)
        task.add_done_callback(inflight.discard)

    async def _main():
        try:                                           # BEFORE the socket (G-20)
            auth = await app.client.auth_test()
        except Exception as exc:                       # SlackApiError is NOT a
            raise RuntimeError(                        # RuntimeError -> wrap it
                f"Slack auth.test failed (check the bot token): {exc}") from exc
        state["bot_user_id"], state["bot_id"] = auth["user_id"], auth["bot_id"]
        handler = AsyncSocketModeHandler(app, app_token)
        reaper = asyncio.create_task(core.run_reaper())  # same loop (G-28)
        try:
            await handler.start_async()                # blocks, serves events
        finally:
            reaper.cancel()
            await handler.close_async()
            await transport.aclose()                   # close download session

    asyncio.run(_main())                   # asyncio.run handles KeyboardInterrupt;
                                           # cli.main() maps it to exit(130) (G-shutdown)
```

`_run_turn` resolves the display name in the BACKGROUND (off the ack path, G-14), then
calls the core (it closes over the app via `transport`, so `app` is not a parameter):

```python
async def _run_turn(core, transport, msg) -> None:
    name = await transport.resolve_display(msg.author_id)   # cached users_info
    msg = replace(msg, author_display=name)                 # dataclasses.replace
    await core.handle_message(msg)
```

Ordering invariants made explicit:

- `auth.test` is awaited and `bot_user_id`/`bot_id` stored BEFORE `start_async()`, so
  the self/bot drop is never bypassed on the first event (G-20). It runs in `_main`
  BEFORE the socket opens, and a bad/expired token raises `SlackApiError` (a plain
  `Exception`, NOT a `RuntimeError`), so it is WRAPPED into a `RuntimeError` that
  `serve`'s `except RuntimeError` maps to a clean `ClickException` (not a traceback).
- The reaper task is created on the same running loop as the per-turn tasks (G-28).
- Graceful shutdown: `asyncio.run` raises `KeyboardInterrupt` on Ctrl-C; the `finally`
  cancels the reaper, `close_async()`s the socket, AND `await transport.aclose()`s the
  lazily created `aiohttp` download session (so it doesn't leak / warn "Unclosed client
  session"); in-flight turns finish on their own per-thread locks (the cap below bounds
  them). `cli.main()`'s existing `KeyboardInterrupt -> exit(130)` still applies since
  `run_slack_bot` is called synchronously from `serve` (G-shutdown).
- Fan-out bound (G-27): `inflight` tracks spawned turns. Beyond a threshold the
  listener posts a brief busy notice and skips spawning, instead of growing unbounded
  pending coroutines. That notice is a `chat_postMessage` round-trip, so it is spawned
  FIRE-AND-FORGET (tracked, then `return`) rather than awaited, keeping the listener
  body free of any awaited Web API call on the pre-ack path even under overload (G-14).
  (Per-conversation serialization is already guaranteed by the core's per-`thread_key`
  lock; the global `Semaphore(max_concurrent)` still caps simultaneous backend asks.)

### 5b. The event → `IncomingMessage` parser (pure, `slack_bolt`-free)

A free function, imported by tests with NO socket and NO `slack_bolt`:

```python
def slack_event_to_incoming(
    event: dict,
    *,
    bot_user_id: str,
    bot_id: str,
    channel_ids: set[str],
    respond_to_dms: bool = False,
    resolve_display=lambda uid: uid,
) -> Optional[IncomingMessage]:
    """Normalize a Slack message event to an IncomingMessage, or None to drop.

    Drop rules, in order:
    - subtype in the IGNORE set (G-subtype): message_changed, message_deleted,
      bot_message, channel_join, channel_leave, channel_topic, channel_purpose,
      channel_name, me_message, thread_broadcast, channel_archive,
      channel_unarchive. Edits/deletes are INTENTIONALLY ignored.
    - self/bot loop (G-self): event.get("bot_id") == bot_id, OR
      event.get("subtype") == "bot_message", OR event.get("user") == bot_user_id.
    - NOTE: do NOT gate on `subtype is None` at the decorator/function entry — a
      file-only upload arrives on a regular message; detect files by
      `event.get("files")` regardless of subtype (G-subtype).

    Classify (G-im-mpim):
    - channel_type in ("im","mpim") -> Surface.DM, thread_key=f'dm:{channel}',
      channel_id=channel. (respond_to_dms gates DMs; if False, return None.)
    - else (channel/group): if `thread_ts` present and `thread_ts != ts`
      -> Surface.THREAD, thread_key=f'th:{channel}-{thread_ts}',
      thread_ts=thread_ts. A parent (thread_ts == ts or absent) in a watched
      channel -> Surface.CHANNEL. A channel NOT in `channel_ids` -> None (the
      watched-channel gate, adapter-side; DMs are not gated by channel_ids).

    Leading mention -> Addressee (THREAD surface only):
    - `<@BOTUSERID>` leading -> Addressee.SELF and STRIP it from text.
    - `<@OTHER>` / `<!here>` / `<!channel>` / `<!subteam^...>` leading
      -> Addressee.OTHER (text unstripped). CHANNEL/DM leave NONE.

    Attachments: AttachmentRef per files[] entry (filename/size/url fallbacks, G-16),
    _native = the raw file dict. An attachment-only (text=="") message is NOT dropped.

    author_display: resolve_display(user) (defaults to the raw id; the live bot passes
    a no-op here and resolves in the background task, G-14).
    """
```

The Slack mention regexes (adapter-private): leading self
`^\s*<@%s>\s*` % re.escape(bot_user_id); leading other-user `^\s*<@[UW][A-Z0-9]+>`;
broadcast `^\s*<!(here|channel|everyone)>` and `^\s*<!subteam\^[A-Z0-9]+>`.

**CHANNEL-surface mention rule (G-broadcast):** on CHANNEL the handle-token parse
governs and `addressee` is left NONE, matching Discord. So `<!here> pay …` in a
watched channel is parsed as token `<!here>` (not a handle, fails `HANDLE_RE`) and is
silently dropped — intended.

### 5c. `SlackTransport(ChatTransport)`

Holds `app`, `bot_token`, `cfg`, a `dict[str,str]` display-name cache, and an
`aiohttp` session (lazy).

- `limits` -> `TransportLimits(max_message_len=4000, max_attachments_per_msg=10,
  thread_title_max=10**9, max_error_len=3500, list_truncate_len=3900)`. Slack ignores
  titles (`thread_title_max` huge); `list_truncate_len <= max_message_len` holds (G-08,
  G-19).
- `open_thread(parent, title)` -> `ts = parent.thread_ts` (the triggering ts carried on
  the channel parent dest, G-18); return `ChatDest(thread_key=f"th:{parent.channel_id}-{ts}",
  surface=Surface.THREAD, channel_id=parent.channel_id, thread_ts=ts)`. No API call.
- `send_text(dest, text)` -> `await app.client.chat_postMessage(channel=dest.channel_id,
  thread_ts=dest.thread_ts or None, text=discord_to_mrkdwn(text))` — SAME thread_ts on
  every send (G-25). `discord_to_mrkdwn` escapes `& < >` first, then `**`->`*`,
  `[t](u)`->`<u|t>`, `~~`->`~` (G-03).
- `send_files(dest, caption, files)` -> one `files_upload_v2(channel=dest.channel_id,
  thread_ts=dest.thread_ts or None, initial_comment=discord_to_mrkdwn(caption),
  file_uploads=[...])` with content/file + filename + title per entry (G-13). Same
  thread_ts (G-25). Does NOT read back any returned ts.
- `download_attachment(att, target)` -> authenticated GET of `att.url` with the
  redirect-surviving bearer header + HTML-login guard (G-15). The guard is factored
  into a pure helper for testing:
  ```python
  def validate_download(content_type: str, status: int) -> None:
      if "text/html" in (content_type or "") or status >= 400:
          raise RuntimeError(f"Slack file download failed (status={status})")
  ```
- `add_reaction(message, emoji)` -> `await app.client.reactions_add(
  channel=message.channel_id, timestamp=message.ts, name="hourglass_flowing_sand")`
  (G-02). Best-effort (swallow `SlackApiError`).
- `activity(dest)` -> `noop_activity()`.
- `resolve_display(user_id)` -> cached `users_info`; returns
  `profile.display_name or profile.real_name or user.real_name or user.name or
  user_id` (G-display). Called only from `_run_turn`, never on the ack path.

### 5d. DM (IM) handling

DMs route through `Surface.DM` for both `im` and `mpim` (`thread_key=f"dm:{channel}"`),
gated by `respond_to_dms` (G-im-mpim). The core's `_on_direct` (verbatim from
`:364-433`) then handles `<handle>` rebind / bare follow-up / `!list` / `!close`
(requires an existing binding) / "Start with a handle" hint, identically to Discord.
Slack DMs have no mention logic (`addressee == NONE`).

---

## 6. `SlackConfig` + `TunnelConfig.chat` + `cli serve --chat` + sample `[slack]`

### 6a. `SlackConfig` (`config.py`, after `DiscordConfig` at `:84`)

```python
@dataclass
class SlackConfig:
    """Slack-facing settings (Socket Mode, two tokens).

    Two token env/file pairs (xoxb- bot, xapp- app-level). Allowlists are STRINGS
    (Slack ids are opaque). ``allowed_usergroup_ids`` (subteam ``S…`` ids) is the
    Slack analog of Discord's ``allowed_role_ids``.
    """

    bot_token_env: str = "AGENT_TUNNEL_SLACK_BOT_TOKEN"
    bot_token_file: str = ""
    app_token_env: str = "AGENT_TUNNEL_SLACK_APP_TOKEN"
    app_token_file: str = ""
    channel_ids: list[str] = field(default_factory=list)
    allowed_user_ids: list[str] = field(default_factory=list)
    allowed_usergroup_ids: list[str] = field(default_factory=list)
    respond_to_dms: bool = False
```

### 6b. `TunnelConfig` additions (`config.py`, in the dataclass at `:164-183`)

Add `chat: str = "discord"` (after `tmux_session`), `slack: SlackConfig =
field(default_factory=SlackConfig)` (after `discord`), and the accessor:

```python
def principal_allowlists(self) -> tuple[set[str], set[str]]:
    """(user_ids, role_or_usergroup_ids) for the active front-end, as strings.

    Empty/empty means 'open'. Lets ``ChatCore._allowed`` honor Slack usergroups
    where Discord uses roles, without the core knowing platform names.
    """
    if self.chat == "slack":
        return (
            {str(u) for u in self.slack.allowed_user_ids},
            {str(g) for g in self.slack.allowed_usergroup_ids},
        )
    return (
        {str(u) for u in self.discord.allowed_user_ids},
        {str(r) for r in self.discord.allowed_role_ids},
    )
```

### 6c. `load_config` changes (`config.py:193-268`)

- Add a `chat: Optional[str] = None` parameter (mirroring `backend`).
- Read `chat` from `[tunnel]`: extend the string-key loop at `:227` to
  `("backend", "tmux_session", "platform", "chat")`.
- Add `_apply(cfg.slack, data.get("slack", {}))` next to the other `_apply` calls
  (`:234-237`).
- After the `if backend:` block (`:239-240`), apply the override + auto-platform:
  ```python
  if chat:
      cfg.chat = chat
  # Auto-name the platform from the front-end UNLESS set explicitly in TOML
  # (G-platform-timing): the guard checks the TOML table only, so `serve --chat
  # slack` takes the same auto path, and an explicit [tunnel] platform always wins.
  if cfg.chat == "slack" and "platform" not in tunnel_tbl:
      cfg.platform = "Slack"
  ```
- Validate `chat` alongside the backend check (`:266-267`):
  ```python
  if cfg.chat not in ("discord", "slack"):
      raise ValueError(f"Unknown chat front-end: {cfg.chat!r}")
  ```
- **Channel-id type validation (G-config-types):** after the `_apply` calls, assert
  the right id type per front-end (raise `ValueError` naming the table; `serve` maps it
  to `ClickException`):
  ```python
  if any(not isinstance(c, int) for c in cfg.discord.channel_ids):
      raise ValueError("[discord] channel_ids must be integers (snowflakes).")
  if any(not isinstance(c, str) for c in cfg.slack.channel_ids):
      raise ValueError("[slack] channel_ids must be strings (e.g. \"C0123ABC\").")
  ```

### 6d. `cli.py serve --chat` + `_build` + dispatch + `doctor` (`cli.py`)

`_build` gains `chat` and resolves `--channel` per front-end (`--channel` becomes
`type=str`; Discord casts to int with a clear error):

```python
def _build(config, backend=None, channel=(), token_env=None, chat=None) -> TunnelConfig:
    try:
        cfg = load_config(path=Path(config) if config else None,
                          backend=backend, chat=chat)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if channel:
        if cfg.chat == "slack":
            cfg.slack.channel_ids = list(channel)
        else:
            try:
                cfg.discord.channel_ids = [int(c) for c in channel]
            except ValueError as exc:
                raise click.ClickException(
                    f"Discord --channel ids must be integers: {exc}") from exc
    if token_env:
        cfg.discord.token_env = token_env   # Discord-only (Slack has two tokens)
    return cfg
```

`serve` gains `--chat` (`type=click.Choice(["discord","slack"])`, `default=None`),
changes `--channel` to `type=str`, and dispatches:

```python
cfg = _build(config, backend, channels, token_env, chat)
store = TunnelStore(cfg.state_path)
registry = Registry(cfg.registry_path)
if cfg.chat == "slack":
    try:
        from .slack_bot import run_slack_bot as run
    except ImportError as exc:
        raise click.ClickException(
            f"slack_bolt is required for `serve --chat slack`: {exc}\n"
            "Install it with:  uv tool install 'claude-code-tools[slack]'") from exc
    watched = cfg.slack.channel_ids
else:
    try:
        from .discord_bot import run_bot as run
    except ImportError as exc:
        raise click.ClickException(f"discord.py is required for serve: {exc}") from exc
    watched = cfg.discord.channel_ids
click.echo(f"agent-tunnel: chat={cfg.chat} backend={cfg.backend} "
           f"registry={cfg.registry_path} channels={watched}")
try:
    run(cfg, store, registry)
except RuntimeError as exc:
    raise click.ClickException(str(exc)) from exc
```

`doctor` (`cli.py:627-666`) branches on `cfg.chat` (G-23, G-doctor). Import the
generalized `resolve_token` from `chat_types` for the Slack branch; keep the
`discord_bot` 1-arg wrapper for Discord. Slack checks: bot token resolves; app token
resolves; (best-effort) `auth.test` succeeds and prints `bot_user_id`; report
`cfg.slack.channel_ids`. Shared lines (`claude` on PATH, tmux, converter, published
count) unchanged.

### 6e. Sample `[slack]` block (`sample_config()`, after the `[discord]` block at `:305`)

Also add a `chat` comment to `[tunnel]` (after `:292`):

```
# Chat front-end `serve` runs: "discord" (default) or "slack". Override per
# run with `agent-tunnel serve --chat slack`. Selecting "slack" auto-sets the
# platform label to "Slack" (unless you set [tunnel] platform yourself).
# chat = "discord"
```

The `[slack]` block (no literal braces → no `{{` escaping needed):

```
[slack]
# Slack uses TWO tokens (Socket Mode — no public URL, an outbound websocket
# like Discord). Put neither token here; point at env vars or files.
#   Bot token   (xoxb-…): all Web API calls (post, react, upload, read users).
#   App token   (xapp-…): opens the Socket Mode socket; needs connections:write.
# Only used when [tunnel] chat = "slack" (or `serve --chat slack`).
bot_token_env = "AGENT_TUNNEL_SLACK_BOT_TOKEN"
app_token_env = "AGENT_TUNNEL_SLACK_APP_TOKEN"
# Optional files holding the tokens, used if the env vars are unset.
# bot_token_file = "~/Documents/tokens/slack-bot-token.txt"
# app_token_file = "~/Documents/tokens/slack-app-token.txt"
# Channel ids the bot watches (Slack ids are STRINGS: channel details ->
# e.g. "C0123ABCDEF"). Empty = answers only in DMs / threads it is in.
channel_ids = []
# Empty lists mean: anyone in the watched channels may ask.
# Slack ids are strings (users "U…", usergroups/subteams "S…").
allowed_user_ids = []
# Slack usergroups (subteams) — the analog of Discord roles. Requires the
# usergroups:read scope to resolve membership; otherwise leave empty.
allowed_usergroup_ids = []
respond_to_dms = false
```

---

## 7. pyproject deps + Slack OAuth scope checklist

### 7a. `pyproject.toml` (`:7-28`)

Keep `discord.py` a **hard dependency** (zero packaging churn / no break for existing
`serve` users — the safer call per G-pyproject), and add `slack_bolt` + `aiohttp` as an
optional `[slack]` extra (our own code imports `aiohttp` directly for the
authenticated download, so it is a declared dep, not transitive-only):

```toml
[project.optional-dependencies]
dev = ["commitizen>=3.0.0"]
gdocs = [
    "google-api-python-client>=2.0.0",
    "google-auth-oauthlib>=1.0.0",
    "Pillow>=10.0.0",
]
slack = [
    "slack_bolt>=1.18.0",
    "aiohttp>=3.9.0",
]
```

`serve --chat slack` already fails with an actionable `ImportError -> ClickException`
("Install with `claude-code-tools[slack]`") when the extra is absent.

**Version bump (G-pyproject):** this feature is a `cz bump` (commitizen updates
`pyproject.toml:version` + `claude_code_tools/__init__.py:__version__`). Repo is at
`1.14.1` (both files agree). After the bump, re-run the plugin-version unification so
all `plugins/*/.claude-plugin/plugin.json` match the package version. Update the
`agent_tunnel/__init__.py` "Discord v1" wording to mention Slack.

### 7b. Slack OAuth scope checklist (copy-paste)

App setup (api.slack.com/apps -> Create New App -> From scratch):

```
Socket Mode (sidebar): toggle ON. Generate an App-Level Token with scope:
  connections:write          # the xapp-… token

OAuth & Permissions -> Bot Token Scopes (add AT LEAST these):
  channels:history           # read public channel messages
  groups:history             # read private channel messages
  im:history                 # read DMs (respond_to_dms)
  mpim:history               # read group DMs (respond_to_dms)
  app_mentions:read          # receive @-mentions in channels
  chat:write                 # post answers and notices
  users:read                 # resolve U… ids to display names
  reactions:write            # the ⏳ busy reaction (NOT reactions:read — unused, G-12)
  files:read                 # download a colleague's attachments
  files:write                # post deliverables back
  usergroups:read            # resolve subteam (S…) membership for allowed_usergroup_ids
                             #   (omit if you don't use usergroup allowlists; paid plans)

# "AT LEAST", not "exactly": the *:history scopes above cover reading messages, and
# files_upload_v2 with an explicit channel id covers the common upload path. But the
# slack_sdk v2 upload helper and channel resolution can, in some workspaces, also touch
# conversations.* lookups. If `files_upload_v2` or posting fails with a
# `missing_scope`/`not_in_channel`/`channel_not_found` error, add the matching
# conversation read scope for that surface (and re-install the app):
  channels:read              # public-channel metadata / membership (if needed)
  groups:read                # private-channel metadata (if needed)
  im:read                    # DM metadata (if needed)
  mpim:read                  # group-DM metadata (if needed)

Event Subscriptions -> Enable Events -> Subscribe to bot events:
  message.channels
  message.groups
  message.im
  message.mpim
  # Do NOT subscribe to app_mention (G-double-answer): a watched-channel @-mention
  # already arrives as message.channels; subscribing to BOTH would double-process.
  # Leading-bot-mention detection is done inside the message handler.

Install App -> Install to Workspace -> Allow. Copy the Bot User OAuth Token (xoxb-…).
/invite @YourBot in each watched channel. Channel id: View channel details (C0123ABC).
```

`reactions:read` is deliberately omitted (the bot only adds reactions, never reads
them — G-12). `usergroups:read` is required ONLY if `allowed_usergroup_ids` is used;
otherwise the usergroup allowlist branch simply never matches (degrade gracefully).

---

## 8. Tests

Two new files in `tests/`, real objects, no mocks, `pytest -xvs`, driving coroutines
with `asyncio.run(...)` (no extra plugin), mirroring `tests/test_agent_tunnel.py` and
`tests/test_discord_bot.py`.

### 8a. `tests/test_chat_core.py`

A real in-memory `FakeTransport(ChatTransport)` records every call and opens
deterministic thread dests (no network):

```python
class FakeTransport:
    def __init__(self, limits=None):
        self._limits = limits or TransportLimits()
        self.texts = []       # (thread_key, text)
        self.files = []       # (thread_key, caption, [OutgoingFile])
        self.reactions = []   # (author_id, emoji)
        self.opened = []      # (parent_key, title)
        self.downloads = []   # (AttachmentRef, Path)
        self.fail_download = False
        self._seq = 0
    @property
    def limits(self): return self._limits
    async def open_thread(self, parent, title):
        self._seq += 1
        self.opened.append((parent.thread_key, title))
        return ChatDest(thread_key=f"th:fake{self._seq}", surface=Surface.THREAD,
                        channel_id=parent.channel_id)
    async def send_text(self, dest, text): self.texts.append((dest.thread_key, text))
    async def send_files(self, dest, caption, files):
        self.files.append((dest.thread_key, caption, list(files)))
    async def download_attachment(self, att, target):
        self.downloads.append((att, target))
        if self.fail_download: raise RuntimeError("boom")
        target.write_bytes(b"FAKE")
    async def add_reaction(self, message, emoji):
        self.reactions.append((message.author_id, emoji))
    def activity(self, dest): return noop_activity()
```

A real `FakeBackend(Backend)` returns a canned `Answer` and records the `question`
passed to `ask` (so the relay prefix is asserted); `forget`/`reap_idle` no-op. Inject
by monkeypatching `chat_core.backend_for_record` to return it (real object, only the
lookup redirected — the no-mock-compliant substitution, matching how
`test_agent_tunnel.py` uses real backends only for flag-building).

Helpers `_msg(**over)` and `_core(tmp_path, **cfg_over)` (real `TunnelConfig(state_path,
registry_path)`, real `TunnelStore`, real `Registry`, `FakeTransport`).

Cases:

1. `test_split_chunks_default_and_limit` — `split_chunks("") == []`; `limit=2000`
   round-trips; `limit=50` chunks to <=50 (proves the limit is honored). Keeps the
   shipped no-arg call valid (G-21).
2. `test_allowed_open / by_user_id / by_usergroup_id / denied` — Slack cfg
   (`chat="slack"`, `slack.allowed_user_ids=["U123"]`,
   `slack.allowed_usergroup_ids=["S9"]`) AND a Discord cfg (`discord.allowed_user_ids=
   [123]`, int). Empty/empty -> True; string user match -> True; usergroup via
   `author_role_ids=frozenset({"S9"})` -> True; non-match -> False; the int-vs-str
   coercion: Discord `[123]` matches `author_id="123"` (behavior-preservation point).
3. `test_cooldown_blocks / allows_after / per_principal` — `per_user_cooldown_s=100`;
   first True (records), immediate second False, different principal True; monkeypatch
   `chat_core.time.time` forward -> True.
4. `test_deliver_answer_inline_chunked / as_file` — short answer at
   `max_message_len=50` -> multiple `send_text` each <=50; long answer
   (`> max_inline_chars`) -> one `send_files` with a single `OutgoingFile` named
   `answer.md` whose `data` decodes to the full text + first-chunk caption.
5. `test_thread_key_scheme_is_opaque` — full open + bind under `th:fake1` and a
   Slack-style `th:C123-1700000000.0001`, follow-up resolves the same key (G-05).
6. `test_thread_addressee_other_silent / self_answers / none_answers` — bound thread;
   OTHER -> no send; SELF (pre-stripped) -> answers; NONE -> answers.
7. `test_pipeline_channel_open_binds_and_answers` — seed
   `registry.upsert(PublishRecord(handle="pay", session_id=SID, cwd=str(tmp_path),
   access="read"))`; `handle_message(_msg(surface=CHANNEL, text="pay how does refresh
   work?", channel_id="C1"))`. Assert `opened == [("", "pay: how does refresh work?")]`,
   `store.get("th:fake1")` bound, answer delivered via `send_text` on `th:fake1`, and
   `FakeBackend`'s captured question starts with `"... (via Discord) says:"` /
   `cfg.platform`.
8. `test_pipeline_channel_handle_only_posts_ready_notice` — `text="pay"` -> "Connected
   to **pay**…" and NO `ask`.
9. `test_pipeline_unknown_handle_complains_before_allowlist` — no registry entry,
   `text="nope"` -> exactly one "No live session for handle `nope`…", and assert it
   fires even when the allowlist would DENY the author (ordering invariant). Then
   `text="nope please help"` -> silent.
10. `test_pipeline_thread_followup_answers_and_cooldown` — pre-bind; follow-up answers;
    immediate second (within cooldown) -> no answer + one
    `add_reaction(author_id, "hourglass")`.
11. `test_pipeline_attachments_ingested` — `attachments=(AttachmentRef("a.txt", 4),)`;
    assert `downloads` recorded, the question handed to `ask` contains the
    `attachment_preamble` path; oversize ref (`size > max_attachment_mb*1MB`) -> a
    "⚠️ Skipped:" `send_text`.
12. `test_pipeline_attachment_download_failure_skipped` (G-error) —
    `tx.fail_download=True`; the file appears in "⚠️ Skipped" and is absent from the
    saved set.
13. `test_pipeline_attachment_only_empty_content` (G-error) — an attachment whose
    ingested content is empty -> "⚠️ Nothing to act on" `send_text`, no `ask`.
14. `test_pipeline_deliverables_posted` — `Answer(attachments=[tmpfile])` -> one
    `send_files` caption "📎 Deliverable(s)…" + `OutgoingFile.from_path`; an 11-file
    answer with `max_attachments_per_msg=10` -> two batches ("Deliverable(s)", then
    "More deliverables").
15. `test_pipeline_deliverables_send_failure_swallowed` (G-error) — a `FakeTransport`
    variant whose `send_files` raises -> swallowed + logged, no crash, skipped notice
    still posts if applicable.
16. `test_pipeline_backend_error_truncated` — `ask` raises `BackendError("x"*5000)` ->
    one `send_text` of length `max_error_len + 2` ("⚠️ " + slice).
17. `test_pipeline_backend_unexpected_error` (G-error) — `ask` raises `ValueError` ->
    generic "⚠️ Unexpected error" and the lock/sem are released (a subsequent turn on
    the same key proceeds).
18. `test_pipeline_stale_binding_guard` — bind `th:fake1` session A; before the lock,
    `store.remove` + re-`bind` with a different session/`created_at`; assert "↪️ …
    restarted…" and NO `ask`.
19. `test_pipeline_dm_rebind_then_followup` — `_msg(surface=DM, text="pay first q")`
    binds + answers; bare `_msg(surface=DM, text="more")` follows up; `_msg(surface=DM,
    text="other-handle")` rebinds (asserts old-fork `forget` then new bind); the
    "Connected" rebind notice firing BEFORE the allowlist gate is asserted by setting a
    denying allowlist and checking the notice still posts then returns.
20. `test_access_collision_slack_dm_key` (G-06-collision) — bind a Slack-style
    `dm:{C}` key under handle `cli` (session A), then `registry.upsert` a `cli` for a
    DIFFERENT session; drive a turn and assert access is NOT escalated (the Slack
    mirror of `test_access_sync_ignores_handle_collision_from_other_session`,
    `tests/test_agent_tunnel_backends.py:262`) — the guard keys on
    `session_id == expert_session_id`.
21. `test_concurrency_per_thread_serialization` (G-27) — drive 3 concurrent thread
    keys through `FakeTransport` with a `FakeBackend.ask` that sleeps; assert per-key
    serialization (no interleaving on one key) and that the global
    `Semaphore(max_concurrent)` caps simultaneous `ask` calls.

### 8b. `tests/test_slack_bot.py`

Tests `slack_event_to_incoming` (and the pure `validate_download` /
`discord_to_mrkdwn` helpers) from fixture payloads copied verbatim from the research.
Imports ONLY those free functions (no `slack_bolt`; the module's import is lazy).
Module-level fixture dicts.

Cases:

1. `test_channel_message` — `{"type":"message","channel":"C123","user":"U1",
   "text":"pay hello","ts":"1355517523.000005","channel_type":"channel"}`,
   `channel_ids={"C123"}` -> `surface==CHANNEL`, `text=="pay hello"`,
   `author_id=="U1"`, `channel_id=="C123"`, `ts==…`, `dest.thread_key==""`,
   `dest.thread_ts=="1355517523.000005"` (triggering ts carried for open_thread,
   G-18), `addressee==NONE`.
2. `test_threaded_reply` — `thread_ts="1700000000.0001"`, `ts="…0009"` ->
   `surface==THREAD`, `dest.thread_key=="th:C123-1700000000.0001"`,
   `dest.thread_ts=="1700000000.0001"`. A second fixture with `thread_ts == ts` ->
   `CHANNEL` (the `thread_ts != ts` reply test).
3. `test_im_message` — `channel="D024BE91L"`, `channel_type="im"` -> `surface==DM`,
   `thread_key=="dm:D024BE91L"`. And `test_mpim_message` — `channel_type="mpim"` ->
   `surface==DM`, `thread_key=="dm:<channel>"` (G-im-mpim).
4. `test_leading_self_mention_stripped` — `text="<@U0LAN0Z89> what's up?"`,
   `bot_user_id="U0LAN0Z89"`, in a THREAD -> `addressee==SELF`, `text=="what's up?"`.
   `<@U999>` -> `addressee==OTHER`, text unstripped. `<!here> …` in a THREAD ->
   `addressee==OTHER`. `<!here> pay …` on CHANNEL -> `addressee==NONE`, token `<!here>`
   (non-handle) (G-broadcast).
5. `test_message_with_files` — `files=[{"id":"F1","name":"ghostrap.png","size":12345,
   "url_private_download":"https://files.slack.com/…/download/…"}]`, `text=""` ->
   `attachments == (AttachmentRef("ghostrap.png", 12345, url="https://…/download/…",
   _native=<file dict>),)`, NOT dropped. A `name:None` fixture ->
   `filename=="file-F1"`. A fixture with only `url_private` (no `_download`) -> `url`
   falls back to `url_private` (G-16).
6. `test_bot_subtype_ignored` — `subtype:"bot_message"` -> None; `bot_id==MY_BOT_ID` ->
   None; `user==MY_USER_ID` (im) -> None; `message_changed`/`message_deleted`/
   `channel_join` -> None (G-subtype, G-self).
7. `test_channel_not_watched_dropped` — `channel_type="channel"` in `C999` not in
   `channel_ids` -> None. A DM (`channel_type="im"`) with `respond_to_dms=True` is NOT
   dropped; with `respond_to_dms=False` -> None.
8. `test_resolve_display_used` — `resolve_display=lambda uid: {"U1":"Alice"}.get(uid,
   uid)` -> `author_display=="Alice"` for `U1`, `"U2"` (id) for unknown.
9. `test_validate_download_html_guard` — `validate_download("text/html; charset=utf-8",
   200)` raises; `validate_download("image/png", 200)` does not; `status>=400` raises
   (G-15).
10. `test_discord_to_mrkdwn` — `"**b**"`->`"*b*"`; `"[t](u)"`->`"<u|t>"`; `"~~s~~"`->
    `"~s~"`; `"a < b && c > d"` -> `"a &lt; b &amp;&amp; c &gt; d"` (escape FIRST);
    inline-code and code-fences pass through (G-03).
11. `test_dedup_cache` — `_SeenCache(cap=4)`: `add(k)` True then False on repeat;
    capacity-bounded (oldest evicted past `cap`) (G-24).

### 8c. Test-suite housekeeping

- `tests/test_discord_bot.py` keeps importing `format_relayed_message` from
  `discord_bot` (re-exported from `chat_types`) — untouched.
- `tests/test_agent_tunnel.py` `test_split_chunks_roundtrip`, `test_resolve_token`,
  `test_is_close_command`, `test_is_list_command` keep passing: `discord_bot`
  re-exports `split_chunks`/`is_*` and keeps the 1-arg `resolve_token(cfg)` wrapper
  (G-21, G-23).

---

## 9. Docs updates

### 9a. Starlight `docs-site/src/content/docs/tools/agent-tunnel.mdx`

Verify anchors at edit time (headers stable, line numbers not — G-docs).

- Frontmatter `description` and the intro: "…over Discord **or Slack**…", with a pointer
  to the new section.
- Add `## Using Slack instead of Discord` AFTER "Part C (run it)" and before "Watching
  live", containing: the Socket-Mode framing ("outbound websocket — no tunnel, no
  port, no cloud"); a `<Steps>` app-creation walkthrough (create app -> Socket Mode +
  `connections:write` xapp- token -> Bot Token Scopes (the Section 7b list) -> Event
  Subscriptions (the four `message.*`, explicitly NOT `app_mention`) -> Install ->
  `/invite` + channel id); a scope-checklist table (scope ↔ why); an `<Aside>` "Two
  tokens, two roles"; the `[slack]` TOML block from Section 6e; `agent-tunnel serve
  --chat slack` (and "or just `serve` when `[tunnel] chat = "slack"`"); an `<Aside
  type="tip">` on `uv tool install 'claude-code-tools[slack]'` and `doctor` checking
  both tokens; and a closing paragraph that the colleague vocabulary
  (`!list`/`<handle> question`/in-thread follow-ups/`!done`/`@someone-else`
  left-to-humans/attachments) is identical to Discord.
- Security `<Aside>` (currently lists `allowed_user_ids`/`allowed_role_ids`): update to
  enumerate BOTH Discord (`allowed_user_ids`/`allowed_role_ids`) and Slack
  (`allowed_user_ids`/`allowed_usergroup_ids`), and "Discord OR Slack channel
  membership" (G-docs).

### 9b. `docs/agent-tunnel-spec.md`

- Components: add a `chat_types.py` + `chat_core.py` entry BEFORE `discord_bot.py`
  (platform-neutral routing/policy/pipeline/reaper behind `ChatTransport`; the neutral
  dataclasses); rewrite the `discord_bot.py` entry to "thin Discord adapter over
  `chat_core`" (lazy `discord`, classification, `<@id>`->`Addressee` + self-strip,
  `DiscordTransport`); add a `slack_bot.py` entry (Socket Mode via `slack_bolt`,
  two-token, drop bot/self/edit/delete + `event_id`/`client_msg_id` dedup,
  `channel_type`+`thread_ts` -> `Surface`, `<@U…>`/`<!…>` -> `Addressee`,
  `files[]`->`AttachmentRef`, ack<3s -> `create_task`, `SlackTransport` with
  `chat_postMessage`/`files_upload_v2`/`reactions_add`/authenticated download +
  HTML-guard/no-op activity/mrkdwn fixups/`TransportLimits(max_message_len=4000,…)`).
- Config entry: add `[slack]` (`SlackConfig`), `[tunnel] chat` selector + auto-platform.
- CLI entry: `serve --chat {discord,slack}` dispatches `run_bot` vs `run_slack_bot`;
  `--channel` int (Discord) / string (Slack).
- Routing section: add the preamble "routing/policy is platform-neutral
  (`chat_core.ChatCore`); adapters only normalize + perform chat I/O", and a Slack-
  mapping bullet (watched channel = `message.channels`/`groups` no `thread_ts`;
  thread = `thread_ts != ts`; DM = `im`/`mpim`; leading `<@bot>` stripped; bot/self/
  edit/delete dropped; Socket-Mode dedup on `event_id`/`client_msg_id`; unnamed
  threads; `⏳` = `reactions_add(hourglass_flowing_sand)`).
- Validation-status: add the `chat_core` + Slack-adapter test bullet (Section 8).
- Note `cli.ask` remains a direct backend smoke test (no `ChatCore`), so the moved
  ingest/deliver code is covered solely by `test_chat_core` (G-26).

---

## 10. Phased implementation checklist (with file lists)

**PR1 — extract `chat_core` + refactor Discord (Discord stays green, no behavior
change):**

- NEW: `claude_code_tools/agent_tunnel/chat_types.py` (Protocol, value objects,
  `TransportLimits`, `OutgoingFile`, `noop_activity`, moved helpers, generalized
  `split_chunks`/`resolve_token`).
- NEW: `claude_code_tools/agent_tunnel/chat_core.py` (`ChatCore` + reaper).
- MODIFIED: `claude_code_tools/agent_tunnel/discord_bot.py` (adapter shape; re-export
  the neutral helpers + `DISCORD_MSG_LIMIT`/`THREAD_NAME_MAX`; keep `resolve_token(cfg)`
  wrapper; `DiscordTransport`).
- NEW: `tests/test_chat_core.py`.
- Gate: full existing suite green (`pytest -xvs tests/test_agent_tunnel.py
  tests/test_discord_bot.py tests/test_agent_tunnel_backends.py`) + `test_chat_core.py`
  green. Manual: `agent-tunnel doctor` and a Discord smoke turn behave identically.

**PR2 — `slack_bot` + `SlackConfig` + `--chat` + tests:**

- NEW: `claude_code_tools/agent_tunnel/slack_bot.py` (`run_slack_bot`,
  `slack_event_to_incoming`, `validate_download`, `discord_to_mrkdwn`, `_SeenCache`,
  `SlackTransport`).
- MODIFIED: `claude_code_tools/agent_tunnel/config.py` (`SlackConfig`,
  `TunnelConfig.chat`/`slack`/`principal_allowlists`, `load_config` `chat` param +
  auto-platform + chat/channel-type validation + `_apply(cfg.slack,…)`,
  `sample_config()` `[slack]` + `chat` comment).
- MODIFIED: `claude_code_tools/agent_tunnel/cli.py` (`serve --chat`, `_build`
  per-front-end channel coercion, dispatch to `run_slack_bot`, `doctor` Slack branch).
- MODIFIED: `pyproject.toml` (`[slack]` extra: `slack_bolt`, `aiohttp`) + `cz bump` +
  plugin-version unification.
- MODIFIED: `claude_code_tools/agent_tunnel/__init__.py` ("Discord v1" wording).
- NEW: `tests/test_slack_bot.py`.
- Gate: `pytest -xvs tests/test_slack_bot.py tests/test_chat_core.py` green; install
  `[slack]` and run a Slack Socket-Mode smoke turn (open thread, follow-up, attachment
  in, deliverable out, `!done`).

**PR3 — docs:**

- MODIFIED: `docs-site/src/content/docs/tools/agent-tunnel.mdx` (Slack section +
  intro/description/security tweaks).
- MODIFIED: `docs/agent-tunnel-spec.md` (components + routing + config + CLI +
  validation-status).

---

## 11. Resolution of every gap (G-NN), stated as handled

**Blockers**

- **G-double-answer (app_mention/message double delivery).** RESOLVED: subscribe ONLY
  to the four `message.*` events; do NOT register an `app_mention` listener. Leading-
  bot-mention is detected inside the message handler (`Addressee.SELF` + strip).
  Dedup additionally keys on `client_msg_id` (or `(channel, ts)`), which is stable
  across deliveries — NOT `event_id` alone (Section 5a/5b, 7b).
- **G-subtype (subtype filtering vs file-only messages).** RESOLVED: do NOT gate on
  `subtype is None`. Register a plain `@app.event("message")`; the pure normalizer
  drops an explicit IGNORE set (`bot_message`, `message_changed`, `message_deleted`,
  `channel_*`, `me_message`, `thread_broadcast`) and ACCEPTS everything else including
  no-subtype messages and file-bearing messages (detected by `event.get("files")`).
  Edits/deletes are intentionally ignored (documented) (Section 5b, 8b#6).
- **G-13 (`files_upload_v2` mapping).** RESOLVED: explicit per-entry mapping —
  `{"content": of.data, "filename": of.filename, "title": of.filename}` for bytes,
  `{"file": str(of.path), …}` for paths; singular `channel`; `title` carries the
  extension; one call with `file_uploads=` + `initial_comment` + `thread_ts`; the
  returned ts is not read back (Section 5c).
- **G-21 (`split_chunks` signature / false 'bug').** RESOLVED: `limit` stays a
  DEFAULTED keyword (`= 2000`); the "latent bug" rationale is dropped. Existing tests
  and the `discord_bot` re-export keep working; core callers always pass the transport
  limit (Section 2c, 3, 8a#1).
- **G-06-collision (`dm:{channel}` key vs `_current_access` guard).** RESOLVED: the
  stale guard in `_answer` (compare `expert_session_id` + `created_at`) and the
  live-access path `backends._current_access` (keys on `session_id ==
  expert_session_id`, `:186-193`) are preserved by the neutral pipeline; a Slack DM
  bound under handle `cli` is NOT escalated by an unrelated `>share cli`. Covered by
  `test_access_collision_slack_dm_key` (Section 8a#20).
- **G-shutdown (Slack graceful shutdown).** RESOLVED: `run_slack_bot` runs
  `asyncio.run(_main())`; `_main` awaits `handler.start_async()` in a `try/finally`
  that cancels the reaper, `close_async()`s the socket, AND `await transport.aclose()`s
  the lazily created `aiohttp` download session (so it is not leaked / "Unclosed client
  session"-warned) on Ctrl-C/exception; in-flight turns finish on their per-thread locks
  (bounded by G-27); `cli.main()`'s `KeyboardInterrupt -> exit(130)` still applies
  (Section 5a).
- **G-22 (line budget).** RESOLVED: split into `chat_types.py` (~430) +
  `chat_core.py` (~560), both well under 1000. `discord_bot.py` drops ~330 moved lines
  to the adapter shape (~717 -> ~250, including `DiscordTransport`) (Section 2, 3, 4).

**Major**

- **G-DM-group (Surface.DM scope).** RESOLVED: `Surface.DM` covers BOTH `im` and
  `mpim` on Slack; on Discord it stays `DMChannel`-only (group DMs unhandled, matching
  the shipped bot — the docstring says exactly that) (Section 2b, 5d).
- **G-02 (cooldown emoji mapping).** RESOLVED: the core passes the neutral name
  `"hourglass"`; the Discord adapter maps `{"hourglass": "⏳"}` (load-bearing code,
  not prose); the Slack adapter uses `hourglass_flowing_sand` (the ⏳ flowing-sand
  glyph, verified — not ⌛) (Section 2a, 4, 5c).
- **G-03 (markdown / mrkdwn).** RESOLVED: the adapter's `send_text`/`send_files`
  translate via the pure, tested `discord_to_mrkdwn` — escape `& < >` FIRST, then
  `**`->`*`, `[t](u)`->`<u|t>`, `~~`->`~`, applied BEFORE any send (so escaping can't
  push a chunk over the limit post-chunking, because translation happens at send time
  on already-chunked text and Slack's 4000 cap has headroom; the answer text is
  escaped too, which is correct for Slack). The set of core-emitted markdown strings is
  enumerated in the spec; tested in `test_discord_to_mrkdwn` (Section 5c, 8b#10).
- **G-12 (scopes: `reactions:read` unused; `usergroups:read` missing).** RESOLVED:
  `reactions:read` removed; `usergroups:read` added (used to populate
  `author_role_ids` for `allowed_usergroup_ids`), guarded to degrade gracefully when
  absent/erroring (paid-plan feature) (Section 7b, 2b).
- **G-14 (3s ack vs `users_info`).** RESOLVED: the event handler does only synchronous
  drop/classify/dedup/build (author_display = raw id) then `create_task` and returns;
  `users_info` resolution happens in `_run_turn` (background), cached. No awaited Web
  API call on the pre-ack path — INCLUDING the fan-out-overflow path, where the busy
  notice (`chat_postMessage`) is spawned fire-and-forget rather than awaited, so the
  ack is never blocked even under overload (Section 5a).
- **G-15 (`url_private` redirect header-stripping).** RESOLVED: download uses
  `url_private_download`, keeps the bearer header across the file-host redirect, and
  raises on a `text/html` (login-page) response BEFORE writing — codified in the
  `download_attachment` contract and the pure `validate_download` helper (Section 2a,
  5c, 8b#9).
- **G-16 (Slack file object normalization).** RESOLVED: explicit `filename`/`size`/
  `url` fallbacks in `slack_event_to_incoming`; `_native` holds the raw dict; tested
  incl. null name and `url_private` fallback (Section 2b, 5b, 8b#5).
- **G-24 (dedup mandatory + bounded + correct key).** RESOLVED: dedup runs
  synchronously BEFORE spawning, in a bounded `_SeenCache(cap=…)`, keyed on
  `client_msg_id` else `(channel, ts)` (stable across the app_mention/message pair and
  retries) (Section 5a, 8b#11).
- **G-20 (auth.test before socket).** RESOLVED: `auth.test` awaited and ids stored
  before `start_async()`, so the self/bot drop is never bypassed on the first event
  (Section 5a).
- **G-doctor / G-23 (`resolve_token` signature + doctor two-token).** RESOLVED:
  generalized `resolve_token(env_name, file_path)` lives in `chat_types`; a 1-arg
  `resolve_token(cfg)` wrapper stays in `discord_bot` for the existing test and the
  Discord doctor branch; the Slack doctor branch imports the 2-arg helper from
  `chat_types` for both token pairs (Section 3, 6d).
- **G-pyproject (deps + version + break).** RESOLVED: `discord.py` stays a HARD dep
  (zero break); only `[slack]` (`slack_bolt`, `aiohttp`) added as an extra; explicit
  `cz bump` + plugin-version unification + `__init__.py` wording update (Section 7a).
- **G-config-types (channel-id type validation).** RESOLVED: `load_config` asserts
  `discord.channel_ids` are int and `slack.channel_ids` are str, raising `ValueError`
  naming the table; the channel-watched gate is independent of the allowlist gate, both
  typed correctly (Section 6c).
- **G-27 (semaphore + unbounded fan-out under Slack create_task).** RESOLVED: the
  listener tracks `inflight` tasks and applies a fan-out threshold (posts a brief busy
  notice and skips spawning beyond it); per-conversation serialization is the per-
  `thread_key` lock; the global `Semaphore(max_concurrent)` caps simultaneous backend
  asks. Tested by `test_concurrency_per_thread_serialization` (Section 5a, 8a#21).
- **G-validation (Slack startup validation).** RESOLVED: `run_slack_bot` resolves both
  tokens (per-token `RuntimeError`), `auth.test`s once in `_main` BEFORE the socket
  (caches ids); because `auth.test` raises `SlackApiError` (a plain `Exception`, not a
  `RuntimeError`), it is WRAPPED into a `RuntimeError` so `serve`'s
  `except RuntimeError` surfaces it as a clean `ClickException` instead of a raw
  traceback; reuses the no-channels-and-no-DMs guard; `doctor` shows both tokens as
  separate lines + the bot user id (Section 5a, 6d).
- **G-error (missing error-path tests).** RESOLVED: added cases for download-raises ->
  skipped, non-`BackendError` -> generic + lock released, `send_files` raise swallowed,
  empty-after-ingest, and the HTML-guard helper (Section 8a#12,13,15,17, 8b#9).
- **G-mrkdwn-escaping (load-bearing).** RESOLVED: folded into G-03 — `& < >` escaping
  is mandatory in `discord_to_mrkdwn`, applied before markup translation and to answer
  text, with a dedicated test asserting `if a < b && c > d` round-trips escaped.

**Minor**

- **G-04 (unknown-handle reply-quote).** RESOLVED (accepted, documented): routed
  through `send_text(msg.dest, …)`; identical text, reply-quote affordance dropped; no
  `reply` method added (minimal surface) (Section 4).
- **G-05 (single-materialization of attachments).** RESOLVED: `attlist =
  list(attachments)` once; safe because the adapter passes the complete tuple;
  `AttachmentRef.size` defaults to 0 matching `getattr(att,"size",0) or 0` (Section
  2c).
- **G-08 (`list_truncate_len` vs `max_message_len`).** RESOLVED: documented invariant
  `list_truncate_len <= max_message_len` AND `_list_handles` truncates to the `min(...)`,
  so the no-re-chunk contract can't be violated (Section 2a, 2c).
- **G-09 (ingest notices under lock; delivery after).** RESOLVED: the `_answer` port
  keeps ingest-time `send_text` notices inside the `activity()` + `async with lock,
  sem` block and answer delivery + deliverables strictly after (Section 2c).
- **G-10 / G-self (self-drop non-uniform).** RESOLVED: documented as adapter-specific —
  Discord uses `author.bot` (covers self); Slack additionally compares `bot_id` /
  `subtype:"bot_message"` / own `user` from `auth.test` (Section 4, 5b, 2b docstrings).
- **G-18 (thread_ts establishment ordering).** RESOLVED: the channel-surface parent
  dest carries the triggering `ts` in `thread_ts`; `SlackTransport.open_thread` reads
  it to build `th:{channel}-{ts}` and thread off it; tested that the channel parent
  exposes the triggering ts (Section 2b, 5c, 8b#1).
- **G-19 (Slack length caps).** RESOLVED: `max_message_len=4000` with a comment that
  40000 is the hard ceiling (do not raise toward it); the `answer.md` preview is
  `split_chunks(text, limit)[0]` (<= 4000) (Section 2a).
- **G-25 (consistent thread_ts on every send).** RESOLVED: the conversation's
  `thread_ts` (the parent ts, fixed at `open_thread`) is forwarded on EVERY
  `chat_postMessage` and `files_upload_v2`, so the thread is consistent regardless of
  which send fires first (Section 2a, 5c).
- **G-broadcast (Slack broadcast/CHANNEL mention rule).** RESOLVED: CHANNEL ignores
  `addressee` (handle-token parse governs, matching Discord); THREAD broadcasts ->
  `OTHER`; tested (Section 5b, 8b#4).
- **G-im-mpim (DM detection for mpim + respond_to_dms).** RESOLVED: both `im` and
  `mpim` -> `Surface.DM` (`dm:{channel}`), both gated by `respond_to_dms`; mpim test
  added (Section 5b, 5d, 8b#3).
- **G-28 (reaper scheduling on Slack).** RESOLVED: the reaper task is created inside
  `_main` on the same running loop as the per-turn tasks, right before
  `start_async()` (Section 5a).
- **G-26 (`cli.ask` bypasses the core).** RESOLVED: documented that `cli.ask` remains a
  direct backend smoke test (no `ChatCore`), so the moved ingest/deliver code is
  covered solely by `test_chat_core` (Section 3, 8c, 9b).
- **G-docs (mdx anchors + security Aside fields).** RESOLVED: edit by header (not line
  number); update the security Aside to enumerate both Discord and Slack allowlist
  fields and "Discord OR Slack channel membership" (Section 9a).
- **G-display (`users_info` fallback chain).** RESOLVED: `resolve_display` returns
  `display_name or real_name or user.real_name or user.name or user_id`, cached, called
  only off the ack path (Section 5c, 2b).

---

## Files touched (summary)

- **New:** `claude_code_tools/agent_tunnel/chat_types.py`,
  `claude_code_tools/agent_tunnel/chat_core.py`,
  `claude_code_tools/agent_tunnel/slack_bot.py`, `tests/test_chat_core.py`,
  `tests/test_slack_bot.py`.
- **Modified:** `claude_code_tools/agent_tunnel/discord_bot.py`,
  `claude_code_tools/agent_tunnel/config.py`, `claude_code_tools/agent_tunnel/cli.py`,
  `claude_code_tools/agent_tunnel/__init__.py`, `pyproject.toml`,
  `claude_code_tools/__init__.py` (version bump), all
  `plugins/*/.claude-plugin/plugin.json` (version unification),
  `docs-site/src/content/docs/tools/agent-tunnel.mdx`, `docs/agent-tunnel-spec.md`.
