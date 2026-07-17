"""Regression tests for the iteration-8 adversarial-review fixes.

Covers: strip-before-cap for tool values represented as LISTS of
text blocks (leading/trailing whitespace must neither consume the
truncation cap nor inflate the truncation count), agent detection
that only treats RECOGNIZED ``record_type`` values as Codex evidence
(a Claude record carrying ``record_type: null`` stays Claude), and
id-based Codex lookup that streams the ENTIRE rollout instead of
treating the first 25 unrecognized records as conclusive absence.

Split into its own file to keep the sibling port test files under
the repo's 1000-line limit. Shares fixture helpers via imports from
tests.test_port_session.
"""

import json
import uuid
from pathlib import Path

import pytest

from claude_code_tools.port_codex_to_claude import (
    TOOL_TEXT_CAP,
    port_codex_session_to_claude,
)
from claude_code_tools.session_utils import (
    detect_agent_from_content,
    find_matching_session_files,
)
from tests.test_port_session import (
    MODERN_UUID,
    _msg,
    _resp,
    _session_meta,
    _ts,
    write_rollout_lines,
)


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "myproj"
    d.mkdir()
    return d


@pytest.fixture
def claude_home(tmp_path):
    d = tmp_path / "claude-home"
    d.mkdir()
    return d


@pytest.fixture
def codex_home(tmp_path):
    d = tmp_path / "codex-home"
    d.mkdir()
    return d


def _tool_output_rollout(codex_home, project_dir, output) -> Path:
    """Write a minimal rollout with one function_call_output value."""
    lines = [
        _session_meta(0, MODERN_UUID, str(project_dir)),
        _resp(1, _msg("user", "Q")),
        _resp(
            2,
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": output,
            },
        ),
    ]
    return write_rollout_lines(codex_home, MODERN_UUID, lines)


class TestBlockListStripBeforeCap:
    """Text-block-list tool values are stripped BEFORE capping."""

    def test_leading_whitespace_block_does_not_eat_cap(
        self, codex_home, claude_home, project_dir
    ):
        # One block: 2000 leading spaces, then real text over the cap.
        padded = " " * 2000 + "z" * (TOOL_TEXT_CAP + 100)
        rollout = _tool_output_rollout(
            codex_home,
            project_dir,
            [{"type": "output_text", "text": padded}],
        )
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert (
            "[codex tool result] " + "z" * TOOL_TEXT_CAP
            + "... [truncated 100 chars]"
        ) in raw
        assert "z" * (TOOL_TEXT_CAP + 1) not in raw

    def test_whitespace_only_first_block_skipped(
        self, codex_home, claude_home, project_dir
    ):
        rollout = _tool_output_rollout(
            codex_home,
            project_dir,
            [
                {"type": "output_text", "text": " " * 5000},
                {
                    "type": "output_text",
                    "text": "z" * (TOOL_TEXT_CAP + 40) + " " * 5000,
                },
            ],
        )
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        # Trailing whitespace is excluded from the truncation count
        # and the whitespace-only leading block from the kept prefix.
        assert (
            "[codex tool result] " + "z" * TOOL_TEXT_CAP
            + "... [truncated 40 chars]"
        ) in raw

    def test_internal_separator_whitespace_preserved(
        self, codex_home, claude_home, project_dir
    ):
        rollout = _tool_output_rollout(
            codex_home,
            project_dir,
            [
                {"type": "output_text", "text": "  hello  "},
                {"type": "output_text", "text": "  world  "},
            ],
        )
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        # Same as "\n".join(parts).strip(): inner padding survives.
        assert "[codex tool result] hello  \\n  world" in raw

    def test_all_whitespace_blocks_are_empty_output(
        self, codex_home, claude_home, project_dir
    ):
        rollout = _tool_output_rollout(
            codex_home,
            project_dir,
            [
                {"type": "output_text", "text": "   "},
                {"type": "output_text", "text": "\n\t"},
            ],
        )
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "[codex tool result] (empty output)" in raw


class TestRecordTypeDetection:
    """Only recognized record_type values count as Codex evidence."""

    def test_claude_line_with_null_record_type_is_claude(
        self, tmp_path
    ):
        line = {
            "sessionId": str(uuid.uuid4()),
            "record_type": None,
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "uuid": str(uuid.uuid4()),
        }
        path = tmp_path / "claude-null-record-type.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        assert detect_agent_from_content(path) == "claude"

    def test_unknown_record_type_alone_is_unrecognized(self, tmp_path):
        path = tmp_path / "unknown-record-type.jsonl"
        path.write_text(
            json.dumps({"record_type": "totally_new"}) + "\n",
            encoding="utf-8",
        )
        assert detect_agent_from_content(path) is None

    def test_state_record_type_is_codex(self, tmp_path):
        path = tmp_path / "state-record-type.jsonl"
        path.write_text(
            json.dumps({"record_type": "state"}) + "\n",
            encoding="utf-8",
        )
        assert detect_agent_from_content(path) == "codex"


class TestIdLookupStreamsWholeFile:
    """Id-based Codex lookup must not stop at 25 leading records."""

    def test_rollout_with_many_unrecognized_leading_records_found(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            json.dumps({"weird": i, "record_type": f"future_{i}"})
            for i in range(40)
        ]
        lines += [
            _session_meta(41, MODERN_UUID, str(project_dir)),
            _resp(42, _msg("user", "Q late in file")),
            _resp(43, _msg("assistant", "A late", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        matches = find_matching_session_files(
            MODERN_UUID,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert matches == [("codex", rollout)]

    def test_rollout_with_oversized_leading_lines_found(
        self, codex_home, claude_home, project_dir
    ):
        big = '{"garbage": "' + "x" * 1_100_000 + '"}'
        lines = [big for _ in range(26)]
        lines += [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        matches = find_matching_session_files(
            MODERN_UUID,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert matches == [("codex", rollout)]
