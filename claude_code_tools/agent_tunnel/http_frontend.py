"""HTTP front-end: ask a published session from a program.

The chat front-ends answer people in channels; this one answers a caller
over HTTP, for things like a docs site's "Ask" button. It runs inside the
same ``agent-tunnel serve`` daemon and goes through the same
:class:`~.relay.Relay`, so the concurrency cap, per-thread locks, fork
refresh and logging are shared with Discord and Mattermost.

Routes (``[http]`` in the config; off unless ``port`` is set):

    GET  /health
    POST /ask   {"handle": ..., "question": ..., "thread": ..., "sender": ...}

Every route requires ``X-Ask-Token: <contents of http.token_file>``.
``thread`` is any caller-chosen id; questions with the same handle and
thread share one fork, so follow-ups remember earlier turns. A thread that
already exists keeps working after its handle is revoked, as chat threads
do; only a new thread needs a live handle. The response says whether a
turn ran (``ran``) separately from what it produced, and a failed turn is
an HTTP 502 carrying the relay's own error text, never an empty answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import re
from pathlib import Path
from typing import Any, Optional, Sequence

from aiohttp import web

from .config import TunnelConfig
from .relay import QUEUE_NOTICE, Relay

logger = logging.getLogger("agent_tunnel.http")

PLATFORM = "http"
MAX_QUESTION = 8000
MAX_SENDER = 200
THREAD_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def resolve_http_token(cfg: TunnelConfig) -> str:
    """The shared secret callers must present, or an empty string."""
    if not cfg.http.token_file:
        return ""
    path = Path(cfg.http.token_file).expanduser()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def http_ready(cfg: TunnelConfig) -> Optional[str]:
    """None if the HTTP front-end can run, else why it can't."""
    if not cfg.http.port:
        return "No http.port configured"
    port = cfg.http.port
    if type(port) is not int or not 1 <= port <= 65535:  # bool is an int, so type()
        return f"http.port must be an integer from 1 to 65535, not {port!r}"
    if not resolve_http_token(cfg):
        return (
            "No HTTP token found (set http.token_file to a file holding the "
            "shared secret callers send as X-Ask-Token)"
        )
    return None


class CollectDest:
    """A relay destination that keeps the answer in memory.

    Long answers arrive as a preview plus an ``answer.md`` file; the file's
    text wins, and anything the relay posts after it (a warning about an
    oversized deliverable, say) is kept as a notice. Whether the turn failed
    is not read from this text: :meth:`Relay.answer` returns the problem.
    """

    max_len = 1_000_000
    max_files = 10

    def __init__(self) -> None:
        """Start with nothing collected."""
        self.chunks: list[str] = []
        self.full_text: Optional[str] = None
        self.notices: list[str] = []
        self.files: list[Path] = []

    async def send(self, text: str) -> None:
        """Record one chat-sized chunk, or a notice.

        The relay's exact queue notice, and anything posted after the full
        answer file, are notices; every other chunk is answer text.
        """
        if text == QUEUE_NOTICE or self.full_text is not None:
            self.notices.append(text)
        else:
            self.chunks.append(text)

    async def send_text_file(self, preview: str, name: str, data: bytes) -> None:
        """Record the full answer that was too long for chat."""
        del preview, name
        self.full_text = data.decode("utf-8", errors="replace")

    async def send_files(self, caption: str, paths: Sequence[Path]) -> None:
        """Record deliverable paths; the HTTP caller gets their names only."""
        del caption
        self.files.extend(paths)

    def typing(self) -> contextlib.AbstractAsyncContextManager[Any]:
        """No typing indicator over HTTP."""
        return contextlib.nullcontext()

    @property
    def text(self) -> str:
        """The answer as one string."""
        if self.full_text is not None:
            return self.full_text
        return "".join(self.chunks)


def _json(status: int, body: dict[str, Any]) -> web.Response:
    return web.json_response(body, status=status)


def _field(body: dict[str, Any], name: str, limit: int) -> tuple[str, str]:
    """A declared string field, or an error message; over-limit is an error."""
    value = body.get(name)
    if value is None:
        value = ""
    if not isinstance(value, str):
        return "", f"{name} must be a string"
    value = value.strip()
    if len(value) > limit:
        return "", f"{name} is longer than {limit} characters"
    return value, ""


async def _turn(
    cfg: TunnelConfig,
    relay: Relay,
    thread_key: str,
    handle: str,
    thread: str,
    question: str,
    sender: str,
) -> web.Response:
    """Bind if new, run one relay turn, and shape the JSON reply.

    Every store, registry and relay call happens here, under the caller's
    exception handler, so any failure keeps the JSON contract.
    """
    if relay.store.get(thread_key) is None:
        rec = relay.registry.get(handle)
        if rec is None or rec.revoked:
            return _json(404, {"ran": False, "error": f"no live handle {handle!r}"})
        relay.bind(thread_key, rec, sender or "http", cfg.http.platform)
    dest = CollectDest()
    problem = await relay.answer(dest, thread_key, question, sender=sender)
    if problem is not None or not dest.text:
        error = problem or "the turn produced no answer"
        return _json(502, {"ran": False, "error": error, "thread": thread})
    record = relay.store.get(thread_key)
    return _json(
        200,
        {
            "ran": True,
            "answer": dest.text,
            "thread": thread,
            "handle": handle,
            "fork_session_id": record.fork_session_id if record else "",
            "files": [p.name for p in dest.files],
            "notices": dest.notices,
        },
    )


def make_app(cfg: TunnelConfig, relay: Relay) -> web.Application:
    """The aiohttp application; separated from serving so tests can drive it."""
    token = resolve_http_token(cfg).encode("utf-8")

    @web.middleware
    async def require_token(request: web.Request, handler: Any) -> web.Response:
        given = request.headers.get("X-Ask-Token", "").encode("utf-8")
        if not token or not hmac.compare_digest(given, token):
            return _json(401, {"ran": False, "error": "bad token"})
        return await handler(request)

    async def health(_request: web.Request) -> web.Response:
        handles = [r.handle for r in relay.registry.active()]
        return _json(200, {"ok": True, "handles": handles})

    async def ask(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any malformed body is a 400
            return _json(400, {"ran": False, "error": "body is not JSON"})
        if not isinstance(body, dict):
            return _json(400, {"ran": False, "error": "body must be a JSON object"})
        fields: dict[str, str] = {}
        for name, limit in (
            ("handle", 80),
            ("question", MAX_QUESTION),
            ("thread", 80),
            ("sender", MAX_SENDER),
        ):
            value, err = _field(body, name, limit)
            if err:
                status = 413 if "longer" in err else 400
                return _json(status, {"ran": False, "error": err})
            fields[name] = value
        # The registry keys handles case-insensitively; so must thread keys.
        handle, question = fields["handle"].lower(), fields["question"]
        thread, sender = fields["thread"], fields["sender"]
        if not handle or not question:
            return _json(
                400, {"ran": False, "error": "handle and question are required"}
            )
        if not THREAD_RE.match(thread):
            return _json(
                400, {"ran": False, "error": "thread must match [A-Za-z0-9_.:-]{1,80}"}
            )
        # The same per-user cooldown the chat front-ends apply, keyed by the
        # sender the caller vouches for, else by the caller's address.
        caller = sender or (request.remote or "unknown")
        if not relay.cooldown_ok((PLATFORM, caller)):
            wait = cfg.limits.per_user_cooldown_s
            return _json(
                429,
                {"ran": False, "error": f"cooldown: one question per {wait:g} s"},
            )
        thread_key = f"{PLATFORM}:{handle}:{thread}"
        try:
            return await _turn(cfg, relay, thread_key, handle, thread, question, sender)
        except Exception:  # noqa: BLE001 - keep the JSON contract on any failure
            logger.exception("HTTP turn failed [%s]", thread_key)
            return _json(
                502,
                {
                    "ran": False,
                    "error": "unexpected failure; see the daemon log",
                    "thread": thread,
                },
            )

    app = web.Application(client_max_size=256 * 1024, middlewares=[require_token])
    app.router.add_get("/health", health)
    app.router.add_post("/ask", ask)
    return app


async def run_http(cfg: TunnelConfig, relay: Relay) -> None:
    """Serve the HTTP front-end until cancelled."""
    runner = web.AppRunner(make_app(cfg, relay))
    await runner.setup()
    site = web.TCPSite(runner, cfg.http.bind, cfg.http.port)
    await site.start()
    logger.info("HTTP front-end listening on %s:%d", cfg.http.bind, cfg.http.port)
    try:
        await asyncio.Event().wait()  # until cancelled
    finally:
        await runner.cleanup()
