"""Mattermost front-end: event parsing, routing, serve planning, relay turn.

Routing and parsing are pure functions, tested directly. The relay turn runs
the real Relay + HeadlessBackend against a stand-in ``claude`` script and an
in-memory Destination, so the shared path both platforms use is exercised
end to end without a chat server.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Sequence

import pytest

from claude_code_tools.agent_tunnel.config import TunnelConfig, load_config
from claude_code_tools.agent_tunnel.mattermost_bot import (
    MMFile,
    MMPost,
    leading_mention,
    mattermost_ready,
    parse_posted,
    route_post,
)
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.relay import Relay
from claude_code_tools.agent_tunnel.serve import plan_frontends
from claude_code_tools.agent_tunnel.store import TunnelStore

CHAN = "c" * 26
BOT = "b" * 26
USER = "u" * 26


def _event(post: dict, channel_type: str = "O", sender: str = "@alice") -> dict:
    return {
        "event": "posted",
        "data": {
            "post": json.dumps(post),
            "channel_type": channel_type,
            "sender_name": sender,
        },
    }


def test_parse_posted_basic_and_files() -> None:
    post = parse_posted(
        _event(
            {
                "id": "p1",
                "channel_id": CHAN,
                "user_id": USER,
                "root_id": "",
                "message": " cyberins what's new? ",
                "file_ids": ["f1", "f2"],
                "metadata": {"files": [{"id": "f1", "name": "a.pdf", "size": 9}]},
            }
        )
    )
    assert post is not None
    assert post.message == "cyberins what's new?"
    assert post.sender == "alice"
    assert not post.direct and not post.system and not post.from_bot
    assert [(f.id, f.name, f.size) for f in post.files] == [
        ("f1", "a.pdf", 9),
        ("f2", "file", 0),
    ]


def test_parse_posted_flags_and_junk() -> None:
    assert parse_posted({"event": "typing"}) is None
    assert parse_posted({"event": "posted", "data": {"post": "not json"}}) is None
    bot = parse_posted(
        _event({"id": "p", "props": {"from_bot": "true"}, "type": ""})
    )
    assert bot is not None and bot.from_bot
    sys_post = parse_posted(_event({"id": "p", "type": "system_join_channel"}))
    assert sys_post is not None and sys_post.system
    dm = parse_posted(_event({"id": "p"}, channel_type="D"))
    assert dm is not None and dm.direct


def test_leading_mention() -> None:
    assert leading_mention("@tunnelbot hi") == "tunnelbot"
    assert leading_mention("  @Bob.Smith, look") == "bob.smith"
    assert leading_mention("hello @bob") is None


def _route(post: MMPost, bound: frozenset[str] | set[str] = frozenset(),
           live=("cyberins",),
           allowed: Sequence[str] = ()):
    return route_post(
        post,
        bot_user_id=BOT,
        bot_username="tunnelbot",
        channel_ids=[CHAN],
        allowed_user_ids=allowed,
        is_bound=lambda k: k in bound,
        handle_live=lambda h: h in live,
    )


def _post(message: str, root: str = "", **kw) -> MMPost:
    base = dict(id="p1", channel_id=CHAN, user_id=USER, root_id=root,
                message=message)
    base.update(kw)
    return MMPost(**base)


def test_route_open_list_unknown() -> None:
    r = _route(_post("cyberins what did you finish?"))
    assert (r.action, r.handle, r.text) == ("open", "cyberins", "what did you finish?")
    assert r.thread_key == "mm:p1" and r.root_id == "p1"
    assert _route(_post("cyberins")).action == "open"  # ready notice
    assert _route(_post("!list")).action == "list"
    unknown = _route(_post("nosuch"))
    assert unknown.action == "unknown_handle" and unknown.handle == "nosuch"
    # Ordinary chatter in the channel is left alone.
    assert _route(_post("lunch anyone?")).action == "ignore"


def test_route_ignores_self_bots_system_dm_other_channels() -> None:
    assert _route(_post("cyberins q", user_id=BOT)).action == "ignore"
    assert _route(_post("cyberins q", from_bot=True)).action == "ignore"
    assert _route(_post("cyberins q", system=True)).action == "ignore"
    assert _route(_post("cyberins q", direct=True)).action == "ignore"
    assert _route(_post("cyberins q", channel_id="x" * 26)).action == "ignore"
    assert _route(_post("")).action == "ignore"


def test_route_thread_followups() -> None:
    bound = {"mm:root1"}
    r = _route(_post("and then?", root="root1"), bound)
    assert (r.action, r.thread_key, r.root_id, r.text) == (
        "followup", "mm:root1", "root1", "and then?"
    )
    # Unbound thread: not ours.
    assert _route(_post("and then?", root="other"), bound).action == "ignore"
    # Side-chat among teammates is ignored; @bot is stripped.
    assert _route(_post("@bob see above", root="root1"), bound).action == "ignore"
    assert _route(_post("@here look", root="root1"), bound).action == "ignore"
    r = _route(_post("@tunnelbot why?", root="root1"), bound)
    assert (r.action, r.text) == ("followup", "why?")
    assert _route(_post("!done", root="root1"), bound).action == "close"
    assert _route(_post("!list", root="root1"), bound).action == "list"
    # Attachment-only follow-up still counts.
    att = _post("", root="root1", files=[MMFile("f", "a.txt")])
    assert _route(att, bound).action == "followup"


def test_route_allowlist() -> None:
    assert _route(_post("cyberins q"), allowed=["someone-else"]).action == "ignore"
    assert _route(_post("cyberins q"), allowed=[USER]).action == "open"


def test_config_and_readiness(tmp_path: Path, monkeypatch) -> None:
    tok = tmp_path / "mm.txt"
    tok.write_text("secret\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f"""
[mattermost]
url = "http://localhost:8065"
token_file = "{tok}"
channel_ids = ["{CHAN}"]
allowed_user_ids = ["{USER}"]
verify_tls = false
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENT_TUNNEL_MATTERMOST_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_TUNNEL_DISCORD_TOKEN", raising=False)
    cfg = load_config(cfg_file)
    assert cfg.mattermost.channel_ids == [CHAN]
    assert cfg.mattermost.verify_tls is False
    assert mattermost_ready(cfg) is None
    # No Discord token: serve runs Mattermost alone.
    assert plan_frontends(cfg) == ["mattermost"]

    cfg.mattermost.channel_ids = []
    assert "channel_ids" in (mattermost_ready(cfg) or "")
    with pytest.raises(RuntimeError):
        plan_frontends(cfg)


def test_plan_frontends_both_and_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TUNNEL_DISCORD_TOKEN", "d")
    monkeypatch.setenv("AGENT_TUNNEL_MATTERMOST_TOKEN", "m")
    cfg = TunnelConfig()
    cfg.discord.channel_ids = [1]
    assert plan_frontends(cfg) == ["discord"]
    cfg.mattermost.url = "http://x"
    cfg.mattermost.channel_ids = [CHAN]
    assert plan_frontends(cfg) == ["discord", "mattermost"]
    # Mattermost configured but its token not issued yet: Discord keeps
    # running rather than serve refusing to start.
    monkeypatch.delenv("AGENT_TUNNEL_MATTERMOST_TOKEN")
    assert plan_frontends(cfg) == ["discord"]
    # ...but with nothing else able to run, the Mattermost problem is fatal.
    monkeypatch.delenv("AGENT_TUNNEL_DISCORD_TOKEN")
    with pytest.raises(RuntimeError, match="Mattermost token"):
        plan_frontends(cfg)
    cfg.mattermost.url = ""
    with pytest.raises(RuntimeError, match="Discord token"):
        plan_frontends(cfg)


# ------------------------------------------------------- shared relay turn

FAKE_CLAUDE = """\
import json, sys, uuid
argv = sys.argv[1:]
prompt = sys.stdin.read()
sid = str(uuid.uuid4())
system = argv[argv.index("--append-system-prompt") + 1]
print(json.dumps({"result": "LINE\\n" * 30 + "ECHO:" + prompt
                  + "\\nSYSTEM-MENTIONS-MATTERMOST:" + str("Mattermost" in system),
                  "session_id": sid}))
"""


class FakeDest:
    """In-memory Destination recording what the relay posts."""

    max_len = 100
    max_files = 2

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.files: list[tuple[str, str, bytes]] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def send_text_file(self, preview: str, name: str, data: bytes) -> None:
        self.files.append((preview, name, data))

    async def send_files(self, caption: str, paths) -> None:
        self.sent.append(caption)

    @asynccontextmanager
    async def typing(self) -> AsyncIterator[None]:
        yield


class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.filename = name
        self.size = len(data)
        self._data = data

    async def save(self, path: str) -> None:
        Path(path).write_bytes(self._data)


@pytest.fixture
def relay(tmp_path: Path) -> Relay:
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    launcher = tmp_path / "claude"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    project = tmp_path / "project"
    project.mkdir()
    cfg = TunnelConfig(
        state_path=tmp_path / "state" / "state.json",
        registry_path=tmp_path / "registry.json",
    )
    cfg.claude.binary = str(launcher)
    cfg.limits.per_user_cooldown_s = 0
    registry = Registry(cfg.registry_path)
    return Relay(cfg, TunnelStore(cfg.state_path), registry), PublishRecord(
        handle="cyberins", session_id="s" * 36, cwd=str(project)
    )


def test_relay_turn_chunks_and_names_platform(relay) -> None:
    rly, rec = relay
    rly.bind("mm:root1", rec, "alice", "Mattermost")
    dest = FakeDest()
    uploads = [FakeUpload("notes.txt", b"hello file")]
    asyncio.run(
        rly.answer(dest, "mm:root1", "what's new?", uploads, sender="alice")
    )
    # Chunks may split mid-line, so rejoin without adding separators.
    text = "".join(dest.sent)
    assert all(len(chunk) <= FakeDest.max_len for chunk in dest.sent)
    assert len(dest.sent) > 1  # long answer was chunked to the platform limit
    # The fork was told who asked, on which platform, and where the file is.
    assert "alice (via Mattermost) says:" in text
    assert "notes.txt" in text
    assert "SYSTEM-MENTIONS-MATTERMOST:True" in text
    assert rly.store.get("mm:root1").history  # turn recorded for recaps


def test_relay_long_answer_becomes_file(relay) -> None:
    rly, rec = relay
    rly.cfg.limits.max_inline_chars = 50
    rly.bind("mm:root2", rec, "bob", "Mattermost")
    dest = FakeDest()
    asyncio.run(rly.answer(dest, "mm:root2", "q", sender="bob"))
    ((preview, name, data),) = dest.files
    assert name == "answer.md" and len(preview) <= FakeDest.max_len
    assert b"ECHO:" in data


def test_relay_close_forgets_thread(relay) -> None:
    rly, rec = relay
    rly.bind("mm:root3", rec, "bob", "Mattermost")
    dest = FakeDest()
    asyncio.run(rly.close(dest, "mm:root3"))
    assert rly.store.get("mm:root3") is None
    assert "Closed" in dest.sent[0]


def test_relay_preview_is_short_on_large_limit_platforms(relay) -> None:
    rly, rec = relay
    rly.cfg.limits.max_inline_chars = 50
    rly.bind("mm:root4", rec, "bob", "Mattermost")
    dest = FakeDest()
    dest.max_len = 16000  # Mattermost-sized limit
    asyncio.run(rly.answer(dest, "mm:root4", "q" * 4000, sender="bob"))
    ((preview, _, data),) = dest.files
    assert len(preview) <= 1500 and len(preview) < len(data)


def test_parse_posted_rejects_non_dict_payloads() -> None:
    assert parse_posted({"event": "posted", "data": {"post": "[]"}}) is None
    assert parse_posted({"event": "posted", "data": "x"}) is None
    assert parse_posted(["not", "a", "dict"]) is None  # type: ignore[arg-type]
