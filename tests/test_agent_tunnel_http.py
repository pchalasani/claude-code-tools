"""Tests for the agent-tunnel HTTP front-end.

A fake ``claude`` binary answers every turn, so these exercise the whole
path (token check, binding, the shared relay, fork persistence) without a
real model or network.
"""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_tools.agent_tunnel.config import TunnelConfig, load_config
from claude_code_tools.agent_tunnel.http_frontend import (
    CollectDest,
    http_ready,
    make_app,
)
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.relay import QUEUE_NOTICE, Relay
from claude_code_tools.agent_tunnel.serve import plan_frontends
from claude_code_tools.agent_tunnel.store import TunnelStore

FAKE_CLAUDE = """\
import json, sys, uuid
argv = sys.argv[1:]
prompt = sys.stdin.read()
resume = argv[argv.index("--resume") + 1] if "--resume" in argv else ""
forked = "--fork-session" in argv
sid = "fork-" + str(uuid.uuid4())[:8] if forked else resume
print(json.dumps({"result": "ANSWER to: " + prompt.strip()[-60:],
                  "session_id": sid}))
"""


@pytest.fixture
def stack(tmp_path: Path) -> tuple[TunnelConfig, Relay, PublishRecord]:
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    launcher = tmp_path / "claude"
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {fake} "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "project").mkdir()
    token_file = tmp_path / "http-token"
    token_file.write_text("s3cret\n")
    cfg = TunnelConfig(
        state_path=tmp_path / "state" / "state.json",
        registry_path=tmp_path / "registry.json",
    )
    cfg.claude.binary = str(launcher)
    cfg.limits.per_user_cooldown_s = 0
    cfg.http.port = 8766
    cfg.http.token_file = str(token_file)
    registry = Registry(cfg.registry_path)
    rec = PublishRecord(
        handle="jev-expert", session_id="e" * 36, cwd=str(tmp_path / "project")
    )
    registry.upsert(rec)
    return cfg, Relay(cfg, TunnelStore(cfg.state_path), registry), rec


async def _client(cfg: TunnelConfig, relay: Relay) -> TestClient:
    client = TestClient(TestServer(make_app(cfg, relay)))
    await client.start_server()
    return client


def test_health_lists_handles(stack) -> None:
    cfg, relay, _rec = stack

    async def run() -> None:
        client = await _client(cfg, relay)
        try:
            res = await client.get("/health")
            assert res.status == 401  # every route needs the shared secret
            res = await client.get("/health", headers={"X-Ask-Token": "s3cret"})
            assert res.status == 200
            assert (await res.json()) == {"ok": True, "handles": ["jev-expert"]}
        finally:
            await client.close()

    asyncio.run(run())


def test_ask_rejects_bad_token_and_bad_bodies(stack) -> None:
    cfg, relay, _rec = stack
    asyncio.run(_bad_requests(cfg, relay))


async def _bad_requests(cfg: TunnelConfig, relay: Relay) -> None:
    client = await _client(cfg, relay)
    try:
        res = await client.post(
            "/ask", json={"handle": "jev-expert", "question": "q"}
        )
        assert res.status == 401
        hdr = {"X-Ask-Token": "s3cret"}
        res = await client.post("/ask", data="not json", headers=hdr)
        assert res.status == 400
        res = await client.post("/ask", json=[1, 2], headers=hdr)
        assert res.status == 400
        res = await client.post("/ask", json={"handle": "jev-expert"}, headers=hdr)
        assert res.status == 400
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": "q", "thread": "bad thread!"},
            headers=hdr,
        )
        assert res.status == 400
        res = await client.post(
            "/ask",
            json={"handle": "nobody", "question": "q", "thread": "t1"},
            headers=hdr,
        )
        assert res.status == 404
        assert (await res.json())["ran"] is False
        # Declared types only: a list is not a question, and over-limit is 413.
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": ["q"], "thread": "t1"},
            headers=hdr,
        )
        assert res.status == 400
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": "q" * 8001, "thread": "t1"},
            headers=hdr,
        )
        assert res.status == 413
    finally:
        await client.close()


def test_ask_answers_and_follow_up_keeps_the_fork(stack) -> None:
    cfg, relay, rec = stack
    asyncio.run(_answer_and_follow_up(cfg, relay, rec))


async def _answer_and_follow_up(
    cfg: TunnelConfig, relay: Relay, rec: PublishRecord
) -> None:
    client = await _client(cfg, relay)
    hdr = {"X-Ask-Token": "s3cret"}
    try:
        res = await client.post(
            "/ask",
            json={
                "handle": "jev-expert",
                "question": "what is a Noul?",
                "thread": "tab-1",
                "sender": "prasad@example.com",
            },
            headers=hdr,
        )
        assert res.status == 200
        body = await res.json()
        assert body["ran"] is True
        assert body["answer"].startswith("ANSWER to:")
        assert "prasad@example.com (via the web) says:" in body["answer"]
        first_fork = body["fork_session_id"]
        assert first_fork.startswith("fork-")
        # The thread is bound to the published session, under the http platform.
        stored = relay.store.get("http:jev-expert:tab-1")
        assert stored is not None
        assert stored.expert_session_id == rec.session_id
        assert stored.platform == "the web"

        res = await client.post(
            "/ask",
            json={
                "handle": "jev-expert",
                "question": "and a Choice?",
                "thread": "tab-1",
            },
            headers=hdr,
        )
        body = await res.json()
        assert body["ran"] is True
        # A follow-up resumes the same fork rather than forking again.
        assert body["fork_session_id"] == first_fork
        assert len(relay.store.get("http:jev-expert:tab-1").history) == 2

        # Revoking the handle keeps the existing thread alive, like a chat
        # thread, and only refuses new threads.
        relay.registry.revoke("jev-expert")
        res = await client.post(
            "/ask",
            json={
                "handle": "jev-expert",
                "question": "still there?",
                "thread": "tab-1",
            },
            headers=hdr,
        )
        assert res.status == 200
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": "new?", "thread": "tab-2"},
            headers=hdr,
        )
        assert res.status == 404
    finally:
        await client.close()


def test_ask_surfaces_backend_failure_as_502(stack) -> None:
    cfg, relay, _rec = stack
    Path(cfg.claude.binary).write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    asyncio.run(_failing_backend(cfg, relay))


async def _failing_backend(cfg: TunnelConfig, relay: Relay) -> None:
    client = await _client(cfg, relay)
    try:
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": "q", "thread": "t2"},
            headers={"X-Ask-Token": "s3cret"},
        )
        assert res.status == 502
        body = await res.json()
        assert body["ran"] is False
        assert body["error"]
    finally:
        await client.close()


def test_collect_dest_prefers_full_file_and_keeps_notices() -> None:
    dest = CollectDest()
    asyncio.run(dest.send(QUEUE_NOTICE))
    asyncio.run(dest.send("part one "))
    asyncio.run(dest.send("part two"))
    assert dest.text == "part one part two"
    assert dest.notices == [QUEUE_NOTICE]
    # Only the relay's exact notice is a notice; an answer that opens with the
    # same glyph is an answer.
    hourglass = CollectDest()
    asyncio.run(hourglass.send("⏳ this will take a while, here is why"))
    assert hourglass.text == "⏳ this will take a while, here is why"
    asyncio.run(dest.send_text_file("preview", "answer.md", b"the whole answer"))
    assert dest.text == "the whole answer"
    # Anything posted after the file (an oversized-deliverable warning) is a
    # notice, not lost.
    asyncio.run(dest.send("⚠️ 1 file too large to attach"))
    assert dest.notices[-1] == "⚠️ 1 file too large to attach"
    # An answer that happens to open with a warning glyph is still an answer.
    warned = CollectDest()
    asyncio.run(warned.send("⚠️ careful: this API is unstable"))
    assert warned.text == "⚠️ careful: this API is unstable"


def test_unexpected_relay_failure_is_a_json_502(stack, monkeypatch) -> None:
    cfg, relay, _rec = stack

    async def boom(*_args, **_kwargs):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(relay, "answer", boom)
    asyncio.run(_unexpected_failure(cfg, relay))


def test_failure_before_the_turn_is_a_json_502(stack, monkeypatch) -> None:
    cfg, relay, _rec = stack

    def broken_store(*_args, **_kwargs):
        raise OSError("state file unreadable")

    monkeypatch.setattr(relay.store, "get", broken_store)
    asyncio.run(_pre_turn_failure(cfg, relay))


async def _pre_turn_failure(cfg: TunnelConfig, relay: Relay) -> None:
    client = await _client(cfg, relay)
    try:
        res = await client.post(
            "/ask",
            json={"handle": "jev-expert", "question": "q", "thread": "t4"},
            headers={"X-Ask-Token": "s3cret"},
        )
        assert res.status == 502
        assert res.headers["Content-Type"].startswith("application/json")
        assert (await res.json())["ran"] is False
    finally:
        await client.close()


async def _unexpected_failure(cfg: TunnelConfig, relay: Relay) -> None:
    client = await _client(cfg, relay)
    try:
        res = await client.post(
            "/ask",
            json={"handle": "JEV-EXPERT", "question": "q", "thread": "t3"},
            headers={"X-Ask-Token": "s3cret"},
        )
        assert res.status == 502
        body = await res.json()
        assert body["ran"] is False
        assert "unexpected failure" in body["error"]
        # Handle casing is normalized: the thread is keyed by the lowercase form.
        assert relay.store.get("http:jev-expert:t3") is not None
    finally:
        await client.close()


def test_http_ready_and_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TUNNEL_DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_TUNNEL_MATTERMOST_TOKEN", raising=False)
    token_file = tmp_path / "http-token"
    token_file.write_text("x")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[http]\nport = 8766\n" f'token_file = "{token_file}"\n', encoding="utf-8"
    )
    cfg = load_config(path=cfg_path)
    assert cfg.http.port == 8766
    assert http_ready(cfg) is None
    # HTTP alone is a valid daemon: no chat front-end needed.
    assert plan_frontends(cfg) == ["http"]
    cfg.http.token_file = str(tmp_path / "missing")
    assert "No HTTP token" in (http_ready(cfg) or "")
    with pytest.raises(RuntimeError, match="No HTTP token"):
        plan_frontends(cfg)
    cfg.http.port = 0
    assert http_ready(cfg) == "No http.port configured"
    for bad in (-1, 65536, True):
        cfg.http.port = bad  # type: ignore[assignment] - deliberately wrong
        assert "1 to 65535" in (http_ready(cfg) or "")


def test_doctor_accepts_http_only(tmp_path: Path, monkeypatch) -> None:
    """An HTTP-only config passes doctor; Discord is not demanded."""
    from click.testing import CliRunner

    from claude_code_tools.agent_tunnel.cli import cli

    monkeypatch.delenv("AGENT_TUNNEL_DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_TUNNEL_MATTERMOST_TOKEN", raising=False)
    token_file = tmp_path / "http-token"
    token_file.write_text("x")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[http]\nport = 8766\n" f'token_file = "{token_file}"\n'
        f'[claude]\nbinary = "{sys.executable}"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "HTTP front-end" in result.output
    assert "Discord token" not in result.output

