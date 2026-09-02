"""Tests for the Codex UI event stream synthesized during a port."""

from pathlib import Path
from typing import Any

import pytest

from claude_code_tools.port_claude_to_codex import (
    port_claude_session_to_codex,
)
from tests.test_port_claude_to_codex import (
    CLAUDE_SID,
    _line,
    _read_lines,
)


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    """Create an empty Codex home."""
    path = tmp_path / "codex-home"
    path.mkdir()
    return path


def _port(tmp_path: Path, codex_home: Path, lines: list[str]) -> Path:
    """Port a real temporary Claude JSONL file and return its rollout."""
    source = tmp_path / f"{CLAUDE_SID}.jsonl"
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, rollout = port_claude_session_to_codex(
        source, codex_home=codex_home
    )
    return rollout


def _message_records(path: Path) -> list[dict[str, Any]]:
    """Read rollout records after session metadata."""
    return _read_lines(path)[1:]


def test_events_pair_with_unchanged_response_sequence(
    tmp_path: Path, codex_home: Path
) -> None:
    """Each response is immediately followed by its matching UI event."""
    source_messages = [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]
    lines = [
        _line(index, role, text)
        for index, (role, text) in enumerate(source_messages)
    ]
    records = _message_records(_port(tmp_path, codex_home, lines))

    responses = records[::2]
    assert [
        (
            record["payload"]["role"],
            record["payload"]["content"][0]["text"],
        )
        for record in responses
    ] == source_messages

    for response, event in zip(responses, records[1::2]):
        role = response["payload"]["role"]
        text = response["payload"]["content"][0]["text"]
        expected_type = (
            "user_message" if role == "user" else "agent_message"
        )
        assert response["type"] == "response_item"
        assert event["type"] == "event_msg"
        assert event["timestamp"] == response["timestamp"]
        assert event["payload"]["type"] == expected_type
        assert event["payload"]["message"] == text


def test_event_payloads_have_exact_native_shapes(
    tmp_path: Path, codex_home: Path
) -> None:
    """User and assistant UI payloads match native rollout shapes."""
    records = _message_records(
        _port(
            tmp_path,
            codex_home,
            [_line(0, "user", "hello"), _line(1, "assistant", "hi")],
        )
    )
    user_response, user_event, agent_response, agent_event = records
    assert user_event == {
        "timestamp": user_response["timestamp"],
        "type": "event_msg",
        "payload": {
            "type": "user_message",
            "message": "hello",
            "images": [],
            "local_images": [],
            "audio": [],
            "local_audio": [],
            "text_elements": [],
        },
    }
    assert agent_event == {
        "timestamp": agent_response["timestamp"],
        "type": "event_msg",
        "payload": {
            "type": "agent_message",
            "message": "hi",
            # Real agent_message events carry both of these; `phase`
            # matches the paired response item, `memory_citation` is
            # present and null unless Codex cited a memory.
            "phase": agent_response["payload"]["phase"],
            "memory_citation": None,
        },
    }


def test_user_only_session_emits_only_user_events(
    tmp_path: Path, codex_home: Path
) -> None:
    """A session without assistant replies emits one user event per turn."""
    records = _message_records(
        _port(
            tmp_path,
            codex_home,
            [_line(0, "user", "one"), _line(1, "user", "two")],
        )
    )
    event_types = [
        record["payload"]["type"]
        for record in records
        if record["type"] == "event_msg"
    ]
    assert event_types == ["user_message", "user_message"]


def test_assistant_only_session_emits_matching_events(
    tmp_path: Path, codex_home: Path
) -> None:
    """An assistant-only source includes its synthetic opener event."""
    records = _message_records(
        _port(tmp_path, codex_home, [_line(0, "assistant", "hello")])
    )
    event_types = [
        record["payload"]["type"]
        for record in records
        if record["type"] == "event_msg"
    ]
    assert event_types == ["user_message", "agent_message"]
