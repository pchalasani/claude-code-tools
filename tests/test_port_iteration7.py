"""Regression tests for the iteration-7 adversarial-review fixes.

Covers: tolerance of records whose top-level ``type`` is a non-string
JSON shape (list/dict), encrypted blocks stored as dict VALUES (not
just list elements), depth/cycle bounds on the encrypted-content
strip, NUL-byte cwd fallback, bounded strip-then-truncate of huge
whitespace-padded tool values, content-first agent detection for
direct file paths in the CLI, and export_session metadata hardening
(timestamp shapes, undecodable bytes, non-string Codex roles).

Split into its own file to keep the sibling port test files under the
repo's 1000-line limit. Shares fixture helpers via imports from
tests.test_port_session.
"""

import json
import shutil
import uuid

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.export_session import (
    _get_last_line_timestamp,
    extract_first_last_messages,
    extract_session_metadata,
)
from claude_code_tools.port_codex_to_claude import (
    TOOL_TEXT_CAP,
    port_codex_session_to_claude,
)
from claude_code_tools.session_utils import (
    detect_agent_from_content,
    is_valid_session,
)
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


@pytest.fixture
def runner():
    return CliRunner()


def _combined(result):
    try:
        stderr = result.stderr
    except ValueError:  # older click: stderr mixed into output
        stderr = ""
    return result.output + stderr


class TestNonStringTypeRecords:
    """Records whose ``type`` is a list/dict are skipped, not crashes.

    Unhashable ``type`` values used to raise ``TypeError`` at the
    frozenset membership tests in the converter, in
    detect_agent_from_content, and in is_valid_session.
    """

    def test_converter_ports_around_non_string_types(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            json.dumps({"type": [], "payload": {}}),
            json.dumps({"type": {"weird": 1}, "timestamp": _ts(1)}),
            _resp(3, _msg("user", "Q survives")),
            json.dumps({"type": ["response_item"], "payload": {}}),
            json.dumps({"type": 42, "id": "x", "timestamp": _ts(4)}),
            _resp(5, _msg("assistant", "A survives", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        texts = [
            ln["message"]["content"][0]["text"]
            for ln in _read_lines(out_path)
        ]
        assert texts == ["Q survives", "A survives"]

    def test_detect_agent_skips_non_string_types(self, tmp_path):
        path = tmp_path / "nonstring-type.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": []}) + "\n")
            f.write(json.dumps({"type": {"a": 1}}) + "\n")
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
        assert detect_agent_from_content(path) == "codex"

    def test_detect_agent_only_non_string_types_is_none(self, tmp_path):
        path = tmp_path / "only-nonstring.jsonl"
        path.write_text(
            json.dumps({"type": [], "sessionId": "sid"}) + "\n",
            encoding="utf-8",
        )
        assert detect_agent_from_content(path) is None

    def test_is_valid_session_skips_non_string_types(self, tmp_path):
        path = tmp_path / "valid-after-bad-type.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": ["user"], "sessionId": "s"}) + "\n")
            f.write(
                json.dumps({"type": "user", "sessionId": "sid-1"}) + "\n"
            )
        assert is_valid_session(path) is True

    @pytest.mark.parametrize("bad_sid", [[1], {"a": 1}, 7, True, ""])
    def test_is_valid_session_requires_string_session_id(
        self, tmp_path, bad_sid
    ):
        path = tmp_path / "bad-sid.jsonl"
        path.write_text(
            json.dumps({"type": "user", "sessionId": bad_sid}) + "\n",
            encoding="utf-8",
        )
        assert is_valid_session(path) is False


class TestEncryptedDictValues:
    """Encrypted blocks stored as dict VALUES are dropped everywhere.

    Regression: ``_strip_encrypted`` used to remove encrypted dicts
    only when they were direct list elements, so
    ``{"nested": {"type": "encrypted_content", ...}}`` leaked its
    ciphertext into the transcript.
    """

    def _port_raw(self, codex_home, claude_home, project_dir, items):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            *[_resp(2 + i, item) for i, item in enumerate(items)],
            _resp(9, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return out_path.read_text(encoding="utf-8")

    def test_encrypted_dict_value_in_args_and_output_dropped(
        self, codex_home, claude_home, project_dir
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "doit",
                    "arguments": {
                        "nested": {
                            "type": "encrypted_content",
                            "ciphertext": "SECRET-ARGS",
                        },
                        "keep": "PLAIN-SIBLING",
                    },
                },
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": {
                        "outer": {
                            "type": "encrypted_content",
                            "ciphertext": "SECRET-OUT",
                        },
                        "ok": "VISIBLE-RESULT",
                    },
                },
            ],
        )
        assert "SECRET-ARGS" not in raw
        assert "SECRET-OUT" not in raw
        assert "ciphertext" not in raw
        assert "encrypted_content" not in raw
        # non-encrypted sibling values survive
        assert "PLAIN-SIBLING" in raw
        assert "VISIBLE-RESULT" in raw

    def test_deeply_nested_encrypted_dict_value_dropped(
        self, codex_home, claude_home, project_dir
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            [
                {
                    "type": "custom_tool_call",
                    "call_id": "c2",
                    "name": "t",
                    "input": {
                        "a": {
                            "b": [
                                {
                                    "c": {
                                        "type": "encrypted_content",
                                        "ciphertext": "DEEP-SECRET",
                                    }
                                }
                            ]
                        },
                        "plain": "STILL-HERE",
                    },
                },
            ],
        )
        assert "DEEP-SECRET" not in raw
        assert "encrypted_content" not in raw
        assert "STILL-HERE" in raw


class TestStripBounds:
    """Depth-bounded, cycle-safe strip; bounded string truncation."""

    def test_pathologically_nested_tool_args_do_not_crash(
        self, codex_home, claude_home, project_dir
    ):
        # 600-deep valid JSON: parseable, but the old recursive strip
        # (one stack frame per level, unbounded) could overflow the
        # stack. The bounded strip conservatively drops content nested
        # beyond its depth cap while keeping shallow siblings.
        deep_json = "[" * 600 + '"DEEP-LEAF"' + "]" * 600
        raw_line = (
            '{"timestamp": "' + _ts(2) + '", "type": "response_item", '
            '"payload": {"type": "function_call", "call_id": "c9", '
            '"name": "t", "arguments": {"deep": ' + deep_json + ', '
            '"plain": "SHALLOW-OK"}}}'
        )
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            raw_line,
            _resp(3, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "SHALLOW-OK" in raw
        assert "DEEP-LEAF" not in raw

    def test_huge_whitespace_padded_output_truncated(
        self, codex_home, claude_home, project_dir
    ):
        padded = " " * 50_000 + "z" * (TOOL_TEXT_CAP + 100) + " " * 50_000
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(
                2,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": padded,
                },
            ),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert (
            "[codex tool result] " + "z" * TOOL_TEXT_CAP
            + "... [truncated 100 chars]"
        ) in raw
        # padding stripped, not counted as truncated content
        assert "z" * (TOOL_TEXT_CAP + 1) not in raw

    def test_all_whitespace_output_is_empty_output(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(
                2,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": " " * 10_000,
                },
            ),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "[codex tool result] (empty output)" in raw


class TestNulByteCwd:
    """A harvested cwd containing NUL falls back to the current dir
    instead of crashing ``mkdir`` with ``ValueError``."""

    def test_nul_cwd_falls_back_to_current_dir(
        self, codex_home, claude_home, tmp_path, monkeypatch
    ):
        lines = [
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "session_meta",
                    "payload": {
                        "id": MODERN_UUID,
                        "cwd": "/tmp/evil\x00dir",
                        "git": {"branch": "main"},
                    },
                }
            ),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        run_dir = tmp_path / "rundir"
        run_dir.mkdir()
        monkeypatch.chdir(run_dir)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(run_dir)
        assert "\x00" not in str(out_path)


class TestCLIDirectPathContentFirst:
    """Direct file paths are classified by CONTENT before location."""

    def _invoke(self, runner, args, claude_home, codex_home):
        return runner.invoke(
            main,
            [
                *args,
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
            ],
        )

    def test_malformed_file_in_claude_home_errors_nonzero(
        self, runner, codex_home, claude_home
    ):
        proj = claude_home / "projects" / "-tmp-x"
        proj.mkdir(parents=True)
        bad = proj / f"{uuid.uuid4()}.jsonl"
        bad.write_text("this is not json at all\n", encoding="utf-8")
        result = self._invoke(
            runner, ["port", str(bad)], claude_home, codex_home
        )
        assert result.exit_code != 0
        combined = _combined(result)
        assert "Could not detect agent" in combined
        # never misreported as a portable Claude session
        assert "porting to Codex" not in result.output
        assert "/import" not in result.output

    def test_codex_rollout_copied_under_claude_home_is_codex(
        self, runner, codex_home, claude_home, project_dir
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        proj = claude_home / "projects" / "-tmp-fake"
        proj.mkdir(parents=True)
        copied = proj / f"{uuid.uuid4()}.jsonl"
        shutil.copy(rollout, copied)
        result = self._invoke(
            runner, ["port", str(copied)], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )
        assert "New Claude session id:" in result.output
        assert "/import" not in result.output

    def test_claude_session_direct_path_still_prints_import(
        self, runner, codex_home, claude_home, project_dir
    ):
        sid = str(uuid.uuid4())
        proj = claude_home / "projects" / "-tmp-real"
        proj.mkdir(parents=True)
        line = {
            "type": "user",
            "sessionId": sid,
            "cwd": str(project_dir),
            "message": {"role": "user", "content": "hello"},
            "uuid": str(uuid.uuid4()),
            "timestamp": _ts(0),
        }
        path = proj / f"{sid}.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        result = self._invoke(
            runner, ["port", str(path)], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: claude — porting to Codex"
            in result.output
        )
        assert "/import" in result.output


class TestExportSessionHardening:
    """Metadata/message extraction never crashes on hostile records."""

    def test_last_line_huge_int_returns_none(self, tmp_path):
        path = tmp_path / "huge-last.jsonl"
        path.write_text(
            json.dumps({"type": "user", "timestamp": _ts(0)})
            + "\n"
            + '{"n": '
            + "1" * 5000
            + "}\n",
            encoding="utf-8",
        )
        assert _get_last_line_timestamp(path) is None

    @pytest.mark.parametrize(
        "bad_ts", [{"weird": 1}, [1, 2], 42, True, ""]
    )
    def test_last_line_non_string_timestamp_ignored(
        self, tmp_path, bad_ts
    ):
        path = tmp_path / "bad-ts.jsonl"
        path.write_text(
            json.dumps({"timestamp": bad_ts}) + "\n", encoding="utf-8"
        )
        assert _get_last_line_timestamp(path) is None
        # metadata falls back to file mtime -- always a string
        meta = extract_session_metadata(path, "claude")
        assert isinstance(meta["modified"], str) and meta["modified"]

    def test_undecodable_bytes_do_not_crash_extraction(self, tmp_path):
        path = tmp_path / "bad-bytes.jsonl"
        with open(path, "wb") as f:
            f.write(b'{"type": "user", "sessionId": "sid-1", ')
            f.write(b'"cwd": "/tmp/x", "timestamp": "' + b"\xff\xfe")
            f.write(b'"}\n')
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "sid-1",
                        "cwd": "/tmp/x",
                        "message": {
                            "role": "assistant",
                            "content": "fine",
                        },
                        "timestamp": _ts(1),
                    }
                ).encode("utf-8")
                + b"\n"
            )
        meta = extract_session_metadata(path, "claude")
        assert meta["cwd"] == "/tmp/x"
        first, last, _ = extract_first_last_messages(path, "claude")
        assert last is not None and last["content"] == "fine"

    @pytest.mark.parametrize(
        "bad_role", [["user"], {"role": "user"}, 5, True]
    )
    def test_non_string_codex_roles_skipped(self, tmp_path, bad_role):
        path = tmp_path / "bad-role.jsonl"
        lines = [
            _resp(
                1,
                {
                    "type": "message",
                    "role": bad_role,
                    "content": [
                        {"type": "input_text", "text": "bad role text"}
                    ],
                },
            ),
            _resp(2, _msg("user", "good question")),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        first, last, first_user = extract_first_last_messages(
            path, "codex"
        )
        assert first is not None
        assert first["role"] == "user"
        assert first["content"] == "good question"
        assert last["role"] == "user"
