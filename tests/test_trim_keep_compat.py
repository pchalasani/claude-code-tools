"""Keep-last must preserve existing positional APIs and genuine user turns."""

import json
from pathlib import Path

import pytest

from claude_code_tools import trim_session_claude, trim_session_codex
from claude_code_tools.trim_in_place import trim_session_in_place
from claude_code_tools.trim_session import (
    create_placeholder,
    process_session,
    trim_and_create_session,
)
from tests.test_port_claude_to_codex_noise import TASK_NOTIFICATION_TEXT
from tests.test_trim_keep_turns import LONG, _claude, _turn, _write


def test_positional_dry_run_stays_read_only(tmp_path: Path) -> None:
    """The pre-existing fifth argument still means dry_run=True."""
    source = tmp_path / "session.jsonl"
    _write(source, _turn("claude", 1) + _turn("claude", 2))
    original = source.read_bytes()
    result = trim_session_in_place(source, None, 500, None, True)
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["num_tools_trimmed"] > 0
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("*.bak"))


def test_positional_savings_threshold_keeps_its_meaning(tmp_path: Path) -> None:
    """The pre-existing seventh argument is the minimum token savings."""
    source = tmp_path / "session.jsonl"
    _write(source, _turn("claude", 1))
    result = trim_and_create_session(
        "claude", source, None, 500, tmp_path, None, 1_000_000,
    )
    assert result["num_tools_trimmed"] > 0
    assert result["nothing_to_trim"] is True
    assert result["output_file"] is None


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_positional_parent_file_keeps_its_meaning(
    tmp_path: Path, agent: str,
) -> None:
    """The provider's pre-existing ninth argument names the original file."""
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    _write(source, _turn(agent, 1))
    processor = {
        "claude": trim_session_claude.process_claude_session,
        "codex": trim_session_codex.process_codex_session,
    }[agent]
    result = processor(
        source, output, {}, {"unknown"}, 500, create_placeholder,
        None, None, "/original-parent.jsonl",
    )
    assert result[0] > 0
    assert "/original-parent.jsonl" in output.read_text()


@pytest.mark.parametrize("as_blocks", [False, True])
@pytest.mark.parametrize("notice", [
    TASK_NOTIFICATION_TEXT,
    "<system-reminder>internal reminder</system-reminder>",
    "<local-command-stdout>internal output</local-command-stdout>",
])
def test_internal_notifications_do_not_start_turns(
    tmp_path: Path, as_blocks: bool, notice: str,
) -> None:
    """Unmarked notifications do not shift the cutoff past the real prompt."""
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    records = _turn("claude", 1) + _turn("claude", 2)
    content = [{"type": "text", "text": notice}] if as_blocks else notice
    records.insert(-1, _claude("user", content))
    _write(source, records)
    process_session(
        "claude", source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=1,
    )
    result = [json.loads(line) for line in output.read_text().splitlines()]
    assert result[6:] == records[6:]
    assert LONG not in output.read_text().splitlines()[2]


@pytest.mark.parametrize("prompt", [
    "<task-notification> is the tag I want explained",
    [{"type": "image", "source": {"type": "base64", "data": "fixture"}}],
])
def test_real_prompts_still_start_turns(tmp_path: Path, prompt: object) -> None:
    """Tag discussions and image prompts must still count as user turns."""
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    records = _turn("claude", 1) + _turn("claude", 2)
    records[6] = _claude("user", prompt)
    _write(source, records)
    process_session(
        "claude", source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=1,
    )
    result = [json.loads(line) for line in output.read_text().splitlines()]
    assert result[6:] == records[6:]
    assert LONG not in output.read_text().splitlines()[2]


@pytest.mark.parametrize("wrapper", [
    '<codex_internal_context source="goal">goal</codex_internal_context>',
    "<environment_context>cwd</environment_context>",
    "# AGENTS.md instructions\nRepository instructions",
])
def test_codex_injected_context_does_not_start_a_turn(
    tmp_path: Path, wrapper: str,
) -> None:
    """Injected goal/context messages continue the user's existing turn."""
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    records = _turn("codex", 1) + _turn("codex", 2)
    injected = {"type": "response_item", "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": wrapper}],
    }}
    records.append(injected)
    _write(source, records)
    process_session(
        "codex", source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=1,
    )
    result = [json.loads(line) for line in output.read_text().splitlines()]
    assert result[6:] == records[6:]
    assert LONG not in output.read_text().splitlines()[1]


@pytest.mark.parametrize("content", [
    [{"type": "input_text", "text": "<codex_internal_contextual> explain"}],
    [{"type": "input_image", "image_url": "fixture.png"}],
    [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"},
     {"type": "input_text", "text": "the actual user question"}],
])
def test_codex_real_prompts_still_start_turns(
    tmp_path: Path, content: list[dict[str, str]],
) -> None:
    """Genuine text, images and prompts beside context still count."""
    source, output = tmp_path / "session.jsonl", tmp_path / "out.jsonl"
    records = _turn("codex", 1) + _turn("codex", 2)
    records[6]["payload"]["content"] = content
    _write(source, records)
    process_session(
        "codex", source, output, None, 500, verbose=False,
        trim_assistant_messages=1000, keep_last=1,
    )
    result = [json.loads(line) for line in output.read_text().splitlines()]
    assert result[6:] == records[6:]
    assert LONG not in output.read_text().splitlines()[1]
