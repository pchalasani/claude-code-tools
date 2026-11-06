"""Regression coverage for the owner-authorized user-turn scope of PR #256."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from claude_code_tools.trim_session import process_session

LONG = "payload " * 400


def _claude(kind: str, content: Any) -> dict[str, Any]:
    """Build a Claude message record."""
    return {
        "type": kind, "sessionId": "fixture-session",
        "message": {"role": kind, "content": content},
    }


def _codex(kind: str, **payload: Any) -> dict[str, Any]:
    """Build a Codex response record."""
    return {"type": "response_item", "payload": {"type": kind, **payload}}


def _turn(agent: str, number: int) -> list[dict[str, Any]]:
    """Build a prompt followed by multiple assistant/tool exchanges."""
    if agent == "claude":
        return [
            _claude("user", [{"type": "text", "text": f"prompt {number}"}]),
            _claude("assistant", [
                {"type": "text", "text": LONG},
                {"type": "tool_use", "id": f"call-{number}", "name": "Write",
                 "input": {"content": LONG}},
            ]),
            _claude("user", [
                {"type": "tool_result", "tool_use_id": f"call-{number}",
                 "content": LONG},
            ]),
            {**_claude("user", "synthetic metadata"), "isMeta": True},
            {**_claude("user", "compaction summary"), "isCompactSummary": True},
            _claude("assistant", [{"type": "text", "text": LONG}]),
        ]
    return [
        _codex("message", role="user", content=[
            {"type": "input_text", "text": f"prompt {number}"},
        ]),
        _codex("message", role="assistant", content=[
            {"type": "output_text", "text": LONG},
        ]),
        _codex("function_call", call_id=f"call-{number}", name="shell",
               arguments='{"command":"echo fixture"}'),
        _codex("function_call_output", call_id=f"call-{number}", output=LONG),
        _codex("reasoning", summary=[]),
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": LONG,
        }},
    ]


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    """Write a synthetic transcript with no dependency on user data."""
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("keep", [1, 2, 99])
def test_preserves_complete_user_turns(
    tmp_path: Path, agent: str, keep: int,
) -> None:
    """Every record after the selected prompt survives, including tool results."""
    turns = [_turn(agent, number) for number in range(3)]
    records = [record for turn in turns for record in turn]
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    _write(source, records)
    tools, assistant, saved = process_session(
        agent, source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=keep,
    )
    result = [json.loads(line) for line in output.read_text().splitlines()]
    cutoff = max(0, 3 - keep) * len(turns[0])
    assert result[cutoff:] == records[cutoff:]
    if cutoff:
        assert LONG not in json.dumps([
            record for record in result[:cutoff]
            if record.get("type") != "event_msg"
        ])
        assert tools > 0 and assistant > 0 and saved > 0
    else:
        assert (tools, assistant, saved) == (0, 0, 0)


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("keep", [0, -1])
def test_nonpositive_keep_does_not_change_trimming(
    tmp_path: Path, agent: str, keep: int,
) -> None:
    """Nonpositive values retain the existing no-exemption behavior."""
    source = tmp_path / "session.jsonl"
    _write(source, _turn(agent, 1))
    outputs = [tmp_path / "default.jsonl", tmp_path / "explicit.jsonl"]
    stats = [process_session(
        agent, source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=value,
    ) for output, value in zip(outputs, [None, keep])]
    assert stats[0] == stats[1]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert stats[0][0] > 0


@pytest.mark.parametrize("command", ["trim", "trim-in-place"])
def test_shipped_cli_preserves_user_turn(tmp_path: Path, command: str) -> None:
    """Run the real CLI on an explicit fixture path, without a session search."""
    source = tmp_path / "session.jsonl"
    records = _turn("claude", 1) + _turn("claude", 2)
    _write(source, records)
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(claude_home)}
    args = [sys.executable]
    if command == "trim":
        # Exercise the shipped Click command directly: the root callback's
        # unrelated auto-indexer writes to the user's real search index.
        args.extend(["-c", (
            "import sys; from claude_code_tools.aichat import trim; "
            "trim.main(args=sys.argv[2:], obj={"
            "'claude_home':sys.argv[1], 'codex_home':sys.argv[1]})"
        ), str(claude_home)])
    else:
        args.extend(["-m", "claude_code_tools.aichat", command])
    args.extend([
        str(source), "--keep-last", "1", "--claude-home", str(claude_home),
    ])
    if command == "trim-in-place":
        args.append("--json")
    result = subprocess.run(
        args, input="n\n", capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if command == "trim":
        generated = [path for path in tmp_path.glob("*.jsonl") if path != source]
        assert len(generated) == 1, result.stdout + result.stderr
        output = generated[0]
    else:
        contract = json.loads(result.stdout)
        assert contract["applied"] is True
        output = source
    lines = output.read_text().splitlines()
    assert LONG not in lines[2]
    assert LONG in lines[7] and LONG in lines[8]
