"""The >share hook stores an absolute project cwd.

A relative ``cwd`` in the hook payload would otherwise propagate to the daemon
as ``rec.project_dir`` and mis-resolve the fork's ``--add-dir``/outbox paths
(the same class as the state_path fix). Real subprocess, no mocks.
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


def test_share_hook_stores_absolute_cwd(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(reg_path)}
    payload = json.dumps(
        {
            "session_id": "11111111-2222-3333-4444-555555555555",
            "prompt": ">share relcwd",
            "cwd": "rel/project",  # relative — must be stored absolute
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
    rec = Registry(reg_path).get("relcwd")
    assert rec is not None
    assert Path(rec.cwd).is_absolute()
