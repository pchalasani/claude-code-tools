"""Hardening tests for `aichat port` and its shared helpers.

Covers the adversarial-review findings: real legacy-2025 cwd
harvesting, Codex wrapper-tag filtering (including the injected
"# AGENTS.md instructions" block), malformed top-level JSONL shapes,
validated session-id filename matching, and atomic output writes.

Split into its own file to keep tests/test_port_session.py and
tests/test_port_edge_cases.py under the repo's 1000-line limit.
Shares fixture helpers via imports from tests.test_port_session.
"""

import json
import os
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.export_session import (
    _is_meta_text,
    extract_first_last_messages,
    extract_session_metadata,
)
from claude_code_tools.port_codex_to_claude import (
    TOOL_TEXT_CAP,
    _write_transcript_atomic,
    port_codex_session_to_claude,
)
from claude_code_tools.session_utils import (
    encode_claude_project_path,
    find_matching_session_files,
    find_session_file,
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


def _invoke_port(runner, args, claude_home, codex_home):
    return runner.invoke(
        main,
        [
            "port",
            *args,
            "--claude-home",
            str(claude_home),
            "--codex-home",
            str(codex_home),
        ],
    )


class TestEnvironmentContextCwdFormats:
    """cwd harvesting supports BOTH environment-context encodings."""

    def _port_env_context(
        self, codex_home, claude_home, env_text
    ) -> Path:
        # No session_meta line: the cwd can only come from the
        # injected environment-context user message.
        lines = [
            _resp(1, _msg("user", env_text)),
            _resp(2, _msg("user", "Question")),
            _resp(3, _msg("assistant", "Answer", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return out_path

    def test_real_legacy_current_working_directory_line(
        self, codex_home, claude_home, project_dir
    ):
        """Real 2025 rollouts state the cwd as a plain line."""
        env_text = (
            "<environment_context>\n"
            f"Current working directory: {project_dir}\n"
            "Approval policy: on-request\n"
            "Sandbox mode: read-only\n"
            "Network access: restricted\n"
            "</environment_context>"
        )
        out_path = self._port_env_context(
            codex_home, claude_home, env_text
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(project_dir)
        assert out_path.parent.name == encode_claude_project_path(
            str(project_dir)
        )

    def test_cwd_tag_still_supported(
        self, codex_home, claude_home, project_dir
    ):
        """Newer environment contexts wrap the cwd in a tag."""
        env_text = (
            "<environment_context>\n"
            f"  <cwd>{project_dir}</cwd>\n"
            "  <approval_policy>on-request</approval_policy>\n"
            "</environment_context>"
        )
        out_path = self._port_env_context(
            codex_home, claude_home, env_text
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(project_dir)

    def test_cwd_with_spaces_in_legacy_line(
        self, codex_home, claude_home, tmp_path
    ):
        spaced = tmp_path / "my proj dir"
        spaced.mkdir()
        env_text = (
            "<environment_context>\n"
            f"Current working directory: {spaced}\n"
            "</environment_context>"
        )
        out_path = self._port_env_context(
            codex_home, claude_home, env_text
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(spaced)


# Every wrapper marker Codex injects into user-role content in the
# ground-truth modern rollout format.
WRAPPER_BLOCKS = [
    "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>",
    "<permissions instructions>\nFilesystem sandboxing on",
    "<user_instructions>obey</user_instructions>",
    "<turn_aborted>gone</turn_aborted>",
    "<recommended_plugins>\nHere is a list of plugins",
    "<skills_instructions>\n## Skills\nA skill is a set of",
    "<apps_instructions>\n## Apps (Connectors)\nApps are",
    "<plugins_instructions>\n## Plugins\nA plugin is a set",
    "<multi_agent_mode>Do not spawn sub-agents unless told",
    "# AGENTS.md instructions\n\n<INSTRUCTIONS>\n# STARTING\nrepo rules",
]

WRAPPER_MARKERS = [
    "environment_context",
    "permissions instructions",
    "user_instructions",
    "turn_aborted",
    "recommended_plugins",
    "skills_instructions",
    "apps_instructions",
    "plugins_instructions",
    "multi_agent_mode",
    "AGENTS.md instructions",
]


class TestWrapperBlockFiltering:
    """All known Codex-injected wrapper blocks are filtered out."""

    @pytest.mark.parametrize("wrapper", WRAPPER_BLOCKS)
    def test_is_meta_text_recognizes_each_wrapper(self, wrapper):
        assert _is_meta_text(wrapper) is True

    def test_genuine_heading_text_is_not_filtered(self):
        assert _is_meta_text("# AGENTS overview\nnormal doc") is False
        assert _is_meta_text("Please read AGENTS.md first") is False

    def test_mixed_block_message_keeps_only_genuine_text(
        self, codex_home, claude_home, project_dir
    ):
        """One user message mixing EVERY wrapper block with genuine
        text: each wrapper is removed, the genuine request stays."""
        genuine = "Remove process-history leakage from the paper"
        content = [
            {"type": "input_text", "text": w} for w in WRAPPER_BLOCKS
        ]
        content.insert(
            len(content) // 2, {"type": "input_text", "text": genuine}
        )
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(
                1,
                {"type": "message", "role": "user", "content": content},
            ),
            _resp(2, _msg("assistant", "Done", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert out[0]["type"] == "user"
        assert out[0]["message"]["content"][0]["text"] == genuine
        raw = out_path.read_text(encoding="utf-8")
        for marker in WRAPPER_MARKERS:
            assert marker not in raw, marker

    def test_ground_truth_shaped_initial_user_record(
        self, codex_home, claude_home, project_dir
    ):
        """Mirrors the real modern rollout: the initial user record
        carries recommended_plugins + AGENTS.md + environment_context
        blocks; the genuine first request follows in its own record.
        The synthesized transcript must START with the genuine
        request, not repository instructions."""
        injected = {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<recommended_plugins>\nHere is a list of "
                        "plugins that are available\n"
                        "</recommended_plugins>"
                    ),
                },
                {
                    "type": "input_text",
                    "text": (
                        "# AGENTS.md instructions\n\n<INSTRUCTIONS>\n"
                        "# STARTING\n- repo instructions here\n"
                        "</INSTRUCTIONS>"
                    ),
                },
                {
                    "type": "input_text",
                    "text": (
                        "<environment_context>\n"
                        f"  <cwd>{project_dir}</cwd>\n"
                        "</environment_context>"
                    ),
                },
            ],
        }
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, injected),
            _resp(2, _msg("user", "Fix the flaky test please")),
            _resp(3, _msg("assistant", "On it", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert out[0]["type"] == "user"
        assert (
            out[0]["message"]["content"][0]["text"]
            == "Fix the flaky test please"
        )
        raw = out_path.read_text(encoding="utf-8")
        assert "AGENTS.md instructions" not in raw
        assert "repo instructions here" not in raw
        assert "recommended_plugins" not in raw


class TestMalformedTopLevelShapes:
    """Valid-JSON non-dict records must never crash the extractors."""

    def _write(self, tmp_path, lines) -> Path:
        path = tmp_path / "weird-shapes.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_non_dict_records_skipped_claude(self, tmp_path):
        sid = str(uuid.uuid4())
        genuine = {
            "type": "user",
            "sessionId": sid,
            "cwd": "/tmp/x",
            "gitBranch": "main",
            "timestamp": _ts(1),
            "message": {"role": "user", "content": "Real question"},
        }
        answer = {
            "type": "assistant",
            "sessionId": sid,
            "timestamp": _ts(2),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Real answer"}],
            },
        }
        lines = [
            "null",
            "[]",
            "1",
            '"just a string"',
            # message present but null: must not crash text extraction
            json.dumps({"type": "user", "message": None}),
            json.dumps(genuine),
            json.dumps(answer),
            "[]",
        ]
        path = self._write(tmp_path, lines)
        first, last, _ = extract_first_last_messages(path, "claude")
        assert first is not None
        assert first["content"] == "Real question"
        assert last is not None
        assert last["content"] == "Real answer"
        meta = extract_session_metadata(path, "claude")
        assert meta["cwd"] == "/tmp/x"
        assert meta["session_id"] == sid

    def test_non_dict_records_skipped_codex(self, tmp_path):
        lines = [
            "null",
            "[1, 2]",
            _session_meta(0, MODERN_UUID, "/tmp/proj"),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "A", "output_text")),
            "0",
        ]
        path = self._write(tmp_path, lines)
        first, last, _ = extract_first_last_messages(path, "codex")
        assert first is not None and first["content"] == "Q"
        assert last is not None and last["content"] == "A"
        meta = extract_session_metadata(path, "codex")
        assert meta["cwd"] == "/tmp/proj"

    @pytest.mark.parametrize("bad", [None, "nope", 3, [1]])
    def test_non_dict_trim_and_continue_metadata(self, tmp_path, bad):
        sid = str(uuid.uuid4())
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": sid,
                    "cwd": "/tmp/y",
                    "trim_metadata": bad,
                    "continue_metadata": bad,
                    "message": {"role": "user", "content": "hi"},
                }
            ),
        ]
        path = self._write(tmp_path, lines)
        meta = extract_session_metadata(path, "claude")
        # invalid lineage shapes are ignored, not crashed on
        assert meta["derivation_type"] is None
        assert meta["parent_session_id"] is None
        assert meta["cwd"] == "/tmp/y"

    def test_dict_continue_metadata_still_recognized(self, tmp_path):
        sid = str(uuid.uuid4())
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": sid,
                    "cwd": "/tmp/z",
                    "continue_metadata": {
                        "parent_session_id": "parent-id",
                        "parent_session_file": "/tmp/p.jsonl",
                    },
                    "message": {"role": "user", "content": "hi"},
                }
            ),
        ]
        path = self._write(tmp_path, lines)
        meta = extract_session_metadata(path, "claude")
        assert meta["derivation_type"] == "continued"
        assert meta["parent_session_id"] == "parent-id"


class TestValidatedSessionIdMatching:
    """Filename matches are validated before they count as sessions."""

    def _write_malformed_claude(self, claude_home, name) -> Path:
        proj = claude_home / "projects" / "-tmp-garbage"
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{name}.jsonl"
        path.write_text("this is not json at all\n", encoding="utf-8")
        return path

    def test_malformed_claude_match_reports_not_found(
        self, runner, claude_home, codex_home
    ):
        sid = "deadbeef-1111-4111-8111-111111111111"
        self._write_malformed_claude(claude_home, sid)
        result = _invoke_port(runner, [sid], claude_home, codex_home)
        assert result.exit_code != 0
        try:
            stderr = result.stderr
        except ValueError:
            stderr = ""
        combined = result.output + stderr
        assert "Session not found in Claude or Codex" in combined
        # it must NOT be misreported as a Claude source
        assert "porting to Codex" not in combined

    def test_metadata_only_claude_match_reports_not_found(
        self, runner, claude_home, codex_home
    ):
        """A valid-JSON but non-resumable Claude file (metadata-only)
        is rejected just like the info/copy lookup rejects it."""
        sid = "deadbeef-2222-4222-8222-222222222222"
        proj = claude_home / "projects" / "-tmp-metaonly"
        proj.mkdir(parents=True)
        (proj / f"{sid}.jsonl").write_text(
            json.dumps({"type": "file-history-snapshot", "id": sid})
            + "\n",
            encoding="utf-8",
        )
        result = _invoke_port(runner, [sid], claude_home, codex_home)
        assert result.exit_code != 0

    def test_malformed_claude_does_not_shadow_valid_codex(
        self, runner, claude_home, codex_home, project_dir
    ):
        """A garbage Claude-home file sharing the id must neither win
        detection nor make the valid Codex session ambiguous."""
        write_modern_rollout(codex_home, project_dir)
        self._write_malformed_claude(claude_home, MODERN_UUID)
        result = _invoke_port(
            runner, [MODERN_UUID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )

    def test_malformed_codex_file_is_ignored(
        self, runner, claude_home, codex_home, project_dir
    ):
        """A garbage file in the Codex home sharing an id fragment
        must not make the one valid rollout ambiguous."""
        write_modern_rollout(codex_home, project_dir)
        day_dir = codex_home / "sessions" / "2026" / "07" / "16"
        garbage_id = MODERN_UUID[:-3] + "fff"
        (day_dir / f"rollout-2026-07-16T20-00-00-{garbage_id}.jsonl").write_text(
            "garbage bytes, not a rollout\n", encoding="utf-8"
        )
        # fragment shared by both files
        fragment = MODERN_UUID[:20]
        result = _invoke_port(
            runner, [fragment], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert "Ambiguous" not in result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )

    def test_finder_returns_only_validated_matches(
        self, claude_home, codex_home, project_dir
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        self._write_malformed_claude(claude_home, MODERN_UUID)
        matches = find_matching_session_files(
            MODERN_UUID,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert matches == [("codex", rollout)]


class TestAtomicWrite:
    """The destination session file appears atomically or not at all."""

    def test_success_leaves_only_the_final_file(
        self, codex_home, claude_home, project_dir
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        assert list(out_path.parent.iterdir()) == [out_path]

    def test_raising_stream_leaves_no_partial_or_temp_file(
        self, tmp_path
    ):
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        out_path = dest_dir / "session.jsonl"

        def exploding_lines():
            yield {"type": "user", "uuid": "u1"}
            raise RuntimeError("mid-stream failure")

        with pytest.raises(RuntimeError):
            _write_transcript_atomic(out_path, exploding_lines())
        assert not out_path.exists()
        assert list(dest_dir.iterdir()) == []

    def test_unserializable_line_leaves_no_partial_or_temp_file(
        self, tmp_path
    ):
        dest_dir = tmp_path / "dest2"
        dest_dir.mkdir()
        out_path = dest_dir / "session.jsonl"
        lines = iter(
            [{"type": "user"}, {"type": "assistant", "bad": object()}]
        )
        with pytest.raises(TypeError):
            _write_transcript_atomic(out_path, lines)
        assert not out_path.exists()
        assert list(dest_dir.iterdir()) == []

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="permissions are not enforced as root"
    )
    def test_unwritable_destination_creates_nothing(
        self, codex_home, claude_home, project_dir
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        proj = (
            claude_home
            / "projects"
            / encode_claude_project_path(str(project_dir))
        )
        proj.mkdir(parents=True)
        proj.chmod(0o555)
        try:
            with pytest.raises(OSError):
                port_codex_session_to_claude(
                    rollout, claude_home=claude_home
                )
            assert list(proj.iterdir()) == []
        finally:
            proj.chmod(0o755)


class TestOversizedRecordsPorted:
    """A valid record far larger than any line bound is still ported.

    Regression: oversized JSONL records (e.g. a >10 MB tool result)
    used to be discarded wholesale instead of being flattened and
    truncated as the contract requires.
    """

    def test_over_10mb_tool_result_flattened_and_truncated(
        self, codex_home, claude_home, project_dir
    ):
        big_len = 10_000_100  # larger than the former 10 MB line cap
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Run the big tool")),
            _resp(
                2,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": "x" * big_len,
                },
            ),
            _resp(3, _msg("assistant", "Handled", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        suffix = f"... [truncated {big_len - TOOL_TEXT_CAP} chars]"
        assert (
            "[codex tool result] " + "x" * TOOL_TEXT_CAP + suffix
        ) in raw
        # the record was truncated, not kept whole nor dropped
        assert "x" * (TOOL_TEXT_CAP + 1) not in raw


class TestNestedEncryptedToolValues:
    """encrypted_content nested inside tool args/results never leaks.

    Regression: structured tool arguments/results used to be
    serialized directly, so nested encrypted_content fields leaked
    into the Claude transcript.
    """

    def test_encrypted_stripped_from_args_and_results(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(
                2,
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "doit",
                    "arguments": {
                        "query": "visible-arg",
                        "context": {
                            "encrypted_content": "ARGS-SECRET"
                        },
                    },
                },
            ),
            _resp(
                3,
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": {
                        "result": "visible-result",
                        "blocks": [
                            {
                                "type": "encrypted_content",
                                "encrypted_content": "OUT-SECRET-1",
                            },
                            {
                                "type": "note",
                                "encrypted_content": "OUT-SECRET-2",
                            },
                            {"type": "note", "text": "visible-note"},
                        ],
                    },
                },
            ),
            _resp(
                4,
                {
                    "type": "custom_tool_call_output",
                    "call_id": "c2",
                    "output": [
                        {
                            "type": "encrypted_content",
                            "encrypted_content": "ONLY-SECRET",
                        }
                    ],
                },
            ),
            _resp(5, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        raw = out_path.read_text(encoding="utf-8")
        for secret in (
            "ARGS-SECRET",
            "OUT-SECRET-1",
            "OUT-SECRET-2",
            "ONLY-SECRET",
        ):
            assert secret not in raw, secret
        # non-encrypted sibling values survive
        assert "visible-arg" in raw
        assert "visible-result" in raw
        assert "visible-note" in raw


class TestNonDictJsonLinesInClaudeSessions:
    """Valid non-object JSONL records (null / [] / "x" / 1) in a
    Claude session must be skipped by validation and lookup, never
    crash them."""

    def _write_claude_session(self, claude_home, sid, leading):
        proj = claude_home / "projects" / "-tmp-nondict"
        proj.mkdir(parents=True, exist_ok=True)
        genuine = {
            "type": "user",
            "sessionId": sid,
            "cwd": "/tmp/nondict",
            "gitBranch": "main",
            "timestamp": _ts(1),
            "message": {"role": "user", "content": "Real question"},
        }
        path = proj / f"{sid}.jsonl"
        path.write_text(
            "\n".join(leading + [json.dumps(genuine)]) + "\n",
            encoding="utf-8",
        )
        return path

    def test_is_valid_session_skips_non_dict_records(
        self, claude_home
    ):
        sid = str(uuid.uuid4())
        path = self._write_claude_session(
            claude_home, sid, ["null", "[]", '"just a string"', "1"]
        )
        assert is_valid_session(path) is True

    def test_only_non_dict_records_is_invalid_not_crash(
        self, claude_home
    ):
        proj = claude_home / "projects" / "-tmp-nondict"
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{uuid.uuid4()}.jsonl"
        path.write_text("null\n[]\n1\n", encoding="utf-8")
        assert is_valid_session(path) is False

    def test_finder_returns_session_with_leading_null(
        self, claude_home, codex_home
    ):
        sid = str(uuid.uuid4())
        path = self._write_claude_session(claude_home, sid, ["null"])
        matches = find_matching_session_files(
            sid,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert matches == [("claude", path)]
        # the shared lookup used by info/clone agrees
        result = find_session_file(
            sid,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert result is not None
        assert result[0] == "claude"
        assert result[1] == path

    def test_cli_port_converts_and_exits_zero(
        self, runner, claude_home, codex_home
    ):
        sid = str(uuid.uuid4())
        self._write_claude_session(claude_home, sid, ["null", "[]"])
        result = _invoke_port(runner, [sid], claude_home, codex_home)
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: claude — porting to Codex"
            in result.output
        )
        # the leading non-dict records are tolerated and the session
        # converts for real under the new contract
        assert "New Codex session id:" in result.output
        assert "codex resume " in result.output
        assert "/import" in result.output
        assert list(
            (codex_home / "sessions").rglob("rollout-*.jsonl")
        )


class TestCodexFirstUserDetection:
    """The first GENUINE codex user message is reported, even when it
    shares its timestamp with injected wrapper messages (single-turn
    sessions included)."""

    def _write(self, tmp_path, lines):
        path = tmp_path / "codex-first-user.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_single_turn_first_user_found(self, tmp_path):
        lines = [
            _session_meta(0, MODERN_UUID, "/tmp/proj"),
            # injected wrapper shares the genuine message's timestamp
            _resp(
                0,
                _msg(
                    "user",
                    "<user_instructions>obey</user_instructions>",
                ),
            ),
            _resp(0, _msg("user", "Genuine single question")),
            _resp(1, _msg("assistant", "Answer", "output_text")),
        ]
        path = self._write(tmp_path, lines)
        _, _, first_user = extract_first_last_messages(path, "codex")
        assert first_user is not None
        assert first_user["content"] == "Genuine single question"

    def test_multi_turn_reports_first_genuine_not_second(
        self, tmp_path
    ):
        lines = [
            _session_meta(0, MODERN_UUID, "/tmp/proj"),
            _resp(
                0,
                _msg(
                    "user",
                    "<environment_context>\n  <cwd>/x</cwd>\n"
                    "</environment_context>",
                ),
            ),
            _resp(0, _msg("user", "First real question")),
            _resp(1, _msg("assistant", "A1", "output_text")),
            _resp(5, _msg("user", "Second question")),
            _resp(6, _msg("assistant", "A2", "output_text")),
        ]
        path = self._write(tmp_path, lines)
        _, _, first_user = extract_first_last_messages(path, "codex")
        assert first_user is not None
        assert first_user["content"] == "First real question"


class TestClaudeNullTextBlock:
    """A Claude text block with null / non-string text must not crash
    message or metadata extraction."""

    def test_null_and_non_string_text_blocks_skipped(self, tmp_path):
        sid = str(uuid.uuid4())
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": sid,
                    "cwd": "/tmp/nulltext",
                    "timestamp": _ts(0),
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": None},
                            {"type": "text", "text": 42},
                            {"type": "text", "text": "Real question"},
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": sid,
                    "timestamp": _ts(1),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": None}],
                    },
                }
            ),
        ]
        path = tmp_path / "null-text.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        first, last, first_user = extract_first_last_messages(
            path, "claude"
        )
        assert first is not None
        assert first["content"] == "Real question"
        assert last is not None
        assert first_user is not None
        assert first_user["content"] == "Real question"
        meta = extract_session_metadata(path, "claude")
        assert meta["cwd"] == "/tmp/nulltext"


class TestHostileCwdFallback:
    """A string-valued but filesystem-hostile cwd falls back to the
    current directory instead of crashing after porting began."""

    def _port_with_cwd(self, codex_home, claude_home, cwd_value):
        lines = [
            json.dumps(
                {
                    "timestamp": _ts(0),
                    "type": "session_meta",
                    "payload": {
                        "id": MODERN_UUID,
                        "cwd": cwd_value,
                        "git": {"branch": "main"},
                    },
                }
            ),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        return port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )

    @pytest.mark.parametrize(
        "hostile_cwd",
        [
            "/tmp/bad\ud800dir",  # unpaired surrogate
            "/" + "a" * 300,  # overlong path component
        ],
        ids=["unpaired-surrogate", "overlong-component"],
    )
    def test_hostile_cwd_falls_back_to_current_dir(
        self,
        codex_home,
        claude_home,
        tmp_path,
        monkeypatch,
        hostile_cwd,
    ):
        run_dir = tmp_path / "rundir"
        run_dir.mkdir()
        monkeypatch.chdir(run_dir)
        _, out_path = self._port_with_cwd(
            codex_home, claude_home, hostile_cwd
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(run_dir)
        assert out_path.parent.name == encode_claude_project_path(
            str(run_dir)
        )
        # the hostile value never reaches the transcript
        raw = out_path.read_text(encoding="utf-8")
        assert "a" * 300 not in raw
