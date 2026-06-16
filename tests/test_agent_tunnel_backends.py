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
    backend_for_record,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
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
    # The cache reuses one instance per backend name.
    assert backend_for_record(cfg, store, tmux_rec, cache) is cache["tmux"]
