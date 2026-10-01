"""Real-tmux regressions for cached targets after window removal."""

import os
import shlex
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from claude_code_tools.tmux_remote_controller import RemoteTmuxController


@pytest.fixture
def controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[RemoteTmuxController]:
    """Provide a controller on a private tmux socket and clean it up."""
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is required for this integration test")
    wrapper = tmp_path / "tmux"
    socket = tmp_path / "socket"
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(tmux)} -f /dev/null "
        f'-S {shlex.quote(str(socket))} "$@"\n'
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("TMUX", raising=False)
    remote = RemoteTmuxController()
    try:
        yield remote
    finally:
        remote.cleanup_session()


@pytest.mark.parametrize("target_kind", ["returned", "index", "pane"])
def test_killing_alias_clears_cached_window(
    controller: RemoteTmuxController, target_kind: str
) -> None:
    """Resolve aliases before killing so later operations select a live window."""
    controller.launch_cli("sleep 120")
    surviving_id = controller.target_window
    target = controller.launch_cli("sleep 120")
    assert target is not None
    if target_kind == "index":
        target = target.rsplit(":", 1)[1]
    elif target_kind == "pane":
        target, code = controller._run_tmux(
            ["display-message", "-p", "-t", target, "#{pane_id}"]
        )
        assert code == 0
    controller.kill_window(target)
    assert controller.target_window is None
    assert controller._window_target(None) == surviving_id
