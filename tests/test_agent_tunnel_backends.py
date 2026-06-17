"""Backend-selection tests for agent_tunnel.

Dispatching cleanup by a record's *own* backend (not the now-headless-by-
default config) so one-off management commands still kill tmux forks. Real
objects, no mocks — constructing a TmuxBackend only stores socket strings, it
never touches a live tmux server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_tools.agent_tunnel.backends import (
    Backend,
    BackendError,
    HeadlessBackend,
    TmuxBackend,
    _BaseBackend,
    backend_by_name,
    backend_for_record,
    build_claude_flags,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.paths import uploads_dir_for
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.store import ThreadRecord, TunnelStore


def test_backend_for_record_dispatches_by_record_backend(
    tmp_path: Path,
) -> None:
    # `forget` and `forks --manage` build a backend from the config, which now
    # defaults to headless. Cleanup must follow each record's OWN backend so a
    # tmux fork's window/process is actually killed — not just its JSON state.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    assert cfg.backend == "headless"  # the new default
    store = TunnelStore(cfg.state_path)
    cache: dict[str, Backend] = {}

    tmux_rec = ThreadRecord(thread_key="t", backend="tmux")
    head_rec = ThreadRecord(thread_key="h", backend="headless")
    # A tmux record gets the tmux backend despite the headless config default.
    assert isinstance(
        backend_for_record(cfg, store, tmux_rec, cache), TmuxBackend
    )
    assert isinstance(
        backend_for_record(cfg, store, head_rec, cache), HeadlessBackend
    )
    # No record / blank backend falls back to the config default (headless).
    assert isinstance(
        backend_for_record(cfg, store, None, cache), HeadlessBackend
    )
    blank = ThreadRecord(thread_key="b", backend="")
    assert isinstance(
        backend_for_record(cfg, store, blank, cache), HeadlessBackend
    )
    # Legacy record (pre-`backend` field) loads blank but owns a tmux_window:
    # treat as tmux so its pane is still reaped/cleaned after a headless flip.
    legacy = ThreadRecord(thread_key="L", backend="", tmux_window="agent:L")
    assert isinstance(
        backend_for_record(cfg, store, legacy, cache), TmuxBackend
    )
    # The cache reuses one instance per backend name.
    assert backend_for_record(cfg, store, tmux_rec, cache) is cache["tmux"]


def test_backend_by_name_builds_and_caches(tmp_path: Path) -> None:
    # The daemon reaper reaps per stored backend name via backend_by_name, so
    # a headless daemon still reaps leftover tmux windows from old records.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    cache: dict[str, Backend] = {}
    assert isinstance(backend_by_name(cfg, store, "tmux", cache), TmuxBackend)
    assert isinstance(
        backend_by_name(cfg, store, "headless", cache), HeadlessBackend
    )
    assert backend_by_name(cfg, store, "tmux", cache) is cache["tmux"]


def test_forget_removes_upload_dir(tmp_path: Path) -> None:
    # DM rebind now forgets the old binding, which must wipe its upload dir so
    # a new handle's fork can't Read the previous handle's files (Codex P2).
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    store.bind("dm:1", "a", "sid-a", "/p", "headless")
    uploads = uploads_dir_for(cfg.state_path.parent, "dm:1")
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "secret.txt").write_text("x", encoding="utf-8")

    backend_for_record(cfg, store, store.get("dm:1")).forget("dm:1")

    assert not uploads.exists()  # stale uploads gone
    assert store.get("dm:1") is None  # binding dropped


def test_store_backfills_legacy_blank_backend(tmp_path: Path) -> None:
    # Root fix: a record written before the `backend` field loads blank, but a
    # live tmux_window means its fork runs under tmux. Normalizing on load (one
    # place) means dispatch/reaper/rename/forget all read a correct backend.
    import json

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "records": {
                    "th:1": {
                        "thread_key": "th:1",
                        "handle": "h",
                        "backend": "",
                        "tmux_window": "agent:th-1",
                    }
                },
                "fork_ids": [],
            }
        ),
        encoding="utf-8",
    )
    rec = TunnelStore(path).get("th:1")
    assert rec is not None and rec.backend == "tmux"


def test_persona_platform_substituted_in_flags(tmp_path: Path) -> None:
    # The {platform} placeholder in the persona is filled from cfg.platform, so
    # the fork's system prompt names the actual chat tool (Discord/Slack/...).
    from claude_code_tools.agent_tunnel.backends import build_claude_flags

    cfg = TunnelConfig(state_path=tmp_path / "s.json", platform="Slack")
    flags = build_claude_flags(cfg, "sid", fork=True)
    system = flags[flags.index("--append-system-prompt") + 1]
    assert "via Slack" in system and "{platform}" not in system


def test_all_access_emits_skip_permissions_only_when_gated(
    tmp_path: Path,
) -> None:
    # The "all" level grants --dangerously-skip-permissions, but ONLY when the
    # owner has flipped the config gate. Ungated, it must never emit the flag.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    cfg.claude.allow_skip_permissions = True
    on = build_claude_flags(cfg, "sid", fork=True, access="all")
    assert "--dangerously-skip-permissions" in on
    assert "--allowedTools" not in on and "--permission-mode" not in on

    cfg.claude.allow_skip_permissions = False
    off = build_claude_flags(cfg, "sid", fork=True, access="all")
    assert "--dangerously-skip-permissions" not in off
    assert "--allowedTools" in off  # falls back to the restrictive read preset


def test_all_access_refused_without_gate(tmp_path: Path) -> None:
    # A handle shared as "all" can't run until the owner enables the gate;
    # the backend refuses with a clear, actionable error.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    store.bind("t", "h", "expsid", "/p", "headless", access="all")
    backend = HeadlessBackend(cfg, store)
    with pytest.raises(BackendError, match="allow_skip_permissions"):
        backend._require_binding("t")
    cfg.claude.allow_skip_permissions = True
    assert backend._require_binding("t").access == "all"


def test_all_access_can_write_for_outbox(tmp_path: Path) -> None:
    # "all" is above bash, so it must be in the write/outbox path — else its
    # generated deliverables never get posted back (Codex P2).
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    backend = HeadlessBackend(cfg, TunnelStore(cfg.state_path))
    assert backend._can_write(ThreadRecord(thread_key="t", access="all"))
    assert backend._can_write(ThreadRecord(thread_key="t", access="bash"))
    assert not backend._can_write(ThreadRecord(thread_key="t", access="read"))


def test_require_binding_syncs_access_up_from_registry(tmp_path: Path) -> None:
    # A live `>share --write <handle>` on an already-bound (read) thread must
    # upgrade it on its next turn: _require_binding re-reads the handle's
    # current registry access and persists it onto the stored record, so the
    # turn's build_claude_flags(access=…) and follow-ups see the new level.
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("t", "payments", "expsid", "/p", "headless", access="read")
    Registry(cfg.registry_path).upsert(
        PublishRecord(
            handle="payments", session_id="expsid", cwd="/p", access="write"
        )
    )
    rec = HeadlessBackend(cfg, store)._require_binding("t")
    assert rec.access == "write"  # synced in-memory for this turn's flags
    persisted = store.get("t")
    assert persisted is not None and persisted.access == "write"


def test_require_binding_syncs_access_down_from_registry(
    tmp_path: Path,
) -> None:
    # A `>share --read <handle>` re-share downgrades a live write thread too,
    # so an owner can pull back access mid-conversation.
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("t", "payments", "expsid", "/p", "headless", access="write")
    Registry(cfg.registry_path).upsert(
        PublishRecord(
            handle="payments", session_id="expsid", cwd="/p", access="read"
        )
    )
    rec = HeadlessBackend(cfg, store)._require_binding("t")
    assert rec.access == "read"
    persisted = store.get("t")
    assert persisted is not None and persisted.access == "read"


def test_live_upgrade_to_all_respects_gate(tmp_path: Path) -> None:
    # Upgrading a live thread to "all" (>share --dangerously-skip-permissions)
    # still honors the gate: with allow_skip_permissions off the turn is
    # refused (no silent grant) even though the level synced; once the owner
    # enables the gate the same thread runs.
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("t", "ops", "expsid", "/p", "headless", access="write")
    Registry(cfg.registry_path).upsert(
        PublishRecord(
            handle="ops", session_id="expsid", cwd="/p", access="all"
        )
    )
    backend = HeadlessBackend(cfg, store)
    with pytest.raises(BackendError, match="allow_skip_permissions"):
        backend._require_binding("t")
    persisted = store.get("t")  # synced to "all" before the gate refusal
    assert persisted is not None and persisted.access == "all"
    cfg.claude.allow_skip_permissions = True
    assert backend._require_binding("t").access == "all"  # now allowed


def test_current_access_falls_back_when_handle_revoked(tmp_path: Path) -> None:
    # Registry.get hides a revoked/absent handle, so a revoked thread keeps
    # working at its bound level instead of breaking mid-conversation (revoke
    # semantics are intentionally out of scope for the live-access sync).
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("t", "gone", "expsid", "/p", "headless", access="write")
    reg = Registry(cfg.registry_path)
    reg.upsert(
        PublishRecord(
            handle="gone", session_id="expsid", cwd="/p", access="write"
        )
    )
    reg.revoke("gone")
    rec = HeadlessBackend(cfg, store)._require_binding("t")
    assert rec.access == "write"  # bound level retained despite revoke


def test_access_sync_ignores_handle_collision_from_other_session(
    tmp_path: Path,
) -> None:
    # Guard (Codex P2): sync access only from the registry record bound to THIS
    # thread's session. The CLI direct path binds threads under the sentinel
    # handle "cli"; an unrelated `>share --write cli` (a different session) must
    # NOT escalate them — match on session_id, else keep the stored level.
    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind("t", "cli", "session-A", "/p", "headless", access="read")
    Registry(cfg.registry_path).upsert(
        PublishRecord(
            handle="cli", session_id="session-B", cwd="/other", access="write"
        )
    )
    rec = HeadlessBackend(cfg, store)._require_binding("t")
    assert rec.access == "read"  # not hijacked by a same-name other session
    persisted = store.get("t")
    assert persisted is not None and persisted.access == "read"


def test_store_set_access_persists_and_noops_on_missing(
    tmp_path: Path,
) -> None:
    # set_access is the persistence primitive behind the live upgrade: it
    # writes through to disk for a bound thread and is a clean no-op (returns
    # None) for an unknown thread key.
    store = TunnelStore(tmp_path / "s.json")
    store.bind("t", "h", "expsid", "/p", "headless", access="read")
    updated = store.set_access("t", "bash")
    assert updated is not None and updated.access == "bash"
    persisted = store.get("t")  # persisted across a fresh read
    assert persisted is not None and persisted.access == "bash"
    assert store.set_access("missing", "write") is None  # unknown key: no-op


def test_tmux_backend_overrides_access_changed_hook() -> None:
    # The live-upgrade window-kill is the tmux backend's job; headless uses the
    # base no-op (its next `claude -p` re-reads the access). Asserting the
    # override is wired keeps the actual kill_window behavior — exercised in the
    # live/manual tmux tier — from being silently dropped. No mock, no live tmux.
    assert TmuxBackend._on_access_changed is not _BaseBackend._on_access_changed
    assert HeadlessBackend._on_access_changed is _BaseBackend._on_access_changed
