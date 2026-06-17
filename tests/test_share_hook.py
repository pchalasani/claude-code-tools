"""The >share hook: absolute cwd + access preserved across a relabel.

Real subprocess (no mocks); the registry is redirected via the
``AGENT_TUNNEL_REGISTRY`` env var the hook honors.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from claude_code_tools.agent_tunnel.registry import Registry

HOOK = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "agent-tunnel"
    / "hooks"
    / "share_hook.py"
)


def _run(
    registry: Path, prompt: str, session_id: str, cwd: str = "/work"
) -> None:
    """Invoke the >share hook with a payload; assert it exits cleanly."""
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": session_id,
            "prompt": prompt,
            "cwd": cwd,
            "transcript_path": "",
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_share_hook_stores_absolute_cwd(tmp_path: Path) -> None:
    reg = tmp_path / "registry.json"
    _run(reg, ">share relcwd", "sess-cwd", cwd="rel/project")  # relative cwd
    rec = Registry(reg).get("relcwd")
    assert rec is not None
    assert Path(rec.cwd).is_absolute()


def test_share_hook_relabel_preserves_access(tmp_path: Path) -> None:
    # Re-sharing without a flag must keep the current access level, even when
    # the re-share relabels the handle (which pops the old record).
    reg = tmp_path / "registry.json"
    _run(reg, ">share --write paydocs", "sess-1")
    _run(reg, ">share payments", "sess-1")  # relabel, no access flag
    new = Registry(reg).get("payments")
    assert new is not None and new.access == "write"  # preserved
    assert Registry(reg).get("paydocs") is None  # old handle relabeled away


def test_share_hook_skip_permissions_sets_all(tmp_path: Path) -> None:
    # `>share --dangerously-skip-permissions` records the top "all" level; the
    # daemon still gates it behind [claude] allow_skip_permissions.
    reg = tmp_path / "registry.json"
    _run(reg, ">share --dangerously-skip-permissions full", "sess-x")
    rec = Registry(reg).get("full")
    assert rec is not None and rec.access == "all"
