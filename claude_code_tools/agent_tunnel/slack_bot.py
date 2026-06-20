"""Slack front-end for agent-tunnel (Socket Mode, handle-opens-a-thread model).

This module is a thin **adapter** over the platform-neutral
:class:`~.chat_core.ChatCore`: it owns only the Slack wire-format. It drops
bot/self/edit/delete events, classifies each surviving message into a
:class:`~.chat_types.Surface`, normalizes it into an
:class:`~.chat_types.IncomingMessage` (resolving a leading ``<@bot>`` mention
into an :class:`~.chat_types.Addressee` and stripping it on the THREAD surface),
implements :class:`~.chat_types.ChatTransport` over the Slack Web API, builds one
``ChatCore``, schedules the reaper, and forwards each accepted message to the
core. All routing/policy/pipeline logic lives in ``chat_core``.

Slack runs over Socket Mode -- an outbound WebSocket like Discord's Gateway, so
there is no public Request URL and no open port. Two tokens are used: a bot
token (``xoxb-…``, every Web API call) and an app-level token (``xapp-…``, scope
``connections:write``, opens the socket).

``slack_bolt`` / ``slack_sdk`` / ``aiohttp`` are imported LAZILY (only inside the
functions that need them), exactly like ``discord_bot`` defers ``discord``, so
``import slack_bot`` for the pure helpers (:func:`slack_event_to_incoming`,
:func:`validate_download`, :func:`discord_to_mrkdwn`) never requires the deps.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .chat_core import ChatCore
from .chat_types import (
    AttachmentRef,
    ChatDest,
    ChatTransport,
    IncomingMessage,
    OutgoingFile,
    Surface,
    TransportLimits,
    Addressee,
    noop_activity,
    resolve_token,
)
from .config import TunnelConfig
from .registry import Registry
from .store import TunnelStore

logger = logging.getLogger("agent_tunnel")

# Slack message subtypes the bot ignores outright. Edits/deletes are
# INTENTIONALLY ignored (we answer a message once, when first posted); the
# channel/join/topic housekeeping subtypes are noise. A file-only upload arrives
# as a regular message (no subtype, or one not in this set), so it is NOT here.
_IGNORE_SUBTYPES = frozenset(
    {
        "message_changed",
        "message_deleted",
        "bot_message",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "me_message",
        "thread_broadcast",
        "channel_archive",
        "channel_unarchive",
    }
)

# Leading-mention wire-format (adapter-private). A leading ``<@OTHER>`` user, a
# ``<!here|channel|everyone>`` broadcast, or a ``<!subteam^S…>`` usergroup means
# teammates are addressing someone other than the bot -> stay out (THREAD only).
_LEAD_OTHER_USER_RE = re.compile(r"^\s*<@[UW][A-Z0-9]+>")
_LEAD_BROADCAST_RE = re.compile(r"^\s*<!(here|channel|everyone)>")
_LEAD_SUBTEAM_RE = re.compile(r"^\s*<!subteam\^[A-Z0-9]+>")

# Beyond this many concurrently in-flight turns the listener posts a brief busy
# notice and skips spawning, instead of growing unbounded pending coroutines.
# Per-conversation serialization is the core's per-thread_key lock; the global
# Semaphore(max_concurrent) still caps simultaneous backend asks (G-27).
_MAX_INFLIGHT = 64


def discord_to_mrkdwn(text: str) -> str:
    """Translate Discord-flavored markdown to Slack ``mrkdwn``.

    The core emits Discord-style markup (``**bold**``, ``[text](url)`` links,
    ``~~strike~~``). Slack speaks ``mrkdwn`` instead, and treats ``& < >`` as
    HTML-ish control characters. Order matters: escape ``& < >`` FIRST (so a
    later-inserted ``<url|text>`` link is not mangled), then translate the
    markup (G-03 / G-mrkdwn-escaping).

    Args:
        text: A core-emitted chunk (already split to the message-length cap).

    Returns:
        The ``mrkdwn`` equivalent, safe to pass straight to ``chat_postMessage``.
    """
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # **bold** -> *bold* (non-greedy, across newlines).
    out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out, flags=re.DOTALL)
    # [text](url) -> <url|text>. The url was just escaped (& -> &amp; etc.),
    # which Slack unescapes on render, so links survive intact.
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", out)
    # ~~strike~~ -> ~strike~.
    out = re.sub(r"~~(.+?)~~", r"~\1~", out, flags=re.DOTALL)
    return out


def validate_download(content_type: Optional[str], status: int) -> None:
    """Raise if a Slack file download looks like a failure, before writing bytes.

    Slack ``url_private`` GETs REDIRECT to a CDN host; a client that drops the
    auth header on that cross-origin hop lands on a ``200 text/html`` LOGIN PAGE
    instead of the file. So a non-2xx status OR an HTML content-type means the
    download failed (auth/scope), even with a 200 (G-15).

    Args:
        content_type: The response ``Content-Type`` header (may be empty/None;
            a missing header is treated as a normal binary download, not HTML).
        status: The HTTP status code (``>= 400`` is a failure).

    Raises:
        RuntimeError: The response is an HTML login page or an error status.
    """
    if "text/html" in (content_type or "") or status >= 400:
        raise RuntimeError(f"Slack file download failed (status={status})")


class _SeenCache:
    """A bounded, insertion-ordered set for Socket-Mode delivery dedup.

    Slack may deliver the same logical message more than once (retries, the
    app_mention/message pair). The listener records a stable per-message key
    (``client_msg_id`` else ``(channel, ts)``) here BEFORE spawning the turn, so
    a duplicate is dropped synchronously. Bounded so a long-lived daemon's dedup
    set can't grow without limit -- the oldest keys evict past ``cap`` (G-24).
    """

    def __init__(self, cap: int) -> None:
        """Create a cache holding at most ``cap`` recent keys."""
        self._cap = cap
        self._keys: "OrderedDict[str, None]" = OrderedDict()

    def add(self, key: str) -> bool:
        """Record ``key``; return ``False`` if it was already present.

        Eviction is by INSERTION order, NOT recency: a duplicate ``add`` returns
        ``False`` WITHOUT refreshing the key's position (no ``move_to_end``), so a
        key still evicts on its original insertion slot. Pure dedup never needs
        LRU; making it one would silently change which retries get dropped.

        Args:
            key: A stable per-message dedup key.

        Returns:
            ``True`` if this is the first time ``key`` is seen (caller should
            process the message), ``False`` if it is a duplicate (caller skips).
        """
        if key in self._keys:
            return False
        self._keys[key] = None
        while len(self._keys) > self._cap:
            self._keys.popitem(last=False)
        return True


def slack_event_to_incoming(
    event: dict,
    *,
    bot_user_id: str,
    bot_id: str,
    channel_ids: set[str],
    respond_to_dms: bool = False,
    resolve_display: Callable[[str], str] = lambda uid: uid,
) -> Optional[IncomingMessage]:
    """Normalize a Slack ``message`` event to an :class:`IncomingMessage`, or drop.

    Pure and ``slack_bolt``-free so the tests can exercise it from fixture dicts
    with no socket and no dependency. The live bot calls it synchronously on the
    ack path with a no-op ``resolve_display`` (the display name is resolved in
    the background turn, G-14); the tests pass a real lookup.

    Drop rules, in order:

    - ``subtype`` in the ignore set (``message_changed``/``message_deleted``/
      ``bot_message``/``channel_*``/``me_message``/``thread_broadcast``):
      edits/deletes and channel housekeeping are intentionally ignored. A
      file-only upload has no such subtype and is NOT dropped (detected via
      ``event.get("files")`` regardless of subtype, G-subtype).
    - self/bot loop: ``bot_id`` matches, OR ``subtype == "bot_message"``, OR
      ``user == bot_user_id`` (G-self).

    Classify (G-im-mpim):

    - ``channel_type`` in ``("im", "mpim")`` -> :attr:`Surface.DM`
      (``dm:{channel}``), gated by ``respond_to_dms`` (``None`` if disabled).
    - else (channel/group): ``thread_ts`` present and ``!= ts`` ->
      :attr:`Surface.THREAD` (``th:{channel}-{thread_ts}``, ``thread_ts``
      carried on the dest). A parent (``thread_ts == ts`` or absent) in a
      watched channel -> :attr:`Surface.CHANNEL` with ``dest.thread_ts = ts`` so
      :meth:`SlackTransport.open_thread` can thread off it. A CHANNEL-surface
      post NOT in ``channel_ids`` (or with no ``ts`` to anchor on) -> ``None``.

    Watched-channel gate scope (deliberate, security-relevant): ONLY the
    top-level CHANNEL surface is gated by ``channel_ids``. A THREAD reply
    (``thread_ts != ts``) is intentionally NOT re-gated — a follow-up in an
    already-running thread is answered wherever it lives, even if its channel is
    not (or no longer) in ``channel_ids``. DMs are likewise not gated by
    ``channel_ids`` (only by ``respond_to_dms``). A future "tighten the gate"
    refactor must keep this thread-bypass intact (pinned by a test).

    Leading mention -> :class:`Addressee` (THREAD surface only):

    - ``<@bot>`` leading -> :attr:`Addressee.SELF`, stripped from ``text``.
    - ``<@other>`` / ``<!here|channel|everyone>`` / ``<!subteam^…>`` leading
      -> :attr:`Addressee.OTHER` (text unstripped). CHANNEL/DM leave ``NONE``.

    Args:
        event: The raw Slack ``message`` event payload.
        bot_user_id: This bot's user id (``U…``), from ``auth.test``.
        bot_id: This bot's bot id (``B…``), from ``auth.test``.
        channel_ids: Watched channel ids (Slack strings); gates channel surfaces.
        respond_to_dms: Whether DMs (``im``/``mpim``) are answered.
        resolve_display: ``user_id -> display name`` (defaults to the id).

    Returns:
        The normalized :class:`IncomingMessage`, or ``None`` to drop the event.
    """
    subtype = event.get("subtype")
    if subtype in _IGNORE_SUBTYPES:
        return None
    if (
        (bot_id and event.get("bot_id") == bot_id)
        or subtype == "bot_message"
        or (bot_user_id and event.get("user") == bot_user_id)
    ):
        return None

    channel = event.get("channel", "")
    channel_type = event.get("channel_type", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts", "")
    user = event.get("user", "")
    text = (event.get("text") or "").strip()

    addressee = Addressee.NONE
    if channel_type in ("im", "mpim"):
        if not respond_to_dms:
            return None
        surface = Surface.DM
        thread_key = f"dm:{channel}"
        dest_thread_ts = ""
    else:
        if thread_ts and thread_ts != ts:
            surface = Surface.THREAD
            thread_key = f"th:{channel}-{thread_ts}"
            dest_thread_ts = thread_ts
            # Leading-mention verdict only matters in a thread.
            lead_self = re.compile(r"^\s*<@%s>\s*" % re.escape(bot_user_id))
            if bot_user_id and lead_self.match(text):
                addressee = Addressee.SELF
                text = lead_self.sub("", text, count=1).strip()
            elif (
                _LEAD_OTHER_USER_RE.match(text)
                or _LEAD_BROADCAST_RE.match(text)
                or _LEAD_SUBTEAM_RE.match(text)
            ):
                addressee = Addressee.OTHER
        else:
            # A top-level post in a channel: gate on the watched-channel list and
            # carry the triggering ts so open_thread can anchor a thread to it. A
            # CHANNEL post with no ts cannot anchor a thread (the binding would
            # key on `th:{channel}-` and a real reply could never match it), so
            # drop it defensively — real Slack always sends ts.
            if channel not in channel_ids or not ts:
                return None
            surface = Surface.CHANNEL
            thread_key = ""
            dest_thread_ts = ts

    dest = ChatDest(
        thread_key=thread_key,
        surface=surface,
        channel_id=channel,
        thread_ts=dest_thread_ts,
    )
    attachments = tuple(
        AttachmentRef(
            filename=(f.get("name") or f"file-{f.get('id', 'x')}"),
            size=int(f.get("size") or 0),
            url=(
                f.get("url_private_download")
                or f.get("url_private")
                or ""
            ),
            _native=f,
        )
        for f in (event.get("files") or [])
    )
    return IncomingMessage(
        text=text,
        dest=dest,
        surface=surface,
        author_id=user,
        author_display=resolve_display(user),
        addressee=addressee,
        attachments=attachments,
        channel_id=channel,
        ts=ts,
    )


class SlackTransport(ChatTransport):
    """``ChatTransport`` over the Slack Web API (Socket Mode bot).

    Holds the ``slack_bolt`` async app (its ``client`` makes every Web API call),
    the bot token (for authenticated file downloads), the config, a display-name
    cache, and a lazily created ``aiohttp`` session. One instance per running bot
    -- the core passes a :class:`ChatDest` to every send.
    """

    def __init__(self, app: Any, bot_token: str, cfg: TunnelConfig) -> None:
        """Keep the app/token/config and prepare the lazy caches."""
        self._app = app
        self._bot_token = bot_token
        self._cfg = cfg
        self._display: dict[str, str] = {}
        self._session: Any = None

    @property
    def limits(self) -> TransportLimits:
        """Slack's caps.

        ``chat.postMessage`` recommends 4000 chars (hard-truncates at 40000 --
        do NOT raise toward it). Slack ignores thread titles, so
        ``thread_title_max`` is effectively unbounded. ``list_truncate_len`` (the
        ``!list`` cap) stays ``<= max_message_len`` so the core's no-re-chunk
        ``send_text`` contract holds (G-08, G-19).
        """
        return TransportLimits(
            max_message_len=4000,
            max_attachments_per_msg=10,
            thread_title_max=10**9,
            max_error_len=3500,
            list_truncate_len=3900,
        )

    async def open_thread(self, parent: ChatDest, title: str) -> ChatDest:
        """Identify the Slack thread a conversation lives in (no API call).

        Slack mints no thread object: the triggering message's ts (carried on the
        channel-surface ``parent`` dest as ``thread_ts``) IS the parent. The
        thread materializes on the first send that passes that ``thread_ts``
        (G-18). ``title`` is ignored.

        Args:
            parent: The CHANNEL-surface dest whose ``thread_ts`` is the triggering
                message ts and whose ``channel_id`` is the channel.
            title: Ignored on Slack (threads are unnamed).

        Returns:
            The THREAD-surface dest carrying ``th:{channel}-{ts}`` + ``thread_ts``.
        """
        ts = parent.thread_ts
        return ChatDest(
            thread_key=f"th:{parent.channel_id}-{ts}",
            surface=Surface.THREAD,
            channel_id=parent.channel_id,
            thread_ts=ts,
        )

    async def send_text(self, dest: ChatDest, text: str) -> None:
        """Post one ``mrkdwn`` message to ``dest`` (no re-chunking).

        The core has already chunked to ``limits.max_message_len``; this only
        translates to ``mrkdwn`` and forwards the SAME ``thread_ts`` as every
        other send in the conversation, so all replies land in one thread (G-25).

        Args:
            dest: Where to post (its ``channel_id`` + ``thread_ts``).
            text: One core-emitted chunk.
        """
        await self._app.client.chat_postMessage(
            channel=dest.channel_id,
            thread_ts=dest.thread_ts or None,
            text=discord_to_mrkdwn(text),
        )

    async def send_files(
        self, dest: ChatDest, caption: str, files: Sequence[OutgoingFile]
    ) -> None:
        """Post one message carrying ``caption`` plus one or more files.

        One ``files_upload_v2`` call (``channel`` is SINGULAR in v2) with the
        batch in ``file_uploads`` and the translated ``caption`` as
        ``initial_comment``. Each entry carries ``filename`` AND ``title`` (Slack
        strips the extension from the filename alone, G-13). Same ``thread_ts`` as
        every other send (G-25); the returned ts is not read back.

        Args:
            dest: Where to post.
            caption: Message text accompanying the files (markdown-translated).
            files: The batch (already size-filtered + count-capped by the core).
        """
        file_uploads = [
            (
                {
                    "content": of.data,
                    "filename": of.filename,
                    "title": of.filename,
                }
                if of.data is not None
                else {
                    "file": str(of.path),
                    "filename": of.filename,
                    "title": of.filename,
                }
            )
            for of in files
        ]
        await self._app.client.files_upload_v2(
            channel=dest.channel_id,
            thread_ts=dest.thread_ts or None,
            initial_comment=discord_to_mrkdwn(caption),
            file_uploads=file_uploads,
        )

    async def download_attachment(
        self, attachment: AttachmentRef, target: Path
    ) -> None:
        """Download one inbound Slack attachment to local ``target``.

        Authenticated GET of ``attachment.url`` (``url_private_download``) with a
        bearer header that MUST survive the file-host redirect: Slack file URLs
        redirect to a CDN host and a client that drops the auth header lands on a
        ``200 text/html`` login page. We disable auto-redirect and re-issue the
        request to each ``Location`` with the bearer header re-attached, then
        :func:`validate_download` raises on an HTML/error response BEFORE bytes
        are written (G-15). Raises on failure so the core marks the file skipped.

        Args:
            attachment: The inbound ref (its ``url`` is the authenticated URL).
            target: Local path to write (its parent already exists).

        Raises:
            RuntimeError: The response is an HTML login page or an error status.
        """
        import aiohttp

        if self._session is None:
            self._session = aiohttp.ClientSession()
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        url = attachment.url
        # Follow redirects MANUALLY, re-attaching the bearer header on each hop,
        # so the cross-origin redirect to Slack's file CDN keeps the auth.
        for _ in range(5):
            async with self._session.get(
                url, headers=headers, allow_redirects=False
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        validate_download(
                            resp.headers.get("Content-Type", ""), resp.status
                        )
                        return
                    url = location
                    continue
                validate_download(
                    resp.headers.get("Content-Type", ""), resp.status
                )
                data = await resp.read()
                target.write_bytes(data)
                return
        raise RuntimeError("Slack file download failed (too many redirects)")

    async def aclose(self) -> None:
        """Close the lazily created download session (idempotent, G-shutdown).

        ``download_attachment`` opens one ``aiohttp.ClientSession`` on first use;
        ``run_slack_bot``'s shutdown ``finally`` awaits this so the session +
        connector are released instead of leaking (the 'Unclosed client session'
        warning). A no-op when no download ever ran.
        """
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def add_reaction(
        self, message: IncomingMessage, emoji: str
    ) -> None:
        """React to ``message`` with the cooldown 'busy' signal (best-effort).

        The core passes the neutral name ``"hourglass"``; Slack uses the
        ``hourglass_flowing_sand`` shortcode (the ⏳ flowing-sand glyph, matching
        Discord's U+23F3). Swallows the Slack API error so a reaction-scope or
        already-reacted failure never breaks a turn (G-02).

        Args:
            message: The triggering message (its ``channel_id`` + ``ts``).
            emoji: The neutral cooldown signal name (``"hourglass"``).
        """
        try:
            # slack_sdk is the optional `slack` extra (not installed in the
            # type-check env, so `# type: ignore`); guarded by the except below.
            from slack_sdk.errors import SlackApiError  # type: ignore
        except ImportError:  # pragma: no cover - dep present when bot runs
            SlackApiError = Exception  # type: ignore[assignment,misc]
        try:
            await self._app.client.reactions_add(
                channel=message.channel_id,
                timestamp=message.ts,
                name="hourglass_flowing_sand",
            )
        except SlackApiError:
            logger.debug("reactions_add failed (best-effort)", exc_info=True)

    def activity(self, dest: ChatDest) -> Any:
        """A no-op 'working' indicator (Slack has no typing API over the socket)."""
        return noop_activity()

    async def resolve_display(self, user_id: str) -> str:
        """Resolve a Slack user id to a display name (cached ``users_info``).

        Returns the best available name: profile ``display_name`` ->
        ``real_name`` -> user ``real_name`` -> ``name`` -> the raw id. Cached, and
        called only from the background turn (never on the ack path, G-14/G-display).
        Any API failure falls back to the raw id.

        Args:
            user_id: The Slack user id (``U…``).

        Returns:
            A human display name, or ``user_id`` if it can't be resolved.
        """
        if not user_id:
            return user_id
        if user_id in self._display:
            return self._display[user_id]
        name = user_id
        try:
            resp = await self._app.client.users_info(user=user_id)
            user = resp.get("user", {}) or {}
            profile = user.get("profile", {}) or {}
            name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
                or user_id
            )
        except Exception:  # network / scope / unknown user
            logger.debug("users_info failed for %s", user_id, exc_info=True)
        self._display[user_id] = name
        return name


async def _run_turn(
    core: ChatCore,
    transport: "SlackTransport",
    msg: IncomingMessage,
) -> None:
    """Resolve the display name (background) then hand the turn to the core.

    Kept off the ack path: the listener spawns this so the awaited
    ``users_info`` lookup never delays the 3-second Socket-Mode ack (G-14). The
    transport already closes over the ``slack_bolt`` app, so it is not passed in.

    Args:
        core: The shared routing/pipeline engine.
        transport: The Slack transport (its cached display-name resolver).
        msg: The pre-ack message (its ``author_display`` is the raw id so far).
    """
    name = await transport.resolve_display(msg.author_id)
    msg = replace(msg, author_display=name)
    await core.handle_message(msg)


def run_slack_bot(
    cfg: TunnelConfig,
    store: TunnelStore,
    registry: Registry,
) -> None:
    """Run the Slack bot until interrupted (blocking). Mirrors ``run_bot``.

    Resolves both tokens, runs ``auth.test`` once (to cache the bot's ids BEFORE
    the socket opens, so the self/bot drop is never bypassed on the first event,
    G-20), builds one :class:`SlackTransport` + :class:`ChatCore`, schedules the
    reaper on the running loop (G-28), and serves ``message`` events over Socket
    Mode. A single ``@app.event("message")`` listener does ONLY synchronous
    drop/classify/dedup then spawns the turn and returns within the 3s ack window
    (G-14); there is deliberately NO ``app_mention`` listener (G-double-answer).

    Args:
        cfg: The tunnel config (its ``slack`` block + limits).
        store: The thread/binding store.
        registry: The published-handle registry.

    Raises:
        RuntimeError: A token is missing, no channels are watched and DMs are
            disabled (the bot would never respond), or ``auth.test`` fails (an
            invalid/expired bot token). ``auth.test`` runs in ``_main`` BEFORE
            the socket opens and its ``SlackApiError`` is wrapped into a
            ``RuntimeError`` so ``serve`` maps it to a clean ``ClickException``
            instead of leaking a traceback.
    """
    import asyncio

    # slack_bolt is the optional `slack` extra (not installed in the type-check
    # env, so pyright can't resolve it -> `# type: ignore` on each import line);
    # the ImportError is surfaced to the user at the `serve` call site.
    from slack_bolt.adapter.socket_mode.async_handler import (  # type: ignore
        AsyncSocketModeHandler,
    )
    from slack_bolt.app.async_app import AsyncApp  # type: ignore

    bot_token = resolve_token(cfg.slack.bot_token_env, cfg.slack.bot_token_file)
    app_token = resolve_token(cfg.slack.app_token_env, cfg.slack.app_token_file)
    if not bot_token:
        raise RuntimeError(
            f"No Slack bot token (set {cfg.slack.bot_token_env} or "
            "slack.bot_token_file)"
        )
    if not app_token:
        raise RuntimeError(
            f"No Slack app-level token (set {cfg.slack.app_token_env} or "
            "slack.app_token_file)"
        )
    if not cfg.slack.channel_ids and not cfg.slack.respond_to_dms:
        raise RuntimeError(
            "No slack.channel_ids configured and DMs are disabled — "
            "the bot would never respond."
        )

    app = AsyncApp(token=bot_token)
    transport = SlackTransport(app, bot_token, cfg)
    core = ChatCore(cfg, store, registry, transport)
    channel_ids = {str(c) for c in cfg.slack.channel_ids}
    seen = _SeenCache(cap=4096)
    state = {"bot_user_id": "", "bot_id": ""}
    inflight: "set[asyncio.Task]" = set()

    @app.event("message")
    async def _on_message(event: dict, body: dict) -> None:
        # SYNCHRONOUS pre-ack work only: drop, classify, dedup, build a
        # lightweight IncomingMessage (author_display = raw id), then spawn the
        # turn and return well inside the 3s ack window (G-14).
        msg = slack_event_to_incoming(
            event,
            bot_user_id=state["bot_user_id"],
            bot_id=state["bot_id"],
            channel_ids=channel_ids,
            respond_to_dms=cfg.slack.respond_to_dms,
        )
        if msg is None:
            return
        key = event.get("client_msg_id") or (
            f'{event.get("channel")}-{event.get("ts")}'
        )
        if not seen.add(key):
            return
        # Bound the fan-out: per-conversation order is the core's per-thread_key
        # lock and the global semaphore caps backend asks, but the spawned-task
        # set itself must not grow without limit (G-27). The busy notice is a
        # chat_postMessage round-trip, so it must NOT be awaited on the pre-ack
        # path (G-14) — spawn it fire-and-forget (tracked so it isn't GC'd) and
        # return immediately, keeping the listener body free of awaited Web API
        # calls even under overload.
        if len(inflight) >= _MAX_INFLIGHT:
            notice = asyncio.create_task(
                transport.send_text(
                    msg.dest,
                    "⏳ Busy right now — please try again shortly.",
                )
            )
            inflight.add(notice)
            notice.add_done_callback(inflight.discard)
            return
        task = asyncio.create_task(_run_turn(core, transport, msg))
        inflight.add(task)
        task.add_done_callback(inflight.discard)

    async def _main() -> None:
        # Cache the bot's ids BEFORE opening the socket so the self/bot drop is
        # never bypassed on the first event (G-20). auth.test runs here, before
        # start_async; a bad/expired token raises SlackApiError (an Exception,
        # NOT a RuntimeError), so wrap it into RuntimeError to surface as serve's
        # clean ClickException rather than a raw traceback.
        try:
            auth = await app.client.auth_test()
        except Exception as exc:
            raise RuntimeError(
                f"Slack auth.test failed (check the bot token): {exc}"
            ) from exc
        state["bot_user_id"] = auth["user_id"]
        state["bot_id"] = auth["bot_id"]
        handler = AsyncSocketModeHandler(app, app_token)
        reaper = asyncio.create_task(core.run_reaper())  # same loop (G-28)
        try:
            await handler.start_async()
        finally:
            reaper.cancel()
            await handler.close_async()
            await transport.aclose()  # release the download session (G-shutdown)

    # asyncio.run raises KeyboardInterrupt on Ctrl-C; cli.main() maps it to
    # exit(130). In-flight turns finish on their own per-thread locks (G-shutdown).
    asyncio.run(_main())
