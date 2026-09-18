"""Mattermost front-end for agent-tunnel (same model as the Discord bot).

- A teammate posts ``<handle> [question]`` in a watched channel. If the
  handle is live, the bot replies *in that post's thread* (Mattermost threads
  are replies to a root post), binds the thread to the published session, and
  answers.
- Replies inside that thread are follow-ups to the same fork; ``!done``
  closes it, ``!list`` lists handles, and a reply that opens with
  ``@someone-else`` is side-chat the bot ignores.

Only the Mattermost wiring lives here: a small REST + WebSocket client on
``aiohttp`` (already a dependency of discord.py) and the routing decision.
The turn itself runs in the shared :class:`~.relay.Relay`. Direct messages
are not handled (Discord's optional DM mode has no Mattermost counterpart
yet).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Sequence

from .config import TunnelConfig
from .registry import HANDLE_RE, Registry
from .relay import Relay, is_close_command, is_list_command

logger = logging.getLogger("agent_tunnel")

PLATFORM = "Mattermost"
# Mattermost's hard cap is 16383 characters; stay a little under it.
MM_MSG_LIMIT = 16000
MM_MAX_FILES = 5
TYPING_EVERY_S = 4.0
BROADCAST_MENTIONS = {"all", "channel", "here"}
_MENTION_RE = re.compile(r"^@([A-Za-z0-9._-]+)")


def resolve_mm_token(cfg: TunnelConfig) -> str:
    """Resolve the Mattermost bot token: env var first, then token_file."""
    mm = cfg.mattermost
    token = os.environ.get(mm.token_env, "").strip()
    if not token and mm.token_file:
        path = Path(mm.token_file).expanduser()
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
    return token


def mattermost_ready(cfg: TunnelConfig) -> Optional[str]:
    """None if the Mattermost front-end can run, else why it can't."""
    mm = cfg.mattermost
    if not mm.url:
        return "No mattermost.url configured"
    if not resolve_mm_token(cfg):
        return (
            f"No Mattermost token found (set {mm.token_env} or "
            "mattermost.token_file in the config)"
        )
    if not mm.channel_ids:
        return "No mattermost.channel_ids configured — the bot would never respond."
    return None


def leading_mention(text: str) -> Optional[str]:
    """Lower-cased username of a leading ``@mention``, else None."""
    match = _MENTION_RE.match(text.lstrip())
    return match.group(1).lower().rstrip(".") if match else None


@dataclass
class MMFile:
    """A file attached to a Mattermost post."""

    id: str
    name: str
    size: int = 0


@dataclass
class MMPost:
    """The parts of a Mattermost post the router needs."""

    id: str
    channel_id: str
    user_id: str
    root_id: str
    message: str
    sender: str = ""
    from_bot: bool = False
    system: bool = False
    direct: bool = False
    files: list[MMFile] = field(default_factory=list)


def parse_posted(event: dict[str, Any]) -> Optional[MMPost]:
    """Turn a websocket ``posted`` event into an :class:`MMPost`.

    Returns None for any other event or a payload that can't be parsed.
    """
    if event.get("event") != "posted":
        return None
    data = event.get("data") or {}
    try:
        post = json.loads(data.get("post") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not post.get("id"):
        return None
    props = post.get("props") or {}
    meta_files = (post.get("metadata") or {}).get("files") or []
    files = [
        MMFile(id=f["id"], name=f.get("name") or "file", size=f.get("size") or 0)
        for f in meta_files
        if f.get("id")
    ]
    known = {f.id for f in files}
    files += [MMFile(id=i, name="file") for i in post.get("file_ids") or []
              if i not in known]
    return MMPost(
        id=post["id"],
        channel_id=post.get("channel_id", ""),
        user_id=post.get("user_id", ""),
        root_id=post.get("root_id") or "",
        message=(post.get("message") or "").strip(),
        sender=(data.get("sender_name") or "").lstrip("@"),
        from_bot=str(props.get("from_bot", "")).lower() == "true",
        system=bool(post.get("type")),
        direct=data.get("channel_type") in ("D", "G"),
        files=files,
    )


@dataclass
class Route:
    """What the bot should do with one post."""

    action: str  # ignore | list | open | unknown_handle | followup | close
    thread_key: str = ""
    root_id: str = ""
    handle: str = ""
    text: str = ""


def route_post(
    post: MMPost,
    *,
    bot_user_id: str,
    bot_username: str,
    channel_ids: Sequence[str],
    allowed_user_ids: Sequence[str],
    is_bound: Callable[[str], bool],
    handle_live: Callable[[str], bool],
) -> Route:
    """Decide what to do with a post (pure; no I/O).

    Args:
        post: The incoming post.
        bot_user_id: The bot's own user id (its posts are ignored).
        bot_username: The bot's username (a leading ``@bot`` is stripped).
        channel_ids: Watched channel ids.
        allowed_user_ids: If non-empty, only these users are answered.
        is_bound: Whether a thread key is bound to a session.
        handle_live: Whether a handle is currently shared.

    Returns:
        The routing decision.
    """
    ignore = Route("ignore")
    if (
        post.user_id == bot_user_id
        or post.from_bot
        or post.system
        or post.direct
        or post.channel_id not in channel_ids
    ):
        return ignore
    if not post.message and not post.files:
        return ignore
    if allowed_user_ids and post.user_id not in allowed_user_ids:
        return ignore
    text = post.message

    if post.root_id:
        key = f"mm:{post.root_id}"
        if not is_bound(key):
            return ignore
        # A reply that opens with @someone-else (or @all/@channel/@here) is
        # teammates talking among themselves. A leading @bot is stripped.
        who = leading_mention(text)
        if who is not None:
            if who in BROADCAST_MENTIONS or who != bot_username.lower():
                return ignore
            text = _MENTION_RE.sub("", text.lstrip(), count=1).strip()
        if is_list_command(text):
            return Route("list", thread_key=key, root_id=post.root_id)
        if is_close_command(text):
            return Route("close", thread_key=key, root_id=post.root_id)
        return Route("followup", thread_key=key, root_id=post.root_id, text=text)

    if is_list_command(text):
        return Route("list", root_id=post.id)
    token, _, remainder = text.partition(" ")
    handle = token.strip().lower()
    if handle_live(handle):
        return Route(
            "open",
            thread_key=f"mm:{post.id}",
            root_id=post.id,
            handle=handle,
            text=remainder.strip(),
        )
    # Only complain if it clearly looks like a handle attempt.
    if HANDLE_RE.match(handle) and not remainder:
        return Route("unknown_handle", root_id=post.id, handle=handle)
    return ignore


class MattermostAPI:
    """Minimal Mattermost v4 REST + WebSocket client on aiohttp."""

    def __init__(self, url: str, token: str, verify_tls: bool = True) -> None:
        """Remember the server; the HTTP session opens in ``start``."""
        self.base = url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls
        self.session: Any = None
        self.ws: Any = None
        self._seq = 0

    async def start(self) -> None:
        """Open the HTTP session."""
        import aiohttp

        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.token}"},
            connector=aiohttp.TCPConnector(ssl=None if self.verify_tls else False),
        )

    async def close(self) -> None:
        """Close the websocket and HTTP session."""
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        async with self.session.request(
            method, f"{self.base}/api/v4{path}", **kw
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(
                    f"Mattermost {method} {path} → {resp.status}: {body}"
                )
            return await resp.json()

    async def me(self) -> dict[str, Any]:
        """The bot's own user record."""
        return await self._json("GET", "/users/me")

    async def create_post(
        self,
        channel_id: str,
        message: str,
        root_id: str = "",
        file_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Post a message (as a thread reply when ``root_id`` is set)."""
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        if file_ids:
            body["file_ids"] = list(file_ids)
        return await self._json("POST", "/posts", json=body)

    async def upload(self, channel_id: str, name: str, data: bytes) -> str:
        """Upload a file to a channel; returns its file id."""
        import aiohttp

        form = aiohttp.FormData()
        form.add_field("channel_id", channel_id)
        form.add_field("files", data, filename=name)
        out = await self._json("POST", "/files", data=form)
        return out["file_infos"][0]["id"]

    async def file_info(self, file_id: str) -> dict[str, Any]:
        """A file's metadata (name, size)."""
        return await self._json("GET", f"/files/{file_id}/info")

    async def download(self, file_id: str, max_bytes: int) -> bytes:
        """Fetch a file's bytes, refusing more than ``max_bytes``."""
        async with self.session.get(
            f"{self.base}/api/v4/files/{file_id}"
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"download {file_id} → {resp.status}")
            data = bytearray()
            async for chunk in resp.content.iter_chunked(1 << 16):
                data += chunk
                if len(data) > max_bytes:
                    raise RuntimeError(f"download {file_id} exceeds size cap")
            return bytes(data)

    async def react(self, user_id: str, post_id: str, emoji: str) -> None:
        """Add an emoji reaction to a post."""
        await self._json(
            "POST",
            "/reactions",
            json={"user_id": user_id, "post_id": post_id, "emoji_name": emoji},
        )

    async def connect_ws(self) -> Any:
        """Open and authenticate the event websocket."""
        ws_url = re.sub(r"^http", "ws", self.base) + "/api/v4/websocket"
        self.ws = await self.session.ws_connect(ws_url, heartbeat=30)
        await self.ws_send(
            "authentication_challenge", {"token": self.token}
        )
        return self.ws

    async def ws_send(self, action: str, data: dict[str, Any]) -> None:
        """Send a websocket action (best effort)."""
        if self.ws is None or self.ws.closed:
            return
        self._seq += 1
        await self.ws.send_json({"seq": self._seq, "action": action, "data": data})


class MMUpload:
    """A Mattermost attachment as a relay :class:`~.relay.Upload`."""

    def __init__(self, api: MattermostAPI, file: MMFile, max_bytes: int) -> None:
        """Wrap one attached file (``size`` must already be known)."""
        self.api = api
        self.file_id = file.id
        self.filename = file.name
        self.size = file.size
        self.max_bytes = max_bytes

    async def save(self, path: str) -> None:
        """Download to ``path`` (the download itself stops at the cap)."""
        data = await self.api.download(self.file_id, self.max_bytes)
        await asyncio.to_thread(Path(path).write_bytes, data)


class MMDest:
    """A Mattermost thread as a relay :class:`~.relay.Destination`."""

    max_len = MM_MSG_LIMIT
    max_files = MM_MAX_FILES

    def __init__(self, api: MattermostAPI, channel_id: str, root_id: str) -> None:
        """Replies go into ``root_id``'s thread in ``channel_id``."""
        self.api = api
        self.channel_id = channel_id
        self.root_id = root_id

    async def send(self, text: str) -> None:
        """Post one reply."""
        await self.api.create_post(self.channel_id, text, self.root_id)

    async def send_text_file(self, preview: str, name: str, data: bytes) -> None:
        """Post a preview with the full text attached."""
        fid = await self.api.upload(self.channel_id, name, data)
        await self.api.create_post(self.channel_id, preview, self.root_id, [fid])

    async def send_files(self, caption: str, paths: Sequence[Path]) -> None:
        """Post local files as attachments."""
        ids = [
            await self.api.upload(self.channel_id, p.name, p.read_bytes())
            for p in paths
        ]
        await self.api.create_post(self.channel_id, caption, self.root_id, ids)

    @asynccontextmanager
    async def typing(self) -> AsyncIterator[None]:
        """Keep a typing indicator alive in the thread while the block runs."""

        async def pulse() -> None:
            while True:
                try:
                    await self.api.ws_send(
                        "user_typing",
                        {"channel_id": self.channel_id, "parent_id": self.root_id},
                    )
                except Exception:
                    pass  # typing is cosmetic; never fail a turn over it
                await asyncio.sleep(TYPING_EVERY_S)

        task = asyncio.create_task(pulse())
        try:
            yield
        finally:
            task.cancel()


async def run_mattermost(cfg: TunnelConfig, relay: Relay) -> None:
    """Run the Mattermost bot until cancelled, reconnecting on drops.

    Login and websocket failures (server down, 5xx, a rejected token) are
    retried with backoff, never raised, so they can't stop other front-ends.

    Raises:
        RuntimeError: Not configured (see ``mattermost_ready``).
    """
    import aiohttp

    problem = mattermost_ready(cfg)
    if problem:
        raise RuntimeError(problem)
    mm = cfg.mattermost
    api = MattermostAPI(mm.url, resolve_mm_token(cfg), mm.verify_tls)
    await api.start()
    tasks: set[asyncio.Task[None]] = set()
    bot: Optional[_Router] = None
    try:
        backoff = 1.0
        while True:
            try:
                if bot is None:
                    me = await api.me()
                    bot = _Router(cfg, relay, api, me["id"], me.get("username", ""))
                    logger.info(
                        "Mattermost: logged in as @%s; watching channels %s",
                        me.get("username"),
                        mm.channel_ids,
                    )
                ws = await api.connect_ws()
                logger.info("Mattermost: websocket connected")
                backoff = 1.0
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        event = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    post = parse_posted(event)
                    if post is None:
                        continue
                    task = asyncio.create_task(bot.handle(post))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                logger.warning("Mattermost: websocket closed; reconnecting")
            except (
                aiohttp.ClientError,
                OSError,
                asyncio.TimeoutError,
                RuntimeError,
            ) as exc:
                # Includes a rejected token or a 5xx at login: keep retrying
                # (with backoff) rather than take the other front-ends down.
                logger.warning(
                    "Mattermost: connection error (%s); retrying in %.0fs",
                    exc,
                    backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
    finally:
        for task in tasks:
            task.cancel()
        await api.close()


class _Router:
    """Carries out :func:`route_post` decisions against the relay."""

    def __init__(
        self,
        cfg: TunnelConfig,
        relay: Relay,
        api: MattermostAPI,
        bot_user_id: str,
        bot_username: str,
    ) -> None:
        self.cfg = cfg
        self.relay = relay
        self.api = api
        self.bot_user_id = bot_user_id
        self.bot_username = bot_username
        self.registry: Registry = relay.registry

    def _cap(self) -> int:
        return int(self.cfg.limits.max_attachment_mb * 1024 * 1024)

    async def _sized(self, files: list[MMFile]) -> list[MMFile]:
        """Fill in sizes the event omitted, so the relay's cap applies.

        A file whose size can't be learned is treated as over the cap.
        """
        out: list[MMFile] = []
        for f in files:
            if f.size <= 0:
                try:
                    info = await self.api.file_info(f.id)
                    f = MMFile(
                        f.id, info.get("name") or f.name, info.get("size") or 0
                    )
                except Exception:
                    logger.warning("Mattermost: no info for file %s", f.id)
                if f.size <= 0:
                    f = MMFile(f.id, f.name, self._cap() + 1)
            out.append(f)
        return out

    async def handle(self, post: MMPost) -> None:
        """Route one post; errors are logged, never raised."""
        try:
            await self._handle(post)
        except Exception:
            logger.exception("Mattermost: failed handling post %s", post.id)

    async def _handle(self, post: MMPost) -> None:
        relay = self.relay
        route = route_post(
            post,
            bot_user_id=self.bot_user_id,
            bot_username=self.bot_username,
            channel_ids=self.cfg.mattermost.channel_ids,
            allowed_user_ids=self.cfg.mattermost.allowed_user_ids,
            is_bound=lambda key: relay.store.get(key) is not None,
            handle_live=lambda h: self.registry.get(h) is not None,
        )
        if route.action == "ignore":
            return
        dest = MMDest(self.api, post.channel_id, route.root_id)
        sender = post.sender or "A teammate"
        uploads = [
            MMUpload(self.api, f, self._cap()) for f in await self._sized(post.files)
        ]
        if route.action == "list":
            await dest.send(relay.handles_text())
        elif route.action == "unknown_handle":
            await dest.send(
                f"No live session for handle `{route.handle}`. "
                "Ask the owner to `>share` it."
            )
        elif route.action == "close":
            await relay.close(dest, route.thread_key)
        elif route.action == "open":
            rec = self.registry.get(route.handle)
            if rec is None:
                return
            logger.info(
                "Opened Mattermost thread for handle %s (session %s) asked by %s",
                rec.handle,
                rec.session_id[:8],
                sender,
            )
            relay.bind(route.thread_key, rec, sender, PLATFORM)
            if route.text or uploads:
                await relay.answer(
                    dest, route.thread_key, route.text, uploads, sender=sender
                )
            else:
                await dest.send(
                    f"Connected to **{rec.label or rec.handle}**. Ask your "
                    "question in this thread; follow-ups stay here."
                )
        elif route.action == "followup":
            if not relay.cooldown_ok(("mattermost", post.user_id)):
                try:
                    await self.api.react(
                        self.bot_user_id, post.id, "hourglass_flowing_sand"
                    )
                except Exception:
                    logger.debug("Mattermost: reaction failed", exc_info=True)
                return
            await relay.answer(
                dest, route.thread_key, route.text, uploads, sender=sender
            )
