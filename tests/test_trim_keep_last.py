"""--keep-last N: the tail of a session is never trimmed (issue #72).

Continuing a session means the last few messages are the context being carried
forward, whatever role they have. These tests pin that the kept tail survives
tool-result suppression, assistant-text replacement and, for Codex, output
supression, while earlier messages are still trimmed as before.
"""
import json

import pytest

from claude_code_tools.trim_session import process_session


def _claude_line(kind, text, line_no):
    """One plausible Claude Code JSONL record carrying `text`."""
    if kind == "assistant":
        return {
            "type": "assistant",
            "uuid": f"a-{line_no}",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    if kind == "tool_result":
        return {
            "type": "user",
            "uuid": f"u-{line_no}",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t-{line_no}", "content": text}
                ],
            },
        }
    return {
        "type": "user",
        "uuid": f"u-{line_no}",
        "message": {"role": "user", "content": text},
    }


LONG = "x" * 2000


def _write_session(path, records):
    with open(path, "w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _read_texts(path):
    return path.read_text().splitlines()


@pytest.fixture
def session_file(tmp_path):
    records = [
        _claude_line("user", "first question", 1),
        _claude_line("tool_result", LONG, 2),      # older: should be trimmed
        _claude_line("assistant", LONG, 3),        # older: should be trimmed
        _claude_line("user", "second question", 4),
        _claude_line("tool_result", LONG, 5),      # in the kept tail
        _claude_line("assistant", LONG, 6),        # in the kept tail
    ]
    path = tmp_path / "session.jsonl"
    _write_session(path, records)
    return path


def test_kept_tail_is_untouched(session_file, tmp_path):
    out = tmp_path / "out.jsonl"
    num_tools, num_assistant, saved = process_session(
        "claude", session_file, out, None, 500, verbose=False,
        trim_assistant_messages=-1, keep_last=2,
    )

    lines = _read_texts(out)
    assert LONG not in lines[1] and LONG not in lines[2], "older records still trimmed"
    assert LONG in lines[4] and LONG in lines[5], "the kept tail must survive"
    assert (num_tools, num_assistant) == (1, 1)
    assert saved > 0


def test_without_keep_last_the_tail_is_trimmed(session_file, tmp_path):
    """Control: the same file, no flag, trims the tail as it always did."""
    out = tmp_path / "out2.jsonl"
    num_tools, num_assistant, _ = process_session(
        "claude", session_file, out, None, 500, verbose=False,
        trim_assistant_messages=-1,
    )

    lines = _read_texts(out)
    assert LONG not in lines[4], "the tail tool result is trimmed without the flag"
    assert (num_tools, num_assistant) == (2, 1)


def test_keep_last_larger_than_the_session_keeps_everything(session_file, tmp_path):
    """Asking to keep more messages than exist exempts the whole session."""
    out = tmp_path / "out3.jsonl"
    num_tools, num_assistant, saved = process_session(
        "claude", session_file, out, None, 500, verbose=False,
        trim_assistant_messages=-1, keep_last=99,
    )

    assert LONG in out.read_text()
    assert (num_tools, num_assistant, saved) == (0, 0, 0)


# ── the kept tail survives the other trim paths too ─────────────────


def _assistant_with_tool_use(text, tool_input_text, line_no):
    return {
        "type": "assistant",
        "uuid": f"a-{line_no}",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": text},
                {"type": "tool_use", "id": f"t-{line_no}", "name": "Write",
                 "input": {"file_path": "/tmp/x", "content": tool_input_text}},
            ],
        },
    }


def test_kept_tail_keeps_its_tool_use_input(tmp_path):
    """A kept Write call must not be rewritten with a truncation marker."""
    path = tmp_path / "s.jsonl"
    _write_session(path, [
        _claude_line("user", "older", 1),
        _assistant_with_tool_use("older", LONG, 2),   # older: tool_use trimmed
        _claude_line("user", "keep me", 3),
        _assistant_with_tool_use("kept", LONG, 4),    # tail: must survive
    ])
    out = tmp_path / "o.jsonl"
    process_session("claude", path, out, None, 500, verbose=False, keep_last=2)

    lines = out.read_text().splitlines()
    assert LONG not in lines[1], "older tool_use input should still be trimmed"
    assert LONG in lines[3], "kept tool_use input must not be truncated"


def _codex_message(role, text, kind="response_item"):
    payload = {"type": "message", "role": role,
               "content": [{"type": "output_text" if role == "assistant" else "input_text",
                            "text": text}]}
    return {"type": kind, "payload": payload}


def test_codex_tail_is_counted_by_messages_not_by_records(tmp_path):
    """A reasoning item and an event_msg mirror must not push the cutoff."""
    path = tmp_path / "c.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "abc", "cwd": "/x", "version": "1"}},
        _codex_message("user", "older question"),
        _codex_message("assistant", LONG),                       # older: trimmed
        _codex_message("user", "the question we continue from"),
        _codex_message("assistant", LONG),                       # the kept message
        {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        _codex_message("assistant", LONG, kind="event_msg"),     # UI mirror, same message
    ]
    _write_session(path, records)

    out = tmp_path / "co.jsonl"
    process_session(
        "codex", path, out, None, 500, verbose=False,
        trim_assistant_messages=-1, keep_last=2,
    )

    lines = out.read_text().splitlines()
    assert LONG not in lines[2], "older assistant message should still be trimmed"
    assert LONG in lines[4], "the final message is kept"
    assert LONG in lines[6], "its event_msg mirror is kept with it, not counted against it"


def test_aichat_cli_exposes_keep_last():
    """The shipped entry point has to offer the flag, not just trim_session."""
    from claude_code_tools.aichat import main

    names = {p.name for p in main.commands["trim"].params}
    assert "keep_last" in names, sorted(names)
