"""Compaction-boundary tests for both port directions.

Codex rollouts and Claude session files are append-only logs: a
compacted session still contains its full pre-compaction history,
which the live agent no longer sends to the model. Both porters must
mirror the source agent's own resume semantics -- start from the last
compaction's replacement content -- or a long session ports into a
"transcript" far larger than any model context (a real 108-compaction
rollout produced ~6M tokens and could never be resumed).

Uses real functions and real tmp files (no mocks), following the
conventions of the other tests in this directory.
"""

import json
from pathlib import Path

import pytest

from claude_code_tools.port_claude_to_codex import (
    iter_flat_claude_messages,
    port_claude_session_to_codex,
)
from claude_code_tools.port_codex_to_claude import (
    iter_flat_messages,
    port_codex_session_to_claude,
)

MODERN_UUID = "019f6d85-df3c-7c83-84f6-b97e73305bbb"
CLAUDE_UUID = "1d18f22c-9a88-4ea9-ae77-4094c9f87bbb"


def _ts(seconds: int) -> str:
    """Build a deterministic ISO timestamp for fixture lines."""
    return f"2026-07-16T20:42:{seconds:02d}.000Z"


def _resp(seconds: int, payload: dict) -> str:
    """Build a modern-format response_item line."""
    return json.dumps(
        {
            "timestamp": _ts(seconds),
            "type": "response_item",
            "payload": payload,
        }
    )


def _msg(role: str, text: str) -> dict:
    """Build a codex message payload."""
    block = "input_text" if role == "user" else "output_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": block, "text": text}],
    }


def _compacted(
    seconds: int,
    history: list | None = None,
    message: str = "",
    payload_override: dict | None = None,
) -> str:
    """Build a modern-format compacted line."""
    if payload_override is not None:
        payload = payload_override
    else:
        payload = {"message": message}
        if history is not None:
            payload["replacement_history"] = history
    return json.dumps(
        {
            "timestamp": _ts(seconds),
            "type": "compacted",
            "payload": payload,
        }
    )


def _session_meta(seconds: int, cwd: str) -> str:
    """Build a modern-format session_meta line."""
    return json.dumps(
        {
            "timestamp": _ts(seconds),
            "type": "session_meta",
            "payload": {
                "id": MODERN_UUID,
                "timestamp": _ts(seconds),
                "cwd": cwd,
                "git": {"branch": "main"},
                "originator": "codex_exec",
            },
        }
    )


def _write_rollout(codex_home: Path, lines: list) -> Path:
    """Write raw fixture lines as a modern rollout under codex_home."""
    day_dir = codex_home / "sessions" / "2026" / "07" / "16"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-07-16T20-41-57-{MODERN_UUID}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ported_texts(out_path: Path) -> list[str]:
    """Read the message texts of a ported Claude session file."""
    texts = []
    for line in out_path.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        texts.append(data["message"]["content"][0]["text"])
    return texts


def _port_codex(tmp_path: Path, lines: list) -> list[str]:
    """Port a fixture rollout and return the flattened texts."""
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    rollout = _write_rollout(codex_home, lines)
    _, out_path = port_codex_session_to_claude(
        rollout, claude_home=claude_home
    )
    return _ported_texts(out_path)


class TestCodexToClaudeCompaction:
    """Codex -> Claude: the last compacted record replaces history."""

    def test_history_before_last_compaction_dropped(self, tmp_path):
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "old question")),
            _resp(2, _msg("assistant", "old answer")),
            _compacted(
                3, history=[_msg("user", "kept user prompt")]
            ),
            _resp(4, _msg("user", "new question")),
            _resp(5, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "old question" not in joined
        assert "old answer" not in joined
        assert "kept user prompt" in joined
        assert "new question" in joined
        assert "new answer" in joined

    def test_only_last_of_chained_compactions_matters(self, tmp_path):
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "ancient question")),
            _compacted(2, history=[_msg("user", "first-kept")]),
            _resp(3, _msg("user", "middle question")),
            _compacted(
                4,
                history=[
                    _msg("user", "first-kept"),
                    _msg("user", "middle question"),
                ],
            ),
            _resp(5, _msg("user", "final question")),
            _resp(6, _msg("assistant", "final answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "ancient question" not in joined
        # Kept via the LAST replacement history, exactly once.
        assert joined.count("middle question") == 1
        assert joined.count("first-kept") == 1
        assert "final question" in joined

    def test_replacement_history_drops_internal_items(self, tmp_path):
        history = [
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "dev instructions"}
                ],
            },
            _msg("user", "kept user prompt"),
            {
                "type": "compaction",
                "encrypted_content": "AAAA",
            },
        ]
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "old question")),
            _compacted(2, history=history),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "dev instructions" not in joined
        assert "AAAA" not in joined
        assert "kept user prompt" in joined
        assert "new answer" in joined

    def test_legacy_plaintext_summary_becomes_user_message(
        self, tmp_path
    ):
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "old question")),
            _compacted(2, message="summary of earlier work"),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "old question" not in joined
        assert "summary of earlier work" in joined
        assert "new answer" in joined

    def test_unusable_compacted_record_is_ignored(self, tmp_path):
        # No replacement_history list, no plaintext message: treating
        # this as a boundary would drop history with no replacement.
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "old question")),
            _compacted(2, payload_override={"message": ""}),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "old question" in joined
        assert "new answer" in joined

    def test_empty_replacement_history_is_ignored(self, tmp_path):
        # Real compactions always keep at least the summary item; an
        # empty list is a partial/corrupt record, and honoring it
        # would drop all history with nothing to replace it.
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "old question")),
            _compacted(2, history=[]),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "old question" in joined
        assert "new answer" in joined

    def test_records_appended_mid_port_are_ignored(self, tmp_path):
        # A LIVE session can append records (even a new compaction)
        # between the scan pass and the message pass; porting them
        # could straddle a boundary the scan never saw. The message
        # pass must stop at the scan-time record count.
        codex_home = tmp_path / "codex"
        rollout = _write_rollout(
            codex_home,
            [
                _session_meta(0, str(tmp_path / "proj")),
                _resp(1, _msg("user", "q1")),
                _resp(2, _msg("assistant", "a1")),
            ],
        )
        gen = iter_flat_messages(rollout, None)
        first = next(gen)  # scan pass done, message pass started
        with open(rollout, "a", encoding="utf-8") as f:
            f.write(
                _compacted(3, history=[_msg("user", "appended kept")])
                + "\n"
            )
            f.write(_resp(4, _msg("user", "appended question")) + "\n")
        texts = [first["text"]] + [m["text"] for m in gen]
        joined = "\n".join(texts)
        assert "q1" in joined
        assert "a1" in joined
        assert "appended kept" not in joined
        assert "appended question" not in joined

    def test_longer_tag_name_is_not_goal_context(self, tmp_path):
        # A genuine user message starting with a LONGER tag name must
        # not be swallowed by the goal-context prefix match.
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(
                1,
                _msg(
                    "user",
                    "<codex_internal_contextual> is a tag I typed",
                ),
            ),
            _resp(2, _msg("assistant", "answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "<codex_internal_contextual> is a tag I typed" in joined
        assert "[codex goal context]" not in joined

    def test_no_compaction_ports_everything(self, tmp_path):
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "q1")),
            _resp(2, _msg("assistant", "a1")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "q1" in joined
        assert "a1" in joined

    def test_goal_context_keeps_only_last_copy(self, tmp_path):
        # Codex re-injects the active goal as its own user message
        # every turn; only the LAST copy is kept, labeled, so the
        # standing goal survives the port without per-turn bloat.
        def goal(n: int) -> str:
            return (
                '<codex_internal_context source="goal">\n'
                f"Goal text v{n}.\n"
                "</codex_internal_context>"
            )

        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", "real question")),
            _resp(2, _msg("user", goal(1))),
            _resp(3, _msg("assistant", "answer one")),
            _resp(4, _msg("user", goal(2))),
            _resp(5, _msg("assistant", "answer two")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "Goal text v1." not in joined
        assert "Goal text v2." in joined
        assert "[codex goal context]" in joined
        assert "real question" in joined
        assert "answer two" in joined

    def test_goal_context_kept_from_replacement_history(
        self, tmp_path
    ):
        goal = (
            '<codex_internal_context source="goal">\n'
            "Standing goal.\n"
            "</codex_internal_context>"
        )
        history = [
            _msg("user", "kept user prompt"),
            _msg("user", goal),
        ]
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", goal)),
            _compacted(2, history=history),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert joined.count("Standing goal.") == 1
        assert "[codex goal context]" in joined
        assert "kept user prompt" in joined

    def test_goal_context_before_boundary_discarded(self, tmp_path):
        goal = (
            '<codex_internal_context source="goal">\n'
            "Stale goal.\n"
            "</codex_internal_context>"
        )
        lines = [
            _session_meta(0, str(tmp_path / "proj")),
            _resp(1, _msg("user", goal)),
            _compacted(2, history=[_msg("user", "kept")]),
            _resp(3, _msg("assistant", "new answer")),
        ]
        joined = "\n".join(_port_codex(tmp_path, lines))
        assert "Stale goal." not in joined
        assert "kept" in joined


def _claude_line(
    seconds: int,
    role: str,
    text: str,
    cwd: str,
    **extra,
) -> str:
    """Build one Claude session user/assistant line."""
    return json.dumps(
        {
            "parentUuid": None,
            "isSidechain": extra.pop("isSidechain", False),
            "userType": "external",
            "cwd": cwd,
            "sessionId": CLAUDE_UUID,
            "version": "2.1.211",
            "gitBranch": "main",
            "type": role,
            "message": {
                "role": role,
                "content": [{"type": "text", "text": text}],
            },
            "uuid": f"00000000-0000-4000-8000-{seconds:012d}",
            "timestamp": _ts(seconds),
            **extra,
        }
    )


def _compact_boundary_line(seconds: int, cwd: str) -> str:
    """Build a Claude compact_boundary system line."""
    return json.dumps(
        {
            "parentUuid": None,
            "isSidechain": False,
            "type": "system",
            "subtype": "compact_boundary",
            "content": "",
            "level": "info",
            "cwd": cwd,
            "sessionId": CLAUDE_UUID,
            "timestamp": _ts(seconds),
            "uuid": f"00000000-0000-4000-8000-{seconds:012d}",
            "compactMetadata": {"trigger": "auto"},
        }
    )


def _port_claude(tmp_path: Path, lines: list) -> str:
    """Port a fixture Claude session and return the rollout text."""
    codex_home = tmp_path / "codex"
    codex_home.mkdir(exist_ok=True)
    session_file = tmp_path / f"{CLAUDE_UUID}.jsonl"
    session_file.write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    _, out_path = port_claude_session_to_codex(
        session_file, codex_home=codex_home
    )
    return out_path.read_text(encoding="utf-8")


class TestClaudeToCodexCompaction:
    """Claude -> Codex: the last compact summary replaces history."""

    def test_history_before_last_summary_dropped(self, tmp_path):
        cwd = str(tmp_path / "proj")
        summary = (
            "This session is being continued from a previous "
            "conversation that ran out of context. Summary: did X."
        )
        lines = [
            _claude_line(1, "user", "old question", cwd),
            _claude_line(2, "assistant", "old answer", cwd),
            _compact_boundary_line(3, cwd),
            _claude_line(
                4,
                "user",
                summary,
                cwd,
                isCompactSummary=True,
                isVisibleInTranscriptOnly=True,
            ),
            _claude_line(5, "assistant", "post-compact answer", cwd),
        ]
        rollout = _port_claude(tmp_path, lines)
        assert "old question" not in rollout
        assert "old answer" not in rollout
        assert "Summary: did X." in rollout
        assert "post-compact answer" in rollout

    def test_only_last_of_multiple_summaries_matters(self, tmp_path):
        cwd = str(tmp_path / "proj")
        lines = [
            _claude_line(1, "user", "ancient question", cwd),
            _compact_boundary_line(2, cwd),
            _claude_line(
                3, "user", "first summary", cwd, isCompactSummary=True
            ),
            _claude_line(4, "assistant", "middle answer", cwd),
            _compact_boundary_line(5, cwd),
            _claude_line(
                6, "user", "second summary", cwd, isCompactSummary=True
            ),
            _claude_line(7, "assistant", "final answer", cwd),
        ]
        rollout = _port_claude(tmp_path, lines)
        assert "ancient question" not in rollout
        assert "first summary" not in rollout
        assert "middle answer" not in rollout
        assert "second summary" in rollout
        assert "final answer" in rollout

    def test_sidechain_summary_does_not_truncate(self, tmp_path):
        cwd = str(tmp_path / "proj")
        lines = [
            _claude_line(1, "user", "main question", cwd),
            _claude_line(
                2,
                "user",
                "subagent summary",
                cwd,
                isCompactSummary=True,
                isSidechain=True,
            ),
            _claude_line(3, "assistant", "main answer", cwd),
        ]
        rollout = _port_claude(tmp_path, lines)
        assert "main question" in rollout
        # Sidechain lines are dropped entirely, but must not act as
        # a truncation boundary for the main transcript.
        assert "subagent summary" not in rollout
        assert "main answer" in rollout

    def test_no_compaction_ports_everything(self, tmp_path):
        cwd = str(tmp_path / "proj")
        lines = [
            _claude_line(1, "user", "q1", cwd),
            _claude_line(2, "assistant", "a1", cwd),
        ]
        rollout = _port_claude(tmp_path, lines)
        assert "q1" in rollout
        assert "a1" in rollout

    def test_records_appended_mid_port_are_ignored(self, tmp_path):
        # A LIVE session can append records (even a new compaction)
        # between the scan pass and the message pass; porting them
        # could straddle a boundary the scan never saw. The message
        # pass must stop at the scan-time record count.
        cwd = str(tmp_path / "proj")
        session_file = tmp_path / f"{CLAUDE_UUID}.jsonl"
        session_file.write_text(
            "\n".join(
                [
                    _claude_line(1, "user", "q1", cwd),
                    _claude_line(2, "assistant", "a1", cwd),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        gen = iter_flat_claude_messages(session_file, None)
        first = next(gen)  # scan pass done, message pass started
        with open(session_file, "a", encoding="utf-8") as f:
            f.write(
                _claude_line(
                    3,
                    "user",
                    "appended summary",
                    cwd,
                    isCompactSummary=True,
                )
                + "\n"
            )
            f.write(
                _claude_line(4, "user", "appended question", cwd)
                + "\n"
            )
        texts = [first["text"]] + [m["text"] for m in gen]
        joined = "\n".join(texts)
        assert "q1" in joined
        assert "a1" in joined
        assert "appended summary" not in joined
        assert "appended question" not in joined

    def test_empty_summary_line_is_not_a_boundary(self, tmp_path):
        # A marked summary line with no usable text must not truncate
        # the transcript: it would drop history while contributing
        # no summary at all.
        cwd = str(tmp_path / "proj")
        lines = [
            _claude_line(1, "user", "old question", cwd),
            _claude_line(2, "assistant", "old answer", cwd),
            _claude_line(
                3, "user", "   ", cwd, isCompactSummary=True
            ),
            _claude_line(4, "assistant", "post answer", cwd),
        ]
        rollout = _port_claude(tmp_path, lines)
        assert "old question" in rollout
        assert "old answer" in rollout
        assert "post answer" in rollout
