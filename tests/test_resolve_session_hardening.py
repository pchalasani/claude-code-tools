"""Hostile-transcript hardening tests for ``aichat resolve``.

Split from ``test_resolve_session.py`` for the repo's 1000-line
file limit; fixtures and helpers come from
:mod:`tests.resolve_session_helpers`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.resolve_session_helpers import (
    FakeHome,
    _invoke,
    _json,
    _write_claude_session,
    _write_codex_session,
    _write_raw_claude_session,
    claude_home,
    runner,
)

__all__ = ["claude_home", "runner"]


def _write_legacy_rollout_lines(
    home: Path, filename_id: str, lines: list[object]
) -> Path:
    """Write a legacy-named Codex rollout from exact JSON values."""
    day_dir = home / "sessions" / "2025" / "08" / "27"
    day_dir.mkdir(parents=True, exist_ok=True)
    rollout = day_dir / (
        f"rollout-2025-08-27T19-44-13-{filename_id}.jsonl"
    )
    rollout.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    return rollout


def test_claude_non_object_json_before_valid_records_is_resolvable(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "ffff6666-6666-4666-8666-666666666666"
    cwd = str(tmp_path / "corrupt-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            None,
            [],
            42,
            "text",
            {"timestamp": {"malformed": True}},
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Corrupt Named Session",
            },
        ],
    )
    valid_result = _invoke(
        runner,
        claude_home.ids[2],
        claude_home.path,
    )
    exact_result = _invoke(runner, session_id, claude_home.path)
    partial_result = _invoke(runner, "ffff", claude_home.path)
    name_result = _invoke(runner, "Corrupt Named", claude_home.path)
    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == claude_home.ids[2]
    assert exact_result.exit_code == 0
    assert _json(exact_result)["matched_by"] == "id"
    assert partial_result.exit_code == 0
    assert _json(partial_result)["session_id"] == session_id
    assert name_result.exit_code == 0
    assert _json(name_result)["session_id"] == session_id


def test_claude_malformed_jsonl_before_between_and_after_valid_records(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fefe6666-6666-4666-8666-666666666666"
    cwd = str(tmp_path / "malformed-json-project")
    session_file = _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [],
    )
    valid_user = json.dumps(
        {"type": "user", "sessionId": session_id, "cwd": cwd}
    )
    valid_title = json.dumps(
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": "Malformed JSON Survivor",
        }
    )
    session_file.write_text(
        "{not valid json}\n"
        f"{valid_user}\n"
        '["unterminated"\n'
        f"{valid_title}\n"
        "not-json-after-valid-records\n",
        encoding="utf-8",
    )

    named = _invoke(runner, "Malformed JSON Survivor", claude_home.path)
    valid = _invoke(runner, claude_home.ids[2], claude_home.path)

    assert named.exit_code == 0
    assert _json(named)["session_id"] == session_id
    assert valid.exit_code == 0
    assert _json(valid)["session_id"] == claude_home.ids[2]
    assert "Traceback" not in named.output + valid.output


def test_claude_malformed_nested_metadata_after_valid_records(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fefe7777-7777-4777-8777-777777777777"
    cwd = str(tmp_path / "nested-metadata-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "progress",
                "cwd": {"wrong": "shape"},
                "trim_metadata": [],
                "continue_metadata": "invalid",
            },
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "file-history-snapshot",
                "metadata": {"git": []},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Nested Metadata Survivor",
            },
            None,
            [],
            {"timestamp": ["wrong", "shape"]},
        ],
    )

    result = _invoke(runner, "Nested Metadata Survivor", claude_home.path)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["directory"] == cwd
    assert "Traceback" not in result.output


def test_claude_invalid_utf8_is_isolated(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "ffff7777-7777-4777-8777-777777777777"
    cwd = str(tmp_path / "invalid-utf8")
    session_file = _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [{"type": "user", "sessionId": session_id, "cwd": cwd}],
    )
    session_file.write_bytes(session_file.read_bytes() + b"\xff\n")
    valid_result = _invoke(runner, claude_home.ids[2], claude_home.path)
    partial_result = _invoke(runner, "ffff7777", claude_home.path)

    assert valid_result.exit_code == 0
    assert _json(valid_result)["session_id"] == claude_home.ids[2]
    assert "Traceback" not in valid_result.output
    assert partial_result.exit_code == 1


def test_stale_claude_file_does_not_block_valid_match(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    payload_cwd = str(tmp_path / "moved-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        str(tmp_path / "original-project"),
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": payload_cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Moved Session",
            },
        ],
    )

    result = _invoke(runner, session_id, claude_home.path)
    name_result = _invoke(runner, "Moved Session", claude_home.path)
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id
    assert payload["name"] == "Moved Session"
    assert name_result.exit_code == 0
    assert _json(name_result)["session_id"] == session_id


def test_claude_sidechain_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "eeee8888-8888-4888-8888-888888888888"
    cwd = str(tmp_path / "sidechain-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "isSidechain": True,
                "message": {"content": "hello"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "Hidden Sidechain",
            },
        ],
    )

    exact_result = _invoke(runner, session_id, claude_home.path)
    partial_result = _invoke(runner, "eeee", claude_home.path)
    name_result = _invoke(runner, "Hidden Sidechain", claude_home.path)
    assert exact_result.exit_code == 0
    assert _json(exact_result)["matched_by"] == "id"
    assert partial_result.exit_code == 1
    assert _json(partial_result)["error"] == "not_found"
    assert name_result.exit_code == 1
    assert _json(name_result)["error"] == "not_found"


def test_claude_agent_filename_sidechain_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    filename_id = "agent-fafa8888-8888-4888-8888-888888888888"
    cwd = str(tmp_path / "filename-sidechain")
    _write_raw_claude_session(
        claude_home.path,
        filename_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": "fafa8888-8888-4888-8888-888888888888",
                "cwd": cwd,
            },
            {
                "type": "custom-title",
                "customTitle": "Filename Sidechain",
            },
        ],
    )

    exact = _invoke(runner, filename_id, claude_home.path)
    named = _invoke(runner, "Filename Sidechain", claude_home.path)

    assert exact.exit_code == 0
    assert _json(exact)["matched_by"] == "id"
    assert named.exit_code == 1


def test_claude_late_sidechain_marker_is_exact_id_only(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "fafa9999-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "late-sidechain")
    lines: list[object] = [
        {"type": "user", "sessionId": session_id, "cwd": cwd},
        *({"type": "progress", "index": index} for index in range(12)),
        {"type": "progress", "isSidechain": True},
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": "Late Sidechain",
        },
    ]
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        lines,
    )

    exact = _invoke(runner, session_id, claude_home.path)
    partial = _invoke(runner, "fafa", claude_home.path)
    named = _invoke(runner, "Late Sidechain", claude_home.path)

    assert exact.exit_code == 0
    assert _json(exact)["matched_by"] == "id"
    assert partial.exit_code == 1
    assert named.exit_code == 1


def test_claude_ai_title_is_not_a_name(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    session_id = "dddd9999-9999-4999-8999-999999999999"
    cwd = str(tmp_path / "ai-title-project")
    _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": "hello"},
            },
            {
                "type": "ai-title",
                "sessionId": session_id,
                "aiTitle": "Automatic Only Title",
            },
        ],
    )

    exact_result = _invoke(runner, session_id, claude_home.path)
    title_result = _invoke(runner, "Automatic Only", claude_home.path)
    assert exact_result.exit_code == 0
    assert _json(exact_result)["name"] is None
    assert title_result.exit_code == 1
    assert _json(title_result)["error"] == "not_found"


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_unreadable_home_is_a_structured_error(
    runner: CliRunner, tmp_path: Path, agent: str
) -> None:
    home = tmp_path / f"unreadable-{agent}"
    home.mkdir()
    (home / "sentinel").write_text("unreadable", encoding="utf-8")
    home.chmod(0)
    try:
        try:
            next(home.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("directory permissions cannot be enforced")

        result = _invoke(runner, "anything", home, agent=agent)
        payload = _json(result)

        assert result.exit_code == 1
        assert set(payload) == {"error", "detail"}
        assert payload["error"] == "unreadable_home"
        assert isinstance(payload["detail"], str)
        assert payload["detail"]
        assert "Traceback" not in result.output
    finally:
        home.chmod(0o700)


def test_claude_wrong_typed_record_type_does_not_block_valid_match(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude"
    hostile_id = "face1000-0000-4000-8000-000000000001"
    _write_raw_claude_session(
        home,
        hostile_id,
        str(tmp_path / "hostile"),
        [{"type": [], "sessionId": None}],
    )
    valid_id = "face2000-0000-4000-8000-000000000002"
    _write_claude_session(
        home,
        valid_id,
        str(tmp_path / "valid"),
        "Valid After Wrong Type",
        1_720_001_000.0,
    )

    result = _invoke(runner, "Valid After Wrong Type", home)

    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "message",
    [
        pytest.param([], id="non-dict-message"),
        pytest.param({"content": 17}, id="non-string-or-list-content"),
        pytest.param(
            {"content": [{"type": "text", "text": 17}]},
            id="non-string-text-block",
        ),
    ],
)
def test_claude_hostile_message_metadata_is_isolated(
    runner: CliRunner, tmp_path: Path, message: object
) -> None:
    home = tmp_path / "claude"
    hostile_id = "face5000-0000-4000-8000-000000000005"
    hostile_cwd = str(tmp_path / "hostile")
    _write_raw_claude_session(
        home,
        hostile_id,
        hostile_cwd,
        [
            {
                "type": "user",
                "sessionId": hostile_id,
                "cwd": hostile_cwd,
                "message": message,
            }
        ],
    )
    valid_id = "face6000-0000-4000-8000-000000000006"
    _write_claude_session(
        home,
        valid_id,
        str(tmp_path / "valid"),
        "Valid After Hostile Message",
        1_720_001_010.0,
    )

    result = _invoke(runner, "Valid After Hostile Message", home)

    assert result.exit_code == 0
    assert _json(result)["session_id"] == valid_id
    assert "Traceback" not in result.output


@pytest.mark.parametrize("contents", [b"", b"not-json\n[truncated\n"])
def test_claude_invalid_transcript_does_not_resolve_by_exact_filename(
    runner: CliRunner, tmp_path: Path, contents: bytes
) -> None:
    home = tmp_path / "claude"
    session_id = "face9000-0000-4000-8000-000000000009"
    project = home / "projects" / "-invalid"
    project.mkdir(parents=True)
    transcript = project / f"{session_id}.jsonl"
    transcript.write_bytes(contents)

    result = _invoke(runner, session_id, home)

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"
    assert "Traceback" not in result.output


def test_unreadable_claude_transcript_does_not_resolve_by_filename(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "claude"
    session_id = "facea000-0000-4000-8000-00000000000a"
    transcript = _write_raw_claude_session(
        home,
        session_id,
        str(tmp_path / "unreadable"),
        [{"type": "user", "sessionId": session_id}],
    )
    transcript.chmod(0)
    try:
        try:
            transcript.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("file permissions cannot be enforced")

        result = _invoke(runner, session_id, home)

        assert result.exit_code == 1
        assert _json(result)["error"] == "not_found"
        assert "Traceback" not in result.output
    finally:
        transcript.chmod(0o600)


def test_claude_hostile_huge_int_record_is_isolated(
    runner: CliRunner, claude_home: FakeHome, tmp_path: Path
) -> None:
    """An oversized integer literal skips only its own line.

    ``json.loads`` raises plain ValueError (not JSONDecodeError) for
    integers beyond the int-str conversion limit; one such record
    must neither hide the file's later valid records nor abort
    resolution of the other sessions in the home.
    """
    session_id = "ffff8888-8888-4888-8888-888888888888"
    cwd = str(tmp_path / "huge-int-project")
    session_file = _write_raw_claude_session(
        claude_home.path,
        session_id,
        cwd,
        [{"type": "user", "sessionId": session_id, "cwd": cwd}],
    )
    session_file.write_text(
        "9" * 10_000 + "\n" + session_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    hostile_result = _invoke(runner, session_id, claude_home.path)
    other_result = _invoke(runner, claude_home.ids[2], claude_home.path)

    assert other_result.exit_code == 0
    assert _json(other_result)["session_id"] == claude_home.ids[2]
    assert hostile_result.exit_code == 0
    assert _json(hostile_result)["session_id"] == session_id
    assert "Traceback" not in hostile_result.output
    assert "Traceback" not in other_result.output


@pytest.mark.parametrize(
    "header",
    [
        {"id": None, "timestamp": None, "instructions": None},
        {"id": "", "timestamp": "", "instructions": None, "git": {}},
        {
            "id": "9c9c9999-9999-4999-8999-999999999999",
            "timestamp": "2025-08-27T19:44:13.169Z",
        },
    ],
    ids=["null-values", "empty-strings", "no-legacy-content"],
)
def test_legacy_header_without_valid_fields_is_not_resumable(
    runner: CliRunner, tmp_path: Path, header: dict[str, object]
) -> None:
    """Truncated or hollow legacy headers never expose a session.

    A resumable legacy header needs a nonempty string ``id`` and
    ``timestamp`` plus recognizable legacy content (a ``git`` object
    or string ``instructions``); key presence alone is not enough.
    """
    home = tmp_path / "codex"
    filename_id = "9c9c9999-9999-4999-8999-999999999999"
    _write_legacy_rollout_lines(home, filename_id, [header])

    result = _invoke(runner, filename_id, home, agent="codex")

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"


def test_legacy_header_id_mismatching_filename_is_rejected(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A legacy header claiming a different ID than its filename is
    treated as corrupt rather than resumable under either ID."""
    home = tmp_path / "codex"
    filename_id = "8b8b8888-8888-4888-8888-888888888888"
    header_id = "7a7a7777-7777-4777-8777-777777777777"
    _write_legacy_rollout_lines(
        home,
        filename_id,
        [
            {
                "id": header_id,
                "timestamp": "2025-08-27T19:44:13.169Z",
                "instructions": None,
                "git": {"branch": "main"},
            }
        ],
    )

    filename_result = _invoke(runner, filename_id, home, agent="codex")
    header_result = _invoke(runner, header_id, home, agent="codex")

    assert filename_result.exit_code == 1
    assert _json(filename_result)["error"] == "not_found"
    assert header_result.exit_code == 1
    assert _json(header_result)["error"] == "not_found"


def test_codex_turn_context_only_rollout_is_resolvable(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A rollout whose only valid record is a real turn_context works.

    Genuine turn_context payloads carry turn environment fields
    (cwd, model, approval_policy, sandbox_policy) and no ``type``
    discriminator, and must still validate a truncated rollout.
    """
    home = tmp_path / "codex"
    session_id = "6d6d6666-6666-4666-8666-666666666666"
    _write_codex_session(
        home,
        session_id,
        "/ctx",
        1_720_002_200.0,
        lines=[
            {
                "timestamp": "2026-07-14T10:00:01.000Z",
                "type": "turn_context",
                "payload": {
                    "cwd": "/ctx",
                    "approval_policy": "never",
                    "sandbox_policy": {"type": "workspace-write"},
                    "model": "gpt-5.2-codex",
                    "effort": "high",
                    "summary": "auto",
                },
            }
        ],
    )

    result = _invoke(runner, session_id, home, agent="codex")
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["session_id"] == session_id


def test_codex_type_less_turn_context_garbage_does_not_validate(
    runner: CliRunner, tmp_path: Path
) -> None:
    """turn_context payloads lacking any context field stay invalid."""
    home = tmp_path / "codex"
    session_id = "5e5e5555-5555-4555-8555-555555555555"
    _write_codex_session(
        home,
        session_id,
        "/ctx",
        1_720_002_201.0,
        lines=[
            {"type": "turn_context", "payload": {}},
            {"type": "turn_context", "payload": {"cwd": "   "}},
            {"type": "turn_context", "payload": {"cwd": 7, "model": []}},
        ],
    )

    result = _invoke(runner, session_id, home, agent="codex")

    assert result.exit_code == 1
    assert _json(result)["error"] == "not_found"
