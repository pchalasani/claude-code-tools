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


@pytest.mark.parametrize("operation", ["send", "kill", "capture"])
@pytest.mark.parametrize("replacement", ["unrelated", "unmarked"])
def test_reused_cached_id_is_not_a_default_target(
    controller: RemoteTmuxController, operation: str, replacement: str
) -> None:
    """Reject reused IDs outside the managed session or without a ready mark."""
    controller.launch_cli("sleep 120")
    cached = controller.target_window
    assert cached == "@1"
    assert controller._run_tmux(["kill-server"])[1] == 0
    session = "unrelated" if replacement == "unrelated" else controller.session_name
    try:
        assert controller._run_tmux(
            ["new-session", "-d", "-s", session, "sleep 120"]
        )[1] == 0
        reused, code = controller._run_tmux(
            ["new-window", "-t", session, "-P", "-F", "#{window_id}", "sleep 120"]
        )
        assert code == 0 and reused == cached
        if replacement == "unrelated":
            assert controller._run_tmux(
                ["set-option", "-w", "-t", reused, controller._ready_option, reused]
            )[1] == 0
        with pytest.raises(ValueError, match="No target pane/window specified"):
            if operation == "send":
                controller.send_keys("unexpected", enter=False)
            elif operation == "kill":
                controller.kill_window()
            else:
                controller.capture_pane()
        assert controller.target_window is None
        assert controller._run_tmux(["has-session", "-t", session])[1] == 0
    finally:
        controller._run_tmux(["kill-server"])


def test_cached_window_survives_active_window_change(
    controller: RemoteTmuxController,
) -> None:
    """Continue selecting the last launched window after an active-window change."""
    controller.launch_cli("sleep 120")
    first = controller.target_window
    controller.launch_cli("sleep 120")
    second = controller.target_window
    assert first is not None and second is not None
    assert controller._run_tmux(["select-window", "-t", first])[1] == 0
    assert controller._window_target(None) == second


def test_launch_does_not_reuse_prefix_matching_session(
    controller: RemoteTmuxController,
) -> None:
    """Launch into the exact managed session even when a longer name exists."""
    other = f"{controller.session_name}-old"
    try:
        assert controller._run_tmux(
            ["new-session", "-d", "-s", other, "sleep 120"]
        )[1] == 0
        target = controller.launch_cli("sleep 120")
        assert target is not None
        assert target.split(":", 1)[0] == controller.session_name
        assert controller._window_target(None) == controller.target_window
    finally:
        controller._run_tmux(["kill-server"])
