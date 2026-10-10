"""Synthetic subprocess-boundary tests for the standalone session helper."""

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def helper() -> ModuleType:
    """Load the repository helper without executing its CLI."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "ask-session.py"
    spec = importlib.util.spec_from_file_location("ask_session", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fork", [False, True])
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("mode", [None, "bypassPermissions"])
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("answer", 0), ("empty", 5), ("whitespace", 5), ("missing", 5),
     ("exit", 2), ("timeout", 4)],
)
def test_codex_native_execution(
    helper: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], fork: bool, raw: bool,
    mode: str | None, outcome: str, expected: int,
) -> None:
    """Use native commands without accessing transcripts; require an answer."""
    info = {
        "agent": "codex", "sessionId": "source-id", "cwd": str(tmp_path),
        "config_root": str(tmp_path / "account"),
    }
    args = argparse.Namespace(
        fork=fork, mode=mode, model="test-model", message="question",
        timeout=15, raw=raw,
    )
    outputs: list[Path] = []
    events = json.dumps({"type": "thread.started", "thread_id": "fork-id"}) + "\n"

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        prefix = ["codex", "exec"]
        prefix += (
            ["--dangerously-bypass-approvals-and-sandbox"]
            if mode else ["-s", "read-only"]
        )
        prefix += ["--model", "test-model", "fork" if fork else "resume", "--json"]
        output = Path(command[command.index("-o") + 1])
        assert command == prefix + ["-o", str(output), "source-id", "-"]
        assert kwargs["input"] == "question"
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["timeout"] == 15
        assert kwargs["env"]["CODEX_HOME"] == info["config_root"]
        assert kwargs["env"].get("HOME") == os.environ.get("HOME")
        outputs.append(output)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "missing":
            output.unlink()
        if outcome in ("answer", "whitespace"):
            output.write_text(
                "final answer\n" if outcome == "answer" else " \n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(
            command, 2 if outcome == "exit" else 0, events, "unsupported fork",
        )

    monkeypatch.setattr(helper.subprocess, "run", run)
    assert helper._send_to_codex(info, args) == expected
    captured = capsys.readouterr()
    if expected == 0:
        assert captured.out == (events if raw else "final answer\n")
        if not raw:
            assert "done: session_id=fork-id" in captured.err
        assert "done: session_id=source-id" not in captured.err
    else:
        assert captured.out == ""
        message = {
            2: "unsupported fork", 4: "Timed out", 5: "no final response",
        }[expected]
        assert message in captured.err
        assert "done:" not in captured.err
    assert len(outputs) == 1 and not outputs[0].exists()
    assert list(tmp_path.iterdir()) == []


def test_claude_fork_keeps_helper_identification(
    helper: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claude still uses its native fork flag and marks the returned session."""
    source = tmp_path / "source.jsonl"
    original = '{"type":"user","sessionId":"source-id"}\n'
    source.write_text(original)
    fork = tmp_path / "fork-id.jsonl"
    info = {
        "agent": "claude", "sessionId": "source-id", "cwd": str(tmp_path),
        "config_root": str(tmp_path), "session_file": str(source),
    }
    args = argparse.Namespace(
        fork=True, mode=None, model=None, message="question", timeout=15, raw=False,
    )

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command == [
            "claude", "-p", "--resume", "source-id", "--output-format", "json",
            "--fork-session",
        ]
        fork.write_text('{"type":"user","sessionId":"fork-id"}\n')
        reply = json.dumps({"session_id": "fork-id", "result": "answer"})
        return subprocess.CompletedProcess(command, 0, reply, "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    assert helper._send_to_claude(info, args) == 0
    assert capsys.readouterr().out == "answer\n"
    assert json.loads(fork.read_text())["sessionType"] == "helper"
    assert source.read_text() == original
