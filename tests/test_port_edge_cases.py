"""Edge-case tests for the codex -> claude session converter.

Split out of tests/test_port_session.py (which holds the happy-path
converter, metadata, legacy-format and CLI tests) to keep both files
under the repo's 1000-line limit. Shares its fixture helpers via
imports from tests.test_port_session.
"""

import json
import uuid

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.port_codex_to_claude import (
    TOOL_TEXT_CAP,
    port_codex_session_to_claude,
)
from claude_code_tools.session_utils import detect_agent_from_content
from tests.test_port_session import (
    MODERN_UUID,
    _msg,
    _read_lines,
    _resp,
    _session_meta,
    _ts,
    write_modern_rollout,
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


class TestMalformedTolerance:
    """Structurally malformed records are skipped, never crash."""

    def test_null_and_invalid_shapes_are_skipped(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            # payload: null must not crash
            json.dumps(
                {"timestamp": _ts(0), "type": "session_meta",
                 "payload": None}
            ),
            # git as a non-dict must not crash; cwd null ignored
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "session_meta",
                    "payload": {
                        "id": MODERN_UUID,
                        "cwd": str(project_dir),
                        "git": "invalid",
                        "timestamp": None,
                    },
                }
            ),
            # response_item with null payload
            json.dumps(
                {
                    "timestamp": _ts(1),
                    "type": "response_item",
                    "payload": None,
                }
            ),
            # text block with non-string text value
            _resp(
                2,
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": 123}],
                },
            ),
            # message with null content
            _resp(
                2,
                {"type": "message", "role": "user", "content": None},
            ),
            # non-dict top-level JSON line
            json.dumps(["not", "a", "dict"]),
            _resp(3, _msg("user", "Real question")),
            # function_call with null name and dict-free args
            _resp(
                4,
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": None,
                    "arguments": None,
                },
            ),
            _resp(5, _msg("assistant", "Real answer", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        # session_meta metadata (from the malformed-but-tolerable
        # record): cwd kept, branch unknown
        assert out[0]["cwd"] == str(project_dir)
        assert out[0]["gitBranch"] == ""
        assert out[0]["continue_metadata"]["parent_session_id"] == (
            MODERN_UUID
        )
        texts = [ln["message"]["content"][0]["text"] for ln in out]
        assert texts[0] == "Real question"
        # empty tool call still labeled, merged into assistant turn
        assert any("[codex tool call] unknown()" in t for t in texts)
        assert any("Real answer" in t for t in texts)

    def test_invalid_utf8_line_is_skipped(
        self, codex_home, claude_home, project_dir
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        # inject invalid UTF-8 bytes as an extra line
        with open(rollout, "ab") as f:
            f.write(b"\xff\xfe garbage bytes \xff\n")
            f.write(
                _resp(
                    12, _msg("assistant", "After bad bytes",
                             "output_text")
                ).encode("utf-8")
                + b"\n"
            )
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "After bad bytes" in raw


class TestStructuredToolValues:
    """Dict/list/scalar tool args and results are serialized."""

    def test_dict_args_and_results_serialized(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Run it")),
            _resp(
                2,
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "doit",
                    "arguments": {"b": 2, "a": 1},
                },
            ),
            _resp(
                3,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": {"ok": True, "n": 5},
                },
            ),
            _resp(
                4,
                {
                    "type": "function_call_output",
                    "call_id": "c2",
                    "output": [1, 2, 3],
                },
            ),
            _resp(
                5,
                {
                    "type": "function_call_output",
                    "call_id": "c3",
                    "output": 42,
                },
            ),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        text = "".join(
            block["text"]
            for line in _read_lines(out_path)
            for block in line["message"]["content"]
        )
        assert '[codex tool call] doit({"a": 1, "b": 2})' in text
        assert '[codex tool result] {"n": 5, "ok": true}' in text
        assert "[codex tool result] [1, 2, 3]" in text
        assert "[codex tool result] 42" in text


class TestToolResultTruncation:
    """Every flattened tool RESULT is capped with the exact suffix."""

    LONG_FN_OUTPUT = "z" * (TOOL_TEXT_CAP + 321)
    LONG_CUSTOM_OUTPUT = "w" * (TOOL_TEXT_CAP + 77)
    LONG_DICT_VALUE = "x" * (TOOL_TEXT_CAP + 400)

    @pytest.fixture
    def raw_output(self, codex_home, claude_home, project_dir):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Run tools")),
            _resp(
                2,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": self.LONG_FN_OUTPUT,
                },
            ),
            _resp(
                3,
                {
                    "type": "custom_tool_call_output",
                    "call_id": "c2",
                    "output": [
                        {
                            "type": "input_text",
                            "text": self.LONG_CUSTOM_OUTPUT,
                        }
                    ],
                },
            ),
            _resp(
                4,
                {
                    "type": "function_call_output",
                    "call_id": "c3",
                    "output": {"data": self.LONG_DICT_VALUE},
                },
            ),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return out_path.read_text(encoding="utf-8")

    def test_function_call_output_capped_with_exact_suffix(
        self, raw_output
    ):
        capped = "z" * TOOL_TEXT_CAP + "... [truncated 321 chars]"
        assert capped in raw_output
        # the original full value must not survive anywhere
        assert self.LONG_FN_OUTPUT not in raw_output
        assert "z" * (TOOL_TEXT_CAP + 1) not in raw_output

    def test_custom_tool_call_output_capped_with_exact_suffix(
        self, raw_output
    ):
        capped = "w" * TOOL_TEXT_CAP + "... [truncated 77 chars]"
        assert capped in raw_output
        assert self.LONG_CUSTOM_OUTPUT not in raw_output
        assert "w" * (TOOL_TEXT_CAP + 1) not in raw_output

    def test_structured_dict_output_capped_with_exact_suffix(
        self, raw_output
    ):
        # serialized form is '{"data": "xxx..."}': 12 framing chars
        # plus the value, so 412 chars beyond the cap are dropped.
        assert "... [truncated 412 chars]" in raw_output
        assert self.LONG_DICT_VALUE not in raw_output


class TestLongMergedTurnPreserved:
    """Merged same-role runs are preserved COMPLETELY: normal message
    text is never truncated, only tool args/results are capped."""

    def test_merged_run_over_100k_chars_survives_fully(
        self, codex_home, claude_home, project_dir
    ):
        chunk = 40_000
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "a" * chunk, "output_text")),
            _resp(3, _msg("assistant", "b" * chunk, "output_text")),
            _resp(4, _msg("assistant", "c" * chunk, "output_text")),
            _resp(
                5,
                _msg(
                    "assistant",
                    "FINAL-ANSWER-SENTINEL",
                    "output_text",
                ),
            ),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert len(out) == 2
        text = out[1]["message"]["content"][0]["text"]
        # every merged chunk survives verbatim, past the old 100k cap
        expected = (
            "a" * chunk
            + "\n\n"
            + "b" * chunk
            + "\n\n"
            + "c" * chunk
            + "\n\nFINAL-ANSWER-SENTINEL"
        )
        assert text == expected
        assert len(text) > 100_000
        assert "[truncated" not in text


class TestEncryptedAgentMessage:
    """Encrypted agent_message payloads are dropped entirely."""

    def test_mixed_plaintext_encrypted_agent_message_dropped(
        self, codex_home, claude_home, project_dir
    ):
        # Mirrors the ground-truth shape: a plaintext routing
        # envelope plus an encrypted_content block.
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Kick off subtask")),
            _resp(
                2,
                {
                    "type": "agent_message",
                    "author": "/root",
                    "recipient": "/root/appendix",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Message Type: NEW_TASK\n"
                                "Task name: /root/appendix\n"
                            ),
                        },
                        {
                            "type": "encrypted_content",
                            "encrypted_content": "ENCRYPTED-BLOB",
                        },
                    ],
                },
            ),
            _resp(
                3,
                {
                    "type": "agent_message",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Plain inter-agent note",
                        }
                    ],
                },
            ),
            _resp(4, _msg("assistant", "All done", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "Message Type: NEW_TASK" not in raw
        assert "ENCRYPTED-BLOB" not in raw
        # non-encrypted agent_message text is retained
        assert "Plain inter-agent note" in raw

    def test_payload_level_encrypted_agent_message_dropped(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(
                2,
                {
                    "type": "agent_message",
                    "encrypted_content": "TOP-LEVEL-BLOB",
                    "content": [
                        {"type": "input_text", "text": "wrapper text"}
                    ],
                },
            ),
            _resp(3, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "TOP-LEVEL-BLOB" not in raw
        assert "wrapper text" not in raw


class TestTrailingUserMessage:
    """A rollout ending on a user message must not end mid-pair."""

    def test_trailing_user_gets_synthetic_assistant_closer(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "First question")),
            _resp(2, _msg("assistant", "First answer", "output_text")),
            _resp(3, _msg("user", "Unanswered follow-up")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert len(out) == 4
        assert out[-2]["type"] == "user"
        assert (
            out[-2]["message"]["content"][0]["text"]
            == "Unanswered follow-up"
        )
        # synthetic assistant closer completes the pair
        assert out[-1]["type"] == "assistant"
        assert MODERN_UUID in out[-1]["message"]["content"][0]["text"]
        # strict alternation still holds
        for i, line in enumerate(out):
            assert line["type"] == (
                "user" if i % 2 == 0 else "assistant"
            )


class TestMixedBlockUserMessages:
    """Wrapper noise is classified per content block, not per message."""

    def _port_texts(self, codex_home, claude_home, project_dir, content):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(
                1,
                {"type": "message", "role": "user", "content": content},
            ),
            _resp(2, _msg("assistant", "Answer", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        raw = out_path.read_text(encoding="utf-8")
        return out, raw

    def test_wrapper_first_block_genuine_second_block_kept(
        self, codex_home, claude_home, project_dir
    ):
        content = [
            {
                "type": "input_text",
                "text": (
                    "<recommended_plugins>stuff</recommended_plugins>"
                ),
            },
            {"type": "input_text", "text": "Real request after wrapper"},
        ]
        out, raw = self._port_texts(
            codex_home, claude_home, project_dir, content
        )
        assert out[0]["type"] == "user"
        assert (
            out[0]["message"]["content"][0]["text"]
            == "Real request after wrapper"
        )
        assert "recommended_plugins" not in raw

    def test_wrapper_in_later_block_dropped(
        self, codex_home, claude_home, project_dir
    ):
        content = [
            {"type": "input_text", "text": "Genuine leading ask"},
            {
                "type": "input_text",
                "text": (
                    "<environment_context>\n  <cwd>/x</cwd>\n"
                    "</environment_context>"
                ),
            },
        ]
        out, raw = self._port_texts(
            codex_home, claude_home, project_dir, content
        )
        assert (
            out[0]["message"]["content"][0]["text"]
            == "Genuine leading ask"
        )
        assert "environment_context" not in raw

    def test_all_wrapper_blocks_drop_whole_message(
        self, codex_home, claude_home, project_dir
    ):
        content = [
            {
                "type": "input_text",
                "text": "<user_instructions>obey</user_instructions>",
            },
            {
                "type": "input_text",
                "text": "<turn_aborted>gone</turn_aborted>",
            },
        ]
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(
                1,
                {"type": "message", "role": "user", "content": content},
            ),
            _resp(2, _msg("user", "Real question")),
            _resp(3, _msg("assistant", "Answer", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert (
            out[0]["message"]["content"][0]["text"] == "Real question"
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "user_instructions" not in raw
        assert "turn_aborted" not in raw


class TestToolResultsAlwaysEmitted:
    """Tool-result records must NEVER be silently dropped.

    Absent, null, empty-string, and whitespace-only outputs are all
    represented with an explicit placeholder, for both
    function_call_output and custom_tool_call_output.
    """

    def _port_raw(self, codex_home, claude_home, project_dir, items):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Run the tools")),
        ]
        lines += [_resp(2 + i, item) for i, item in enumerate(items)]
        lines.append(
            _resp(30, _msg("assistant", "Wrapped up", "output_text"))
        )
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return out_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "out_type",
        ["function_call_output", "custom_tool_call_output"],
    )
    def test_absent_and_null_output_emit_no_output(
        self, codex_home, claude_home, project_dir, out_type
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            [
                {"type": out_type, "call_id": "c1"},  # absent
                {"type": out_type, "call_id": "c2", "output": None},
            ],
        )
        assert raw.count("[codex tool result] (no output)") == 2

    @pytest.mark.parametrize(
        "out_type",
        ["function_call_output", "custom_tool_call_output"],
    )
    def test_empty_and_whitespace_output_emit_empty_output(
        self, codex_home, claude_home, project_dir, out_type
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            [
                {"type": out_type, "call_id": "c1", "output": ""},
                {
                    "type": out_type,
                    "call_id": "c2",
                    "output": "  \n\t ",
                },
            ],
        )
        assert raw.count("[codex tool result] (empty output)") == 2


class TestEmptyEncryptedContentField:
    """PRESENCE of encrypted_content marks a payload encrypted, even
    when its value is empty or null."""

    @pytest.mark.parametrize("value", ["", None])
    def test_agent_message_with_empty_encrypted_field_dropped(
        self, codex_home, claude_home, project_dir, value
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(
                2,
                {
                    "type": "agent_message",
                    "encrypted_content": value,
                    "content": [
                        {
                            "type": "input_text",
                            "text": "leaky plaintext envelope",
                        }
                    ],
                },
            ),
            _resp(3, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "leaky plaintext envelope" not in raw


class TestLegacyHeaderAfterAuthoritativeMeta:
    """A legacy-looking header AFTER the authoritative session_meta
    must not overwrite source_id / branch / timestamp."""

    def test_late_legacy_header_ignored(
        self, codex_home, claude_home, project_dir
    ):
        stray_header = json.dumps(
            {
                "id": "99999999-9999-4999-8999-999999999999",
                "timestamp": "2020-01-01T00:00:00.000Z",
                "git": {"branch": "evil-branch"},
                "instructions": None,
            }
        )
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir), "main"),
            _resp(1, _msg("user", "Q")),
            stray_header,
            _resp(2, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        cm = out[0]["continue_metadata"]
        assert cm["parent_session_id"] == MODERN_UUID
        assert out[0]["gitBranch"] == "main"
        raw = out_path.read_text(encoding="utf-8")
        assert "evil-branch" not in raw
        assert "99999999-9999-4999-8999-999999999999" not in raw


class TestLongSingleMessagePreserved:
    """A single normal message longer than the old 100k cap survives
    verbatim: content after the 100,000-char boundary is retained."""

    @pytest.mark.parametrize("role", ["user", "assistant"])
    def test_content_after_100k_boundary_survives(
        self, codex_home, claude_home, project_dir, role
    ):
        body = "x" * 100_001 + "PAST-BOUNDARY-SENTINEL"
        block = "input_text" if role == "user" else "output_text"
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "A", "output_text")),
        ]
        if role == "user":
            lines.append(_resp(3, _msg("user", body, block)))
            lines.append(
                _resp(4, _msg("assistant", "Done", "output_text"))
            )
        else:
            lines[2] = _resp(2, _msg("assistant", body, block))
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        long_lines = [
            ln
            for ln in out
            if ln["message"]["content"][0]["text"] == body
        ]
        assert len(long_lines) == 1
        assert long_lines[0]["type"] == role
        text = long_lines[0]["message"]["content"][0]["text"]
        assert text.endswith("PAST-BOUNDARY-SENTINEL")
        assert "[truncated" not in text


class TestMalformedMetaViaCLI:
    """A malformed-but-tolerable session_meta must not make the CLI
    session-id lookup report 'Session not found'."""

    def _malformed_rollout(self, codex_home, project_dir):
        lines = [
            # null payload: previously crashed metadata extraction
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "session_meta",
                    "payload": None,
                }
            ),
            # null git value in an otherwise fine record
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "session_meta",
                    "payload": {
                        "id": MODERN_UUID,
                        "cwd": str(project_dir),
                        "git": None,
                    },
                }
            ),
            _resp(1, _msg("user", "Malformed-meta question")),
            _resp(2, _msg("assistant", "Answer", "output_text")),
        ]
        return write_rollout_lines(codex_home, MODERN_UUID, lines)

    def test_cli_port_by_session_id(
        self, codex_home, claude_home, project_dir
    ):
        self._malformed_rollout(codex_home, project_dir)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "port",
                MODERN_UUID,
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Session not found" not in result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )

    def test_shared_finder_returns_rollout(
        self, codex_home, claude_home, project_dir
    ):
        from claude_code_tools.session_utils import (
            extract_session_metadata_codex,
            find_session_file,
        )

        rollout = self._malformed_rollout(codex_home, project_dir)
        metadata = extract_session_metadata_codex(rollout)
        assert metadata is not None
        assert metadata["cwd"] == str(project_dir)
        result = find_session_file(
            MODERN_UUID,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert result is not None
        agent, found_file, _, _ = result
        assert agent == "codex"
        assert found_file == rollout


class TestExtractFirstLastNullPayload:
    """extract_first_last_messages must skip null/invalid payloads."""

    def test_null_payload_response_item_skipped(self, tmp_path):
        from claude_code_tools.export_session import (
            extract_first_last_messages,
        )

        lines = [
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "response_item",
                    "payload": None,
                }
            ),
            json.dumps(
                {
                    "timestamp": _ts(1),
                    "type": "response_item",
                    "payload": "not-a-dict",
                }
            ),
            _resp(2, _msg("user", "Real first message")),
            _resp(3, _msg("assistant", "Real answer", "output_text")),
        ]
        path = tmp_path / "null-payload.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        first, last, _ = extract_first_last_messages(path, "codex")
        assert first is not None
        assert first["content"] == "Real first message"
        assert last is not None
        assert last["content"] == "Real answer"


class TestDetectAgentBoundedRead:
    """detect_agent_from_content must stay memory-bounded."""

    def test_huge_unterminated_line_skipped(self, tmp_path):
        path = tmp_path / "huge-line.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"garbage": "' + "x" * 2_500_000 + '"}\n')
            f.write(
                json.dumps(
                    {
                        "timestamp": _ts(0),
                        "type": "session_meta",
                        "payload": {"id": MODERN_UUID},
                    }
                )
                + "\n"
            )
        # oversized first line is discarded in bounded chunks; the
        # codex line after it is still recognized
        assert detect_agent_from_content(path) == "codex"

    def test_pathological_nesting_skipped(self, tmp_path):
        depth = 200_000
        path = tmp_path / "nested.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write("[" * depth + "]" * depth + "\n")
            f.write(json.dumps({"record_type": "state"}) + "\n")
        assert detect_agent_from_content(path) == "codex"


class TestDetectAgentFromContent:
    """Content sniffing prioritizes line types over a sessionId key."""

    def test_codex_line_with_null_sessionid_is_codex(self, tmp_path):
        line = {
            "sessionId": None,
            "timestamp": _ts(0),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            },
        }
        path = tmp_path / "copied-response-item.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        assert detect_agent_from_content(path) == "codex"

    def test_claude_line_requires_non_empty_string_sessionid(
        self, tmp_path
    ):
        line = {
            "sessionId": str(uuid.uuid4()),
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "uuid": str(uuid.uuid4()),
        }
        path = tmp_path / "claude-line.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        assert detect_agent_from_content(path) == "claude"

    def test_null_sessionid_alone_is_not_claude(self, tmp_path):
        line = {"sessionId": None, "foo": 1}
        path = tmp_path / "null-sid.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        assert detect_agent_from_content(path) is None
