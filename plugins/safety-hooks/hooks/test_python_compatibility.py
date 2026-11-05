"""Exercise standalone safety hooks on the interpreter running pytest.

Run with Python 3.9 as well as the project's current Python to catch eager
evaluation of annotations that would otherwise disable the safety checks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

import pytest

HOOKS_DIR = Path(__file__).resolve().parent


def run_hook(script: str, payload: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Run a checked-out hook with isolated working files and approval state.

    Args:
        script: Hook filename within the plugin's hooks directory.
        payload: JSON event passed on standard input.
        cwd: Temporary directory for hook working files.

    Returns:
        The hook's JSON response after a successful process exit.
    """
    env = dict(os.environ)
    env.pop("CCTOOLS_ALLOW_GIT", None)
    env["CLAUDE_PLUGIN_ROOT"] = str(HOOKS_DIR.parent)
    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps({**payload, "session_id": str(uuid4())}),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        cwd=cwd,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert "error" not in response, response
    return response


@pytest.mark.parametrize(
    ("command", "decision"),
    [
        ("rm victim.txt", "deny"),
        ("git add -A", "deny"),
        ("git commit -m test", "ask"),
        ("cat README.md", "approve"),
    ],
)
def test_bash_decisions(command: str, decision: str, tmp_path: Path) -> None:
    """Imports must succeed and reach the expected Bash safety check."""
    response = run_hook(
        "bash_hook.py",
        {"tool_name": "Bash", "tool_input": {"command": command}},
        tmp_path,
    )
    if decision == "approve":
        assert response == {"decision": "approve"}
    else:
        assert response["hookSpecificOutput"]["permissionDecision"] == decision


@pytest.mark.parametrize("tool_name", ["Write", "Edit"])
def test_file_length_decision(tool_name: str, tmp_path: Path) -> None:
    """Both file-writing events must reach the size check without crashing."""
    target = tmp_path / "large.py"
    target.write_text("old\n", encoding="utf-8")
    content = "pass\n" * 10001
    response = run_hook(
        "file_length_limit_hook.py",
        {
            "tool_name": tool_name,
            "tool_input": {
                "file_path": str(target),
                "content": content,
                "old_string": "old\n",
                "new_string": content,
            },
        },
        tmp_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_read_decision(tmp_path: Path) -> None:
    """The Read entrypoint must still deny reading a dotenv file."""
    response = run_hook(
        "read_env_protection_hook.py",
        {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
        tmp_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_prompt_decision(tmp_path: Path) -> None:
    """The prompt entrypoint must report approval state without toggling it."""
    response = run_hook(
        "allow_git_hook.py", {"prompt": ">allow-git status"}, tmp_path
    )
    assert response["decision"] == "block"
    assert "Commits require approval" in response["reason"]
