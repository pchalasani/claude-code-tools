"""Behavior tests for the platform-neutral ChatCore (routing + pipeline).

The existing ``test_discord_bot.py`` / ``test_agent_tunnel.py`` only cover the
pure helpers; these drive the EXTRACTED routing/answer pipeline end-to-end
through a real in-memory ``FakeTransport`` (records every chat I/O, opens
deterministic thread dests, no network) and a real ``FakeBackend`` (a canned
``Answer``, captured questions) injected by redirecting the
``chat_core.backend_for_record`` lookup. Real ``TunnelConfig``/``TunnelStore``/
``Registry`` pinned to ``tmp_path`` so nothing touches a live daemon's state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from claude_code_tools.agent_tunnel import chat_core
from claude_code_tools.agent_tunnel.backends import (
    Answer,
    BackendError,
    HeadlessBackend,
)
from claude_code_tools.agent_tunnel.chat_core import ChatCore
from claude_code_tools.agent_tunnel.chat_types import (
    Addressee,
    AttachmentRef,
    ChatDest,
    IncomingMessage,
    OutgoingFile,
    Surface,
    TransportLimits,
    noop_activity,
    split_chunks,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.store import TunnelStore


class FakeTransport:
    """In-memory ChatTransport: records calls, mints deterministic threads."""

    def __init__(self, limits: TransportLimits | None = None) -> None:
        self._limits = limits or TransportLimits()
        self.texts: list[tuple[str, str]] = []
        self.files: list[tuple[str, str, list[OutgoingFile]]] = []
        self.reactions: list[tuple[str, str]] = []
        self.opened: list[tuple[str, str]] = []
        self.downloads: list[tuple[AttachmentRef, Path]] = []
        self.fail_download = False
        self.fail_send_files = False
        self._seq = 0

    @property
    def limits(self) -> TransportLimits:
        return self._limits

    async def open_thread(self, parent: ChatDest, title: str) -> ChatDest:
        self._seq += 1
        self.opened.append((parent.thread_key, title))
        return ChatDest(
            thread_key=f"th:fake{self._seq}",
            surface=Surface.THREAD,
            channel_id=parent.channel_id,
        )

    async def send_text(self, dest: ChatDest, text: str) -> None:
        self.texts.append((dest.thread_key, text))

    async def send_files(self, dest, caption, files) -> None:
        if self.fail_send_files:
            raise RuntimeError("send boom")
        self.files.append((dest.thread_key, caption, list(files)))

    async def download_attachment(
        self, attachment: AttachmentRef, target: Path
    ) -> None:
        self.downloads.append((attachment, target))
        if self.fail_download:
            raise RuntimeError("download boom")
        target.write_bytes(b"FAKE")

    async def add_reaction(self, message: IncomingMessage, emoji: str) -> None:
        self.reactions.append((message.author_id, emoji))

    def activity(self, dest: ChatDest):
        return noop_activity()


class FakeBackend:
    """Real Backend implementation with a canned Answer + captured questions."""

    def __init__(self, answer=None, error=None, store=None):
        self.questions: list[tuple[str, str]] = []
        self.forgotten: list[str] = []
        self._answer = answer
        self._error = error
        self._store = store

    def ask(self, thread_key: str, question: str) -> Answer:
        self.questions.append((thread_key, question))
        if self._error is not None:
            raise self._error
        return self._answer or Answer(
            text="ok", fork_session_id="fork1234", new_thread=True
        )

    def forget(self, thread_key: str) -> None:
        # The real backend.forget removes the store binding (+ cleans dirs); mirror
        # the removal so DM rebind's forget-then-bind isn't a no-op.
        self.forgotten.append(thread_key)
        if self._store is not None:
            self._store.remove(thread_key)

    def reap_idle(self) -> int:
        return 0


def _core(tmp_path: Path):
    """A ChatCore wired to a FakeTransport + tmp-pinned store/registry."""
    cfg = TunnelConfig(
        state_path=tmp_path / "state.json",
        registry_path=tmp_path / "registry.json",
    )
    store = TunnelStore(cfg.state_path)
    registry = Registry(cfg.registry_path)
    tx = FakeTransport()
    return ChatCore(cfg, store, registry, tx), tx, store, registry, cfg


def _msg(
    surface: Surface = Surface.CHANNEL,
    text: str = "",
    thread_key: str = "",
    channel_id: str = "C1",
    author_id: str = "U1",
    author_display: str = "Alice",
    author_role_ids=frozenset(),
    addressee: Addressee = Addressee.NONE,
    attachments=(),
) -> IncomingMessage:
    dest = ChatDest(
        thread_key=thread_key, surface=surface, channel_id=channel_id
    )
    return IncomingMessage(
        text=text,
        dest=dest,
        surface=surface,
        author_id=author_id,
        author_display=author_display,
        author_role_ids=author_role_ids,
        addressee=addressee,
        attachments=tuple(attachments),
        channel_id=channel_id,
    )


def _use_backend(monkeypatch, fake: FakeBackend) -> None:
    monkeypatch.setattr(
        chat_core, "backend_for_record", lambda *a, **k: fake
    )


def _seed(registry: Registry, tmp_path: Path, handle="pay", access="read") -> None:
    registry.upsert(
        PublishRecord(
            handle=handle, session_id="S1", cwd=str(tmp_path), access=access
        )
    )


# -- helpers / policy ---------------------------------------------------------


def test_split_chunks_default_and_limit() -> None:
    assert split_chunks("") == []
    assert split_chunks("hello") == ["hello"]
    chunks = split_chunks("x" * 130, 50)
    assert all(len(c) <= 50 for c in chunks) and "".join(chunks) == "x" * 130


def test_allowed_open_user_role_and_denied(tmp_path: Path) -> None:
    core, _, _, _, cfg = _core(tmp_path)
    # empty allowlists -> anyone
    assert core._allowed(_msg(author_id="U1")) is True
    # int Discord ids are stringified -> "123" matches author_id="123"
    cfg.discord.allowed_user_ids = [123]
    assert core._allowed(_msg(author_id="123")) is True
    assert core._allowed(_msg(author_id="999")) is False
    # role/usergroup match
    cfg.discord.allowed_user_ids = []
    cfg.discord.allowed_role_ids = [9]
    assert core._allowed(_msg(author_role_ids=frozenset({"9"}))) is True
    assert core._allowed(_msg(author_role_ids=frozenset({"8"}))) is False


def test_cooldown_blocks_allows_after_and_per_principal(
    tmp_path: Path, monkeypatch
) -> None:
    core, *_ = _core(tmp_path)
    core.cfg.limits.per_user_cooldown_s = 100
    clock = {"t": 1000.0}
    monkeypatch.setattr(chat_core.time, "time", lambda: clock["t"])
    assert core._cooldown_ok("U1") is True
    assert core._cooldown_ok("U1") is False  # within cooldown
    assert core._cooldown_ok("U2") is True   # different principal
    clock["t"] = 1101.0
    assert core._cooldown_ok("U1") is True   # cooldown elapsed


def test_deliver_answer_inline_chunked_and_as_file(tmp_path: Path) -> None:
    core, tx, *_ = _core(tmp_path)
    tx._limits = TransportLimits(max_message_len=50)
    core.cfg.limits.max_inline_chars = 80
    asyncio.run(core._deliver_answer(_dest("th:1"), "y" * 70))  # <=80 -> inline
    assert tx.texts and all(len(t) <= 50 for _, t in tx.texts)  # chunked to 50
    assert "".join(t for _, t in tx.texts) == "y" * 70
    tx.texts.clear()
    long = "z" * 200  # > max_inline_chars -> one file + preview caption
    asyncio.run(core._deliver_answer(_dest("th:1"), long))
    assert not tx.texts and len(tx.files) == 1
    _, caption, files = tx.files[0]
    assert len(files) == 1 and files[0].filename == "answer.md"
    assert files[0].data is not None and files[0].data.decode() == long
    assert caption == long[:50]


def _dest(key: str, surface: Surface = Surface.THREAD) -> ChatDest:
    return ChatDest(thread_key=key, surface=surface, channel_id="C1")


def test_thread_key_scheme_is_opaque(tmp_path: Path, monkeypatch) -> None:
    # Discord th:NNN and a Slack-style th:C-ts both round-trip through the store.
    core, tx, store, registry, _ = _core(tmp_path)
    core.cfg.limits.per_user_cooldown_s = 0  # both turns by the same author
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    for key in ("th:fakeA", "th:C123-1700000000.0001"):
        store.bind(key, "pay", "S1", str(tmp_path), "headless")
        asyncio.run(core._on_thread(_msg(Surface.THREAD, "hi", thread_key=key)))
    assert {k for k, _ in fake.questions} == {"th:fakeA", "th:C123-1700000000.0001"}


def test_thread_addressee_other_silent_self_and_none_answer(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    core.cfg.limits.per_user_cooldown_s = 0  # SELF + NONE turns by one author
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "x", thread_key="th:1",
                             addressee=Addressee.OTHER))
    )
    assert not fake.questions  # addressed to someone else -> silent
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "real q", thread_key="th:1",
                             addressee=Addressee.SELF))
    )
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "another", thread_key="th:1",
                             addressee=Addressee.NONE))
    )
    assert len(fake.questions) == 2  # SELF + NONE both answer


# -- routing: channel open ----------------------------------------------------


def test_channel_open_binds_answers_and_relay_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, cfg = _core(tmp_path)
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    _seed(registry, tmp_path)
    asyncio.run(
        core.handle_message(_msg(text="pay how does refresh work?"))
    )
    assert tx.opened == [("", "pay: how does refresh work?")]
    assert store.get("th:fake1") is not None
    # answer delivered on the new thread; relay prefix names the sender+platform
    assert any(k == "th:fake1" for k, _ in tx.texts)
    assert fake.questions[0][1].startswith("Alice (via Discord) says:")


def test_channel_handle_only_posts_ready_notice(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    _seed(registry, tmp_path)
    asyncio.run(core.handle_message(_msg(text="pay")))
    assert not fake.questions  # no question -> no ask
    assert any("Connected to **pay**" in t for _, t in tx.texts)


def test_unknown_handle_complains_before_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, cfg = _core(tmp_path)
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    cfg.discord.allowed_user_ids = [999]  # would DENY author U1 (id "1")
    asyncio.run(core.handle_message(_msg(text="nope", author_id="1")))
    assert any("No live session for handle `nope`" in t for _, t in tx.texts)
    tx.texts.clear()
    # but a handle-looking token WITH a question stays silent
    asyncio.run(core.handle_message(_msg(text="nope please help", author_id="1")))
    assert not tx.texts


# -- routing: thread follow-up ------------------------------------------------


def test_thread_followup_answers_then_cooldown_reacts(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, cfg = _core(tmp_path)
    cfg.limits.per_user_cooldown_s = 100
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "q1", thread_key="th:1")))
    assert len(fake.questions) == 1
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "q2", thread_key="th:1")))
    assert len(fake.questions) == 1  # cooled down -> no second ask
    assert tx.reactions == [("U1", "hourglass")]


# -- pipeline: attachments + deliverables -------------------------------------


def test_attachments_ingested_and_oversize_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    core, tx, store, registry, cfg = _core(tmp_path)
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    big = int(cfg.limits.max_attachment_mb * 1024 * 1024) + 1
    atts = (AttachmentRef("a.txt", 4), AttachmentRef("huge.bin", big))
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "see file", thread_key="th:1",
                             attachments=atts))
    )
    assert [a.filename for a, _ in tx.downloads] == ["a.txt"]  # huge not fetched
    assert "a.txt" in fake.questions[0][1]  # preamble path handed to the fork
    assert any("⚠️ Skipped:" in t and "huge.bin" in t for _, t in tx.texts)


def test_attachment_download_failure_skipped(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    tx.fail_download = True
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "q", thread_key="th:1",
                             attachments=(AttachmentRef("a.txt", 4),)))
    )
    assert any("⚠️ Skipped:" in t and "a.txt" in t for _, t in tx.texts)


def test_attachment_only_empty_after_ingest(tmp_path: Path, monkeypatch) -> None:
    # text="" + a file whose ingest yields no usable content path still ingests;
    # if the question is empty after ingest with no saved files -> "nothing".
    core, tx, store, registry, _ = _core(tmp_path)
    tx.fail_download = True  # the lone file is skipped -> nothing usable
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(
        core._on_thread(_msg(Surface.THREAD, "", thread_key="th:1",
                             attachments=(AttachmentRef("a.txt", 4),)))
    )
    assert not fake.questions
    assert any("Nothing to act on" in t for _, t in tx.texts)


def test_deliverables_posted_in_batches(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    tx._limits = TransportLimits(max_attachments_per_msg=10)
    outs = []
    for i in range(11):
        p = tmp_path / f"out{i}.txt"
        p.write_text("x")
        outs.append(p)
    fake = FakeBackend(
        Answer(text="done", fork_session_id="f1", new_thread=False, attachments=outs)
    )
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "make files", thread_key="th:1")))
    captions = [c for _, c, _ in tx.files]
    assert "📎 Deliverable(s) from the agent:" in captions
    assert "📎 More deliverables:" in captions  # 11 files -> two batches


def test_deliverables_send_failure_swallowed(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    tx.fail_send_files = True
    p = tmp_path / "out.txt"
    p.write_text("x")
    fake = FakeBackend(
        Answer(text="done", fork_session_id="f1", new_thread=False, attachments=[p])
    )
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    # must not raise even though send_files blows up
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "q", thread_key="th:1")))


# -- pipeline: error paths + stale binding ------------------------------------


def test_backend_error_truncated(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    fake = FakeBackend(error=BackendError("x" * 5000))
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "q", thread_key="th:1")))
    warn = [t for _, t in tx.texts if t.startswith("⚠️ ")][0]
    # "⚠️ " prefix + the error sliced to max_error_len
    assert len(warn) == len("⚠️ ") + core.tx.limits.max_error_len
    assert warn.endswith("x" * 10)


def test_unexpected_error_releases_lock(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    fake = FakeBackend(error=ValueError("kaboom"))
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")
    asyncio.run(core._on_thread(_msg(Surface.THREAD, "q", thread_key="th:1")))
    assert any("Unexpected error" in t for _, t in tx.texts)
    assert not core._locks["th:1"].locked()  # lock released for the next turn


def test_stale_binding_guard(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    fake = FakeBackend()
    _use_backend(monkeypatch, fake)
    store.bind("th:1", "pay", "S1", str(tmp_path), "headless")

    # Force _answer to read rec=S1, then park on the per-thread lock; rebind to a
    # different session before releasing -> the in-lock re-read sees S2, so the
    # queued question is refused instead of answered against the wrong binding.
    async def drive():
        lock = core._locks["th:1"]
        await lock.acquire()
        msg = _msg(Surface.THREAD, "q", thread_key="th:1")
        task = asyncio.create_task(core._answer(msg.dest, "q", msg))
        await asyncio.sleep(0)  # let _answer read S1 and block on the lock
        store.remove("th:1")
        store.bind("th:1", "pay", "S2", str(tmp_path), "headless")
        lock.release()
        await task

    asyncio.run(drive())
    assert not fake.questions
    assert any("was restarted" in t for _, t in tx.texts)


# -- DM rebind ----------------------------------------------------------------


def _dm_msg(text: str) -> IncomingMessage:
    return _msg(
        text=text, surface=Surface.DM, thread_key="dm:D1", channel_id="D1"
    )


def test_dm_rebind_then_followup(tmp_path: Path, monkeypatch) -> None:
    core, tx, store, registry, _ = _core(tmp_path)
    core.cfg.limits.per_user_cooldown_s = 0
    fake = FakeBackend(store=store)  # forget removes the binding (real behavior)
    _use_backend(monkeypatch, fake)
    registry.upsert(PublishRecord(handle="pay", session_id="S1", cwd=str(tmp_path)))
    registry.upsert(PublishRecord(handle="ops", session_id="S2", cwd=str(tmp_path)))
    asyncio.run(core.handle_message(_dm_msg("pay first q")))
    bound = store.get("dm:D1")
    assert bound is not None and bound.expert_session_id == "S1"
    asyncio.run(core.handle_message(_dm_msg("more")))  # bare follow-up
    asyncio.run(core.handle_message(_dm_msg("ops")))   # rebind to a new session
    assert "dm:D1" in fake.forgotten  # old binding torn down on rebind
    rebound = store.get("dm:D1")
    assert rebound is not None and rebound.expert_session_id == "S2"


# -- cross-feature: live-access collision guard (Slack dm: key) ---------------


def test_access_collision_slack_dm_key(tmp_path: Path) -> None:
    # A Slack DM bound under the sentinel handle "cli" (session A) must NOT be
    # escalated by an unrelated >share cli for a different session — the guard
    # keys on session_id == expert_session_id (mirrors the backends test).
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("dm:C9", "cli", "session-A", "/p", "headless", access="read")
    Registry(cfg.registry_path).upsert(
        PublishRecord(
            handle="cli", session_id="session-B", cwd="/p", access="write"
        )
    )
    rec = HeadlessBackend(cfg, store)._require_binding("dm:C9")
    assert rec.access == "read"
