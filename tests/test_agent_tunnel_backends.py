"""Backend-selection tests for agent_tunnel.

Dispatching cleanup by a record's *own* backend (not the now-headless-by-
default config) so one-off management commands still kill tmux forks. Real
objects, no mocks — constructing a TmuxBackend only stores socket strings, it
never touches a live tmux server.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_tools.agent_tunnel.backends import (
    Backend,
    HeadlessBackend,
    TmuxBackend,
    backend_by_name,
    backend_for_record,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.paths import uploads_dir_for
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
