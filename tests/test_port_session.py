"""Tests for `aichat port` and the codex -> claude converter.

Uses real functions and real tmp files (no mocks), following the
conventions of the other tests in this directory.

E2E RESUME VERIFICATION EVIDENCE (mandatory manual check, run for
real against the user's actual homes -- see the feature contract):
a small real codex rollout was ported into the real ~/.claude home
and resumed successfully; the ported session file was deleted
afterwards. Exact commands and output are recorded at the bottom of
this file in the E2E EVIDENCE comment block.
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.port_codex_to_claude import (
    TOOL_TEXT_CAP,
    port_codex_session_to_claude,
)
from claude_code_tools.session_utils import encode_claude_project_path

MODERN_UUID = "019f6d85-df3c-7c83-84f6-b97e73305bbb"
PARENT_UUID = "019f6d83-5ce8-7171-9b53-5c3698deccc0"
LEGACY_UUID = "1d18f22c-9a88-4ea9-ae77-4094c9f87aaa"

LONG_TOOL_ARGS = "y" * (TOOL_TEXT_CAP + 500)


def _ts(seconds: int) -> str:
    """Build a deterministic ISO timestamp for fixture lines."""
    return f"2026-07-16T20:42:{seconds:02d}.000Z"


def _resp(seconds: int, payload) -> str:
    """Build a modern-format response_item line."""
    return json.dumps(
        {
            "timestamp": _ts(seconds),
            "type": "response_item",
            "payload": payload,
        }
    )


def _msg(role: str, text: str, block_type: str = "input_text") -> dict:
    """Build a codex message payload."""
    return {
        "type": "message",
        "role": role,
        "content": [{"type": block_type, "text": text}],
    }


def _session_meta(
    seconds: int,
    session_id: str,
    cwd: str,
    branch: str = "main",
) -> str:
    """Build a modern-format session_meta line."""
    return json.dumps(
        {
            "timestamp": _ts(seconds),
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": _ts(seconds),
                "cwd": cwd,
                "git": {"branch": branch},
                "originator": "codex_exec",
            },
        }
    )


def _rollout_path(codex_home: Path, session_id: str) -> Path:
    """Compute a modern rollout path for a session id."""
    day_dir = codex_home / "sessions" / "2026" / "07" / "16"
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"rollout-2026-07-16T20-41-57-{session_id}.jsonl"


def write_rollout_lines(
    codex_home: Path, session_id: str, lines: list
) -> Path:
    """Write raw fixture lines as a modern rollout under codex_home."""
    path = _rollout_path(codex_home, session_id)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_modern_rollout(codex_home: Path, project_dir: Path) -> Path:
    """Write a synthetic modern-format codex rollout under codex_home."""
    lines = [
        _session_meta(0, MODERN_UUID, str(project_dir)),
        _resp(1, _msg("developer", "<permissions instructions>\nsandbox")),
        # ordinary developer-role text with NO wrapper tag: must be
        # excluded because of its ROLE, not its content
        _resp(1, _msg("developer", "DEVNOISE-ordinary developer text")),
        _resp(
            1,
            _msg(
                "user",
                "<environment_context>\n"
                f"  <cwd>{project_dir}</cwd>\n"
                "</environment_context>",
            ),
        ),
        _resp(1, _msg("user", "<user_instructions>obey</user_instructions>")),
        json.dumps(
            {
                "timestamp": _ts(2),
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "t1"},
            }
        ),
        json.dumps(
            {
                "timestamp": _ts(2),
                "type": "world_state",
                "payload": {"full": True, "state": {}},
            }
        ),
        json.dumps(
            {
                "timestamp": _ts(2),
                "type": "turn_context",
                "payload": {"turn_id": "t1", "cwd": str(project_dir)},
            }
        ),
        _resp(3, _msg("user", "Hello, please compute X")),
        _resp(
            4,
            {
                "type": "reasoning",
                "summary": [
                    {"type": "summary_text", "text": "**Thinking**"}
                ],
                "encrypted_content": "SECRET-REASONING-BLOB",
            },
        ),
        _resp(5, _msg("assistant", "Sure, computing.", "output_text")),
        _resp(
            6,
            {
                "type": "custom_tool_call",
                "call_id": "call_1",
                "name": "exec",
                "input": LONG_TOOL_ARGS,
            },
        ),
        _resp(
            7,
            {
                "type": "custom_tool_call_output",
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "tool ran fine"}
                ],
            },
        ),
        _resp(
            8,
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "send_message",
                "arguments": '{"a": 1}',
            },
        ),
        _resp(
            9,
            {
                "type": "function_call_output",
                "call_id": "call_2",
                "output": "done",
            },
        ),
        _resp(
            9,
            {
                "type": "function_call_output",
                "call_id": "call_3",
                "output": "",
            },
        ),
        "{{{not valid json",
        _resp(10, _msg("user", "Thanks, now do Y")),
        _resp(11, _msg("assistant", "Done with Y", "output_text")),
    ]
    return write_rollout_lines(codex_home, MODERN_UUID, lines)


def write_legacy_rollout(codex_home: Path, project_dir: Path) -> Path:
    """Write a synthetic legacy-2025-format codex rollout.

    Mirrors a real 2025 rollout sample: the environment context
    states the cwd as a plain "Current working directory: <path>"
    line (not a ``<cwd>`` tag).
    """
    day_dir = codex_home / "sessions" / "2025" / "08" / "27"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2025-08-27T19-44-13-{LEGACY_UUID}.jsonl"

    lines = [
        json.dumps(
            {
                "id": LEGACY_UUID,
                "timestamp": "2025-08-27T19:44:13.169Z",
                "instructions": None,
                "git": {"branch": "legacy-branch"},
            }
        ),
        json.dumps({"record_type": "state"}),
        json.dumps(
            {
                "type": "message",
                "id": None,
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "<environment_context>\n"
                            "Current working directory: "
                            f"{project_dir}\n"
                            "Approval policy: on-request\n"
                            "Sandbox mode: read-only\n"
                            "Network access: restricted\n"
                            "</environment_context>"
                        ),
                    }
                ],
            }
        ),
        json.dumps(
            {
                "type": "message",
                "id": None,
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Legacy question"}
                ],
            }
        ),
        json.dumps(
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [
                    {"type": "summary_text", "text": "legacy thoughts"}
                ],
                "encrypted_content": "LEGACY-SECRET",
            }
        ),
        json.dumps({"record_type": "state"}),
        json.dumps(
            {
                "type": "message",
                "id": None,
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Legacy answer"}
                ],
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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


def _read_lines(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestConverterModernFormat:
    """Direct tests of port_codex_session_to_claude (modern format)."""

    @pytest.fixture
    def ported(self, codex_home, claude_home, project_dir):
        rollout = write_modern_rollout(codex_home, project_dir)
        new_id, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return rollout, new_id, out_path

    def test_output_location_and_naming(
        self, ported, claude_home, project_dir
    ):
        _, new_id, out_path = ported
        expected_dir = (
            claude_home
            / "projects"
            / encode_claude_project_path(str(project_dir))
        )
        assert out_path.parent == expected_dir
        assert out_path.name == f"{new_id}.jsonl"
        assert out_path.exists()
        # new_id is a valid, fresh UUIDv4 (not v1 or any other version)
        parsed = uuid.UUID(new_id)
        assert str(parsed) == new_id
        assert parsed.version == 4

    def test_uuid_chain(self, ported):
        _, new_id, out_path = ported
        lines = _read_lines(out_path)
        assert lines[0]["parentUuid"] is None
        uuids = [line["uuid"] for line in lines]
        assert len(set(uuids)) == len(uuids)
        # every line uuid and the session id must be UUIDv4
        assert uuid.UUID(new_id).version == 4
        for line in lines:
            assert uuid.UUID(line["uuid"]).version == 4
            assert uuid.UUID(line["sessionId"]).version == 4
        for prev, cur in zip(lines, lines[1:]):
            assert cur["parentUuid"] == prev["uuid"]

    def test_consistent_session_id(self, ported):
        _, new_id, out_path = ported
        lines = _read_lines(out_path)
        assert all(line["sessionId"] == new_id for line in lines)

    def test_strict_alternation_starting_with_user(self, ported):
        _, _, out_path = ported
        lines = _read_lines(out_path)
        assert lines[0]["type"] == "user"
        for i, line in enumerate(lines):
            expected = "user" if i % 2 == 0 else "assistant"
            assert line["type"] == expected
            assert line["message"]["role"] == expected

    def test_no_empty_content(self, ported):
        _, _, out_path = ported
        for line in _read_lines(out_path):
            content = line["message"]["content"]
            assert isinstance(content, list) and content
            for block in content:
                assert block["type"] == "text"
                assert block["text"].strip()

    def test_tool_call_flattening(self, ported):
        _, _, out_path = ported
        text = "\n".join(
            block["text"]
            for line in _read_lines(out_path)
            if line["type"] == "assistant"
            for block in line["message"]["content"]
        )
        assert "[codex tool call] exec(" in text
        assert "[codex tool result] tool ran fine" in text
        assert '[codex tool call] send_message({"a": 1})' in text
        assert "[codex tool result] done" in text

    def test_truncation(self, ported):
        _, _, out_path = ported
        raw = out_path.read_text(encoding="utf-8")
        assert "... [truncated 500 chars]" in raw
        assert LONG_TOOL_ARGS not in raw
        assert "y" * TOOL_TEXT_CAP in raw

    def test_reasoning_and_encrypted_dropped(self, ported):
        _, _, out_path = ported
        raw = out_path.read_text(encoding="utf-8")
        assert "SECRET-REASONING-BLOB" not in raw
        assert "**Thinking**" not in raw

    def test_environment_and_developer_noise_skipped(self, ported):
        _, _, out_path = ported
        raw = out_path.read_text(encoding="utf-8")
        assert "environment_context" not in raw
        assert "permissions instructions" not in raw
        assert "user_instructions" not in raw
        # developer-role messages are excluded even when their text is
        # ordinary (no wrapper tag)
        assert "DEVNOISE-ordinary developer text" not in raw

    def test_continue_metadata_on_first_line_only(self, ported):
        rollout, _, out_path = ported
        lines = _read_lines(out_path)
        cm = lines[0]["continue_metadata"]
        assert cm["parent_session_id"] == MODERN_UUID
        assert cm["parent_session_file"] == str(rollout.absolute())
        assert cm["ported_from"] == "codex"
        assert cm["continued_at"]
        for line in lines[1:]:
            assert "continue_metadata" not in line

    def test_line_fields_and_timestamps(self, ported, project_dir):
        _, _, out_path = ported
        lines = _read_lines(out_path)
        for line in lines:
            assert line["isSidechain"] is False
            assert line["userType"] == "external"
            assert line["cwd"] == str(project_dir)
            assert line["gitBranch"] == "main"
            assert line["version"]
            assert line["timestamp"]
        # timestamp carried from the corresponding codex line
        assert lines[0]["timestamp"] == _ts(3)
        assert lines[1]["timestamp"] == _ts(5)

    def test_transcript_content(self, ported):
        _, _, out_path = ported
        lines = _read_lines(out_path)
        assert len(lines) == 4
        texts = [
            line["message"]["content"][0]["text"] for line in lines
        ]
        assert texts[0] == "Hello, please compute X"
        assert texts[1].startswith("Sure, computing.")
        assert texts[2] == "Thanks, now do Y"
        assert texts[3] == "Done with Y"


class TestMultipleSessionMeta:
    """Forked rollouts embed ancestor session_meta records too.

    Mirrors the ground-truth fork structure: the file's own
    session_meta comes first, its parent's second. Only the first /
    file-matching record is authoritative.
    """

    def test_first_session_meta_wins(
        self, codex_home, claude_home, project_dir, tmp_path
    ):
        parent_dir = tmp_path / "parentproj"
        parent_dir.mkdir()
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir), "main"),
            _session_meta(
                0, PARENT_UUID, str(parent_dir), "parent-branch"
            ),
            _resp(3, _msg("user", "Forked question")),
            _resp(4, _msg("assistant", "Forked answer", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        cm = out[0]["continue_metadata"]
        # identity = the file's own id, NOT the embedded parent's
        assert cm["parent_session_id"] == MODERN_UUID
        assert out[0]["cwd"] == str(project_dir)
        assert out[0]["gitBranch"] == "main"
        # output dir derives from the file's own cwd
        assert out_path.parent.name == encode_claude_project_path(
            str(project_dir)
        )

    def test_filename_matching_meta_wins_even_if_second(
        self, codex_home, claude_home, project_dir, tmp_path
    ):
        parent_dir = tmp_path / "parentproj2"
        parent_dir.mkdir()
        lines = [
            _session_meta(
                0, PARENT_UUID, str(parent_dir), "parent-branch"
            ),
            _session_meta(0, MODERN_UUID, str(project_dir), "main"),
            _resp(3, _msg("user", "Q")),
            _resp(4, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        cm = out[0]["continue_metadata"]
        assert cm["parent_session_id"] == MODERN_UUID
        assert out[0]["cwd"] == str(project_dir)
        assert out[0]["gitBranch"] == "main"

    def test_ancestor_values_reset_when_authoritative_lacks_fields(
        self, codex_home, claude_home, tmp_path, monkeypatch
    ):
        """Ancestor-first metadata with a null/missing-field
        authoritative record: the ancestor's cwd/branch must NOT
        leak; documented fallbacks apply instead (cwd -> current
        directory, branch -> "")."""
        ancestor_dir = tmp_path / "ancestorproj"
        ancestor_dir.mkdir()
        authoritative = json.dumps(
            {
                "timestamp": _ts(1),
                "type": "session_meta",
                "payload": {"id": MODERN_UUID, "cwd": None, "git": None},
            }
        )
        lines = [
            _session_meta(
                0, PARENT_UUID, str(ancestor_dir), "ancestor-branch"
            ),
            authoritative,
            _resp(3, _msg("user", "Q")),
            _resp(4, _msg("assistant", "A", "output_text")),
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
        assert out[0]["gitBranch"] == ""
        assert out[0]["continue_metadata"]["parent_session_id"] == (
            MODERN_UUID
        )
        # output dir derives from the fallback cwd, not the ancestor's
        assert out_path.parent.name == encode_claude_project_path(
            str(run_dir)
        )

    def test_session_id_field_recognized_as_authoritative(
        self, codex_home, claude_home, project_dir, tmp_path
    ):
        """A session_meta payload with a null ``id`` but a matching
        ``session_id`` is still recognized as authoritative."""
        ancestor_dir = tmp_path / "ancestorproj2"
        ancestor_dir.mkdir()
        authoritative = json.dumps(
            {
                "timestamp": _ts(1),
                "type": "session_meta",
                "payload": {
                    "id": None,
                    "session_id": MODERN_UUID,
                    "cwd": str(project_dir),
                    "git": {"branch": "main"},
                },
            }
        )
        lines = [
            _session_meta(
                0, PARENT_UUID, str(ancestor_dir), "ancestor-branch"
            ),
            authoritative,
            _resp(3, _msg("user", "Q")),
            _resp(4, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        assert out[0]["cwd"] == str(project_dir)
        assert out[0]["gitBranch"] == "main"
        assert out[0]["continue_metadata"]["parent_session_id"] == (
            MODERN_UUID
        )


class TestConverterLegacyFormat:
    """Legacy 2025 rollout format support."""

    def test_legacy_port(self, codex_home, claude_home, project_dir):
        rollout = write_legacy_rollout(codex_home, project_dir)
        new_id, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        expected_dir = (
            claude_home
            / "projects"
            / encode_claude_project_path(str(project_dir))
        )
        assert out_path.parent == expected_dir

        lines = _read_lines(out_path)
        assert len(lines) == 2
        assert lines[0]["type"] == "user"
        assert (
            lines[0]["message"]["content"][0]["text"]
            == "Legacy question"
        )
        assert lines[1]["type"] == "assistant"
        assert (
            lines[1]["message"]["content"][0]["text"]
            == "Legacy answer"
        )
        # cwd harvested from <environment_context>
        assert lines[0]["cwd"] == str(project_dir)
        assert lines[0]["gitBranch"] == "legacy-branch"
        raw = out_path.read_text(encoding="utf-8")
        assert "LEGACY-SECRET" not in raw
        assert "environment_context" not in raw
        cm = lines[0]["continue_metadata"]
        assert cm["parent_session_id"] == LEGACY_UUID


class TestPortCLI:
    """CLI-level tests for `aichat port`."""

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

    def test_codex_source_ports_to_claude(
        self, runner, codex_home, claude_home, project_dir
    ):
        write_modern_rollout(codex_home, project_dir)
        result = self._invoke(
            runner, ["port", MODERN_UUID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )
        assert "New Claude session id:" in result.output
        assert f"cd {project_dir} && claude --resume " in result.output
        # output file actually created under the tmp claude home
        proj = claude_home / "projects" / encode_claude_project_path(
            str(project_dir)
        )
        assert list(proj.glob("*.jsonl"))

    def test_direct_path_outside_homes_detected_by_content(
        self, runner, codex_home, claude_home, project_dir, tmp_path
    ):
        rollout = write_modern_rollout(codex_home, project_dir)
        copied = tmp_path / "copied-rollout.jsonl"
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

    def test_resume_hint_quotes_cwd_with_spaces(
        self, runner, codex_home, claude_home, tmp_path
    ):
        spaced_dir = tmp_path / "my proj"
        spaced_dir.mkdir()
        write_modern_rollout(codex_home, spaced_dir)
        result = self._invoke(
            runner, ["port", MODERN_UUID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert f"cd '{spaced_dir}' && claude --resume " in result.output

    def test_claude_source_converts_to_codex(
        self, runner, codex_home, claude_home, project_dir
    ):
        sid = str(uuid.uuid4())
        enc = encode_claude_project_path(str(project_dir))
        proj = claude_home / "projects" / enc
        proj.mkdir(parents=True)
        line = {
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": str(project_dir),
            "sessionId": sid,
            "version": "2.1.211",
            "gitBranch": "main",
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "uuid": str(uuid.uuid4()),
            "timestamp": _ts(0),
        }
        (proj / f"{sid}.jsonl").write_text(
            json.dumps(line) + "\n", encoding="utf-8"
        )
        result = self._invoke(
            runner, ["port", sid], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: claude — porting to Codex"
            in result.output
        )
        # actually converts now: prints the new codex session id and
        # the resume hint, plus the interactive /import tip
        assert "New Codex session id:" in result.output
        assert f"cd {project_dir} && codex resume " in result.output
        assert "/import" in result.output
        # the rollout was created under the tmp codex home
        assert list(
            (codex_home / "sessions").rglob("rollout-*.jsonl")
        )
        # it is a claude->codex port, not the other direction
        assert "New Claude session id:" not in result.output

    def test_group_level_home_options_before_subcommand(
        self, runner, codex_home, claude_home, project_dir
    ):
        """`aichat --claude-home X --codex-home Y port <id>` must use
        the group-level homes, not the defaults."""
        write_modern_rollout(codex_home, project_dir)
        result = runner.invoke(
            main,
            [
                "--claude-home",
                str(claude_home),
                "--codex-home",
                str(codex_home),
                "port",
                MODERN_UUID,
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "Detected source agent: codex — porting to Claude Code"
            in result.output
        )
        proj = claude_home / "projects" / encode_claude_project_path(
            str(project_dir)
        )
        assert list(proj.glob("*.jsonl"))

    def test_detected_direction_is_first_output(
        self, runner, codex_home, claude_home, project_dir
    ):
        """Nothing (e.g. auto-index progress) may print before the
        detected-direction line."""
        write_modern_rollout(codex_home, project_dir)
        result = self._invoke(
            runner, ["port", MODERN_UUID], claude_home, codex_home
        )
        assert result.exit_code == 0, result.output
        first_line = result.output.splitlines()[0]
        assert first_line == (
            "Detected source agent: codex — porting to Claude Code"
        )
        try:
            stderr = result.stderr
        except ValueError:  # older click: stderr mixed into output
            stderr = ""
        assert "Indexing" not in stderr
        assert stderr.strip() == ""

    def test_ambiguous_partial_id_rejected(
        self, runner, codex_home, claude_home, project_dir
    ):
        """A partial id matching several sessions must error, not
        silently port an arbitrary one."""
        other_uuid = "019f6d85-df3c-7c83-84f6-b97e73305ccc"
        write_modern_rollout(codex_home, project_dir)
        lines = [
            _session_meta(0, other_uuid, str(project_dir)),
            _resp(1, _msg("user", "Other question")),
            _resp(2, _msg("assistant", "Other answer", "output_text")),
        ]
        write_rollout_lines(codex_home, other_uuid, lines)
        # shared fragment of both UUIDs
        result = self._invoke(
            runner,
            ["port", "019f6d85-df3c-7c83-84f6"],
            claude_home,
            codex_home,
        )
        assert result.exit_code != 0
        try:
            stderr = result.stderr
        except ValueError:
            stderr = ""
        combined = result.output + stderr
        assert "Ambiguous session" in combined
        assert MODERN_UUID in combined
        assert other_uuid in combined
        # each candidate line names its agent and modification time
        assert "[codex]" in combined
        assert "modified" in combined
        # nothing was ported
        assert not (claude_home / "projects").exists()

    def test_glob_metacharacters_treated_literally(
        self, runner, codex_home, claude_home, project_dir
    ):
        """Inputs like '*' must not match arbitrary sessions."""
        write_modern_rollout(codex_home, project_dir)
        for weird in ["*", "?", "[a-z]", "  "]:
            result = self._invoke(
                runner, ["port", weird], claude_home, codex_home
            )
            assert result.exit_code != 0, weird
            try:
                stderr = result.stderr
            except ValueError:
                stderr = ""
            combined = result.output + stderr
            assert "Session not found in Claude or Codex" in combined
        assert not (claude_home / "projects").exists()

    def test_unknown_session_errors(
        self, runner, codex_home, claude_home
    ):
        unknown_id = "deadbeef-0000-0000-0000-000000000000"
        result = self._invoke(
            runner,
            ["port", unknown_id],
            claude_home,
            codex_home,
        )
        assert result.exit_code != 0
        try:
            stderr = result.stderr
        except ValueError:  # older click: stderr mixed into output
            stderr = ""
        combined = result.output + stderr
        # clear, user-facing message naming the session and both agents
        assert "Session not found in Claude or Codex" in combined
        assert unknown_id in combined
        # a clean error, not an unrelated exception traceback
        assert "Traceback" not in combined


# ---------------------------------------------------------------------
# E2E EVIDENCE (real-home resume verification, run 2026-07-16; output
# captured verbatim; the ported session file was deleted afterwards).
#
# Step 1 — port a small (58KB) real codex session into ~/.claude:
#   $ aichat port /Users/pchalasani/.codex/sessions/2026/06/23/\
#     rollout-2026-06-23T20-53-54-019ef71e-89c3-7022-a4dd-63315c2044ad.jsonl
#   Detected source agent: codex — porting to Claude Code
#   New Claude session id: 86e76523-e1dc-4927-b7f8-85246b56c7ec
#   Output file: /Users/pchalasani/.claude/projects/\
#     -Users-pchalasani-Git-observability-feat-nd2dl/\
#     86e76523-e1dc-4927-b7f8-85246b56c7ec.jsonl
#   Session cwd: /Users/pchalasani/Git/observability.feat-nd2dl
#
# Step 2 — resume from the session's cwd:
#   $ cd /Users/pchalasani/Git/observability.feat-nd2dl && \
#     timeout 180 claude --resume 86e76523-e1dc-4927-b7f8-85246b56c7ec \
#       -p "Reply with exactly OK"
#   OK
#   EXIT_CODE=0
#
# SUCCESS: claude accepted the synthesized transcript (no API 4xx
# about roles/blocks) and replied. The ported session file was then
# deleted from ~/.claude/projects:
#   $ rm .../86e76523-e1dc-4927-b7f8-85246b56c7ec.jsonl   # deleted
# ---------------------------------------------------------------------
#
# RESOLVER-INTEGRATION E2E EVIDENCE (name-based claude->codex port +
# codex resume, and partial-codex-id port + claude resume, both run
# 2026-07-17 with cleanup) is recorded at the bottom of
# tests/test_port_resolver_integration.py.
# ---------------------------------------------------------------------
