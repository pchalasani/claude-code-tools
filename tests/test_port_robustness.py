"""Regression tests for the iteration-6 adversarial-review fixes.

Covers: tolerance of JSON lines that raise plain ``ValueError`` from
``json.loads`` (integer literals over Python's digit limit), rejection
of path separators in session-id filename matching, shape hardening of
extract_session_metadata's trim/continue metadata fields, and the
recursive encrypted-content scan for agent_message payloads.

Split into its own file to keep the sibling port test files under the
repo's 1000-line limit. Shares fixture helpers via imports from
tests.test_port_session.
"""

import json

import pytest

from claude_code_tools.export_session import extract_session_metadata
from claude_code_tools.port_codex_to_claude import (
    port_codex_session_to_claude,
)
from claude_code_tools.port_service import (
    PortSessionError,
    resolve_port_session,
)
from claude_code_tools.session_utils import (
    detect_agent_from_content,
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


# A JSON line whose integer literal exceeds Python's default
# int-to-str digit limit (4300): json.loads raises a plain ValueError
# (NOT json.JSONDecodeError) while converting it.
HUGE_INT_LINE = '{"n": ' + "1" * 5000 + "}"


class TestHugeIntegerLineTolerance:
    """A record raising plain ValueError from json.loads is skipped."""

    def test_converter_skips_huge_int_and_ports_rest(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            HUGE_INT_LINE,
            _resp(1, _msg("user", "Question after hostile line")),
            HUGE_INT_LINE,
            _resp(2, _msg("assistant", "Answer survives", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        out = _read_lines(out_path)
        texts = [ln["message"]["content"][0]["text"] for ln in out]
        assert texts[0] == "Question after hostile line"
        assert any("Answer survives" in t for t in texts)

    def test_detect_agent_skips_leading_huge_int(self, tmp_path):
        path = tmp_path / "huge-int.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(HUGE_INT_LINE + "\n")
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

    def test_is_valid_session_skips_leading_huge_int(self, tmp_path):
        path = tmp_path / "huge-int-valid.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(HUGE_INT_LINE + "\n")
            f.write(_resp(1, _msg("user", "real question")) + "\n")
        assert is_valid_session(path) is True


class TestPathSeparatorIdentifiers:
    """Identifiers containing path separators match NOTHING instead of
    becoming multi-component glob patterns."""

    def _nested_decoy(self, codex_home):
        # A file the naive "*/*/*/*a/b*.jsonl" glob WOULD reach: it
        # lives one directory deeper than real rollouts.
        deep = codex_home / "sessions" / "2026" / "07" / "16" / "xa"
        deep.mkdir(parents=True, exist_ok=True)
        decoy = deep / f"b-decoy-{MODERN_UUID}.jsonl"
        decoy.write_text(
            _resp(1, _msg("user", "decoy")) + "\n", encoding="utf-8"
        )
        return decoy

    def test_slash_identifier_matches_nothing(
        self, codex_home, claude_home
    ):
        self._nested_decoy(codex_home)
        with pytest.raises(PortSessionError, match="not found"):
            resolve_port_session(
                "a/b",
                claude_home=str(claude_home),
                codex_home=str(codex_home),
            )
        assert (
            find_session_file(
                "a/b",
                claude_home=str(claude_home),
                codex_home=str(codex_home),
            )
            is None
        )

    def test_backslash_identifier_matches_nothing(
        self, codex_home, claude_home
    ):
        with pytest.raises(PortSessionError, match="not found"):
            resolve_port_session(
                "a\\b",
                claude_home=str(claude_home),
                codex_home=str(codex_home),
            )

    def test_plain_identifier_still_matches(
        self, codex_home, claude_home, project_dir
    ):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(2, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        resolved = resolve_port_session(
            MODERN_UUID,
            claude_home=str(claude_home),
            codex_home=str(codex_home),
        )
        assert resolved.agent == "codex"
        assert resolved.session_file == rollout.resolve()


class TestMetadataShapeHardening:
    """Truthy non-string metadata values never crash extraction."""

    def _extract(self, tmp_path, records):
        path = tmp_path / "meta-shape.jsonl"
        path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )
        return extract_session_metadata(path, "claude")

    @pytest.mark.parametrize(
        "bad_parent", [["list"], {"nested": 1}, 123, True, ""]
    )
    def test_non_string_trim_parent_file_ignored(
        self, tmp_path, bad_parent
    ):
        meta = self._extract(
            tmp_path,
            [
                {
                    "type": "user",
                    "sessionId": "sid-1",
                    "cwd": "/tmp/x",
                    "gitBranch": "main",
                    "timestamp": _ts(0),
                    "trim_metadata": {
                        "parent_file": bad_parent,
                        "stats": "not-a-dict",
                    },
                }
            ],
        )
        assert meta["derivation_type"] == "trimmed"
        assert meta["parent_session_file"] is None
        assert meta["parent_session_id"] is None
        assert meta["trim_stats"] is None

    def test_valid_trim_parent_file_still_extracted(self, tmp_path):
        meta = self._extract(
            tmp_path,
            [
                {
                    "type": "user",
                    "sessionId": "sid-1",
                    "cwd": "/tmp/x",
                    "gitBranch": "main",
                    "timestamp": _ts(0),
                    "trim_metadata": {
                        "parent_file": "/tmp/parent-abc.jsonl",
                        "stats": {"kept": 3},
                    },
                }
            ],
        )
        assert meta["parent_session_file"] == "/tmp/parent-abc.jsonl"
        assert meta["parent_session_id"] == "parent-abc"
        assert meta["trim_stats"] == {"kept": 3}

    def test_non_string_continue_metadata_fields_ignored(
        self, tmp_path
    ):
        meta = self._extract(
            tmp_path,
            [
                {
                    "type": "user",
                    "sessionId": "sid-1",
                    "cwd": "/tmp/x",
                    "gitBranch": "main",
                    "timestamp": _ts(0),
                    "continue_metadata": {
                        "parent_session_id": {"weird": 1},
                        "parent_session_file": [1, 2],
                    },
                }
            ],
        )
        assert meta["derivation_type"] == "continued"
        assert meta["parent_session_id"] is None
        assert meta["parent_session_file"] is None

    def test_non_string_cwd_and_branch_skipped(self, tmp_path):
        meta = self._extract(
            tmp_path,
            [
                {
                    "type": "user",
                    "sessionId": "sid-1",
                    "cwd": {"not": "a path"},
                    "gitBranch": ["nope"],
                    "timestamp": _ts(0),
                },
                {
                    "type": "assistant",
                    "sessionId": "sid-1",
                    "cwd": "/real/path",
                    "gitBranch": "main",
                    "timestamp": _ts(1),
                },
            ],
        )
        assert meta["cwd"] == "/real/path"
        assert meta["branch"] == "main"


class TestNestedEncryptedAgentMessage:
    """encrypted_content nested at ANY depth drops the whole
    agent_message."""

    def _port_raw(self, codex_home, claude_home, project_dir, item):
        lines = [
            _session_meta(0, MODERN_UUID, str(project_dir)),
            _resp(1, _msg("user", "Q")),
            _resp(2, item),
            _resp(3, _msg("assistant", "A", "output_text")),
        ]
        rollout = write_rollout_lines(codex_home, MODERN_UUID, lines)
        _, out_path = port_codex_session_to_claude(
            rollout, claude_home=claude_home
        )
        return out_path.read_text(encoding="utf-8")

    def test_encrypted_inside_block_metadata_dropped(
        self, codex_home, claude_home, project_dir
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            {
                "type": "agent_message",
                "content": [
                    {
                        "type": "input_text",
                        "text": "leaky envelope text",
                        "meta": {"encrypted_content": "DEEP-BLOB"},
                    }
                ],
            },
        )
        assert "leaky envelope text" not in raw
        assert "DEEP-BLOB" not in raw

    def test_encrypted_inside_nested_list_dropped(
        self, codex_home, claude_home, project_dir
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            {
                "type": "agent_message",
                "content": [
                    {"type": "input_text", "text": "outer text"},
                    {
                        "type": "wrapper",
                        "items": [
                            [{"type": "encrypted_content"}],
                        ],
                    },
                ],
            },
        )
        assert "outer text" not in raw

    def test_plain_nested_agent_message_still_ported(
        self, codex_home, claude_home, project_dir
    ):
        raw = self._port_raw(
            codex_home,
            claude_home,
            project_dir,
            {
                "type": "agent_message",
                "content": [
                    {
                        "type": "input_text",
                        "text": "plain nested note",
                        "meta": {"routing": {"hop": 1}},
                    }
                ],
            },
        )
        assert "plain nested note" in raw
