"""End-to-end tests for name-aware ``aichat move`` resolution."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.session_utils import encode_claude_project_path
from tests.resolve_session_helpers import (
    FakeHome,
    _write_claude_session,
    claude_home,
    codex_home,
    runner,
)

__all__ = ["claude_home", "codex_home", "runner"]


def _move_args(
    claude_home: FakeHome,
    codex_home: FakeHome,
    session: str,
    new_project: Path,
    *,
    agent: str | None = None,
) -> list[str]:
    """Build a move invocation constrained to isolated fake homes."""
    args = [
        "--claude-home",
        str(claude_home.path),
        "--codex-home",
        str(codex_home.path),
        "move",
        session,
        str(new_project),
    ]
    if agent is not None:
        args.extend(["--agent", agent])
    return args


def _json_lines(session_file: Path) -> list[object]:
    """Read every JSON object from a fake transcript."""
    return [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]


def _without_cwd(records: list[object], agent: str) -> list[object]:
    """Return a deep copy with only move-managed cwd fields removed."""
    copied = json.loads(json.dumps(records))
    for record in copied:
        if not isinstance(record, dict):
            continue
        if agent == "claude":
            record.pop("cwd", None)
            continue
        record_type = record.get("type")
        payload = record.get("payload")
        if (
            isinstance(record_type, str)
            and record_type
            in {"session_meta", "turn_context", "response_item"}
            and isinstance(payload, dict)
        ):
            payload.pop("cwd", None)
    return copied


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    """Snapshot every relative path and file payload beneath a home."""
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }


def test_move_claude_session_by_name(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A named Claude transcript moves and rewrites all cwd fields."""
    source = claude_home.files[2]
    records = _json_lines(source)
    records[1]["cwd"] = claude_home.directories[2]
    records[2]["cwd"] = claude_home.directories[2]
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
    source.chmod(0o640)
    new_project = tmp_path / "new.project_name"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            "Unique Deployment Review",
            new_project,
            agent="claude",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / source.name
    )
    assert destination.is_file()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
    assert not source.exists()
    moved_records = _json_lines(destination)
    assert len(moved_records) == len(records)
    assert all(
        isinstance(record, dict) and "cwd" in record
        for record in moved_records
    )
    assert all(
        record["cwd"] == str(new_project.resolve())
        for record in moved_records
        if isinstance(record, dict)
    )
    assert _without_cwd(moved_records, "claude") == _without_cwd(
        records, "claude"
    )


def test_move_hex_name_outranks_another_sessions_id_fragment(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Move honors exact-name precedence for an 8+ character hex name."""
    query = "deadbeef"
    named = _write_claude_session(
        claude_home.path,
        "eeee4444-4444-4444-8444-444444444444",
        claude_home.directories[0],
        query,
        1_720_000_000.0,
    )
    fragment = _write_claude_session(
        claude_home.path,
        f"ffff{query}-beef-4444-8444-444444444444",
        claude_home.directories[1],
        "Different session",
        1_720_000_001.0,
    )
    fragment_contents = fragment.read_bytes()
    new_project = tmp_path / "hex-name-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            query,
            new_project,
            agent="claude",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / named.name
    )
    assert destination.is_file()
    assert not named.exists()
    assert fragment.is_file()
    assert fragment.read_bytes() == fragment_contents


def test_move_codex_session_by_name_updates_in_place(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A named Codex rollout is rewritten without changing its path."""
    source = codex_home.files[2]
    records = _json_lines(source)
    records.extend(
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "cwd": codex_home.directories[2],
                },
            },
            {
                "type": "turn_context",
                "payload": {
                    "cwd": codex_home.directories[2],
                    "approval_policy": "never",
                },
            },
            {"type": ["turn_context"], "payload": {"cwd": "unchanged"}},
            {"type": "turn_context", "payload": None},
            {"type": "turn_context", "payload": ["unchanged"]},
            {"type": "turn_context", "payload": {"cwd": "rewrite-me"}},
            None,
            ["non-dict", "record"],
        ]
    )
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
    source.chmod(0o640)
    original_rollouts = set(
        (codex_home.path / "sessions").rglob("*.jsonl")
    )
    new_project = tmp_path / "new-codex-project"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            "Unique Codex Migration",
            new_project,
            agent="codex",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert source.is_file()
    assert stat.S_IMODE(source.stat().st_mode) == 0o640
    assert set((codex_home.path / "sessions").rglob("*.jsonl")) == (
        original_rollouts
    )
    moved_records = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
    ]
    cwd_values = [
        payload["cwd"]
        for record in moved_records
        if isinstance(record, dict)
        if isinstance((payload := record.get("payload")), dict)
        and isinstance(record.get("type"), str)
        and record["type"] in {"session_meta", "turn_context", "response_item"}
        and "cwd" in payload
    ]
    assert cwd_values == [str(new_project.resolve())] * 4
    assert moved_records[-6:-2] == [
        {"type": ["turn_context"], "payload": {"cwd": "unchanged"}},
        {"type": "turn_context", "payload": None},
        {"type": "turn_context", "payload": ["unchanged"]},
        {
            "type": "turn_context",
            "payload": {"cwd": str(new_project.resolve())},
        },
    ]
    assert moved_records[-2:] == [None, ["non-dict", "record"]]
    assert _without_cwd(moved_records, "codex") == _without_cwd(
        records, "codex"
    )


def test_move_codex_id_atomically_replaces_discovered_symlink(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A Codex move replaces its discovered link with a complete file."""
    discovered = codex_home.files[2]
    external = tmp_path / "external-codex-session.jsonl"
    external.write_bytes(discovered.read_bytes())
    original_target = external.read_bytes()
    original_records = _json_lines(external)
    discovered.unlink()
    discovered.symlink_to(external)
    new_project = tmp_path / "atomic-codex-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            codex_home.ids[2],
            new_project,
            agent="codex",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert discovered.is_file()
    assert not discovered.is_symlink()
    assert external.read_bytes() == original_target
    moved_records = _json_lines(discovered)
    assert len(moved_records) == len(original_records)
    assert _without_cwd(moved_records, "codex") == _without_cwd(
        original_records, "codex"
    )
    assert all(
        record["payload"]["cwd"] == str(new_project.resolve())
        for record in moved_records
        if isinstance(record, dict)
        and record.get("type") in {"session_meta", "turn_context"}
        and isinstance(record.get("payload"), dict)
        and "cwd" in record["payload"]
    )
    assert not list(discovered.parent.glob(f".{discovered.stem}.*.tmp"))


def test_move_full_codex_id_rejects_filename_content_mismatch(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A full filename ID cannot select a different content-ID session."""
    misleading_id = codex_home.ids[1]
    misleading_link = codex_home.files[1]
    content_target = codex_home.files[2]
    original_target = content_target.read_bytes()
    misleading_link.unlink()
    misleading_link.symlink_to(content_target)
    connection = sqlite3.connect(codex_home.path / "state_5.sqlite")
    try:
        connection.execute(
            "UPDATE threads SET archived = 1 WHERE id = ?",
            (misleading_id,),
        )
        connection.commit()
    finally:
        connection.close()
    new_project = tmp_path / "codex-mismatch-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            misleading_id,
            new_project,
            agent="codex",
        ),
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Error: Session not found" in result.output
    assert misleading_link.is_symlink()
    assert content_target.is_file()
    assert content_target.read_bytes() == original_target


def test_move_claude_symlink_preserves_target(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Moving an explicit symlink removes only the link, not its target."""
    target = claude_home.files[2]
    original_target = target.read_bytes()
    symlink = tmp_path / "linked-session.jsonl"
    symlink.symlink_to(target)
    new_project = tmp_path / "symlink-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(symlink),
            new_project,
            agent="claude",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / f"{target.stem}.jsonl"
    )
    assert destination.is_file()
    assert not symlink.exists()
    assert target.is_file()
    assert target.read_bytes() == original_target
    moved_records = _json_lines(destination)
    cwd_values = [
        record["cwd"] for record in moved_records if "cwd" in record
    ]
    assert cwd_values == [str(new_project.resolve())] * len(cwd_values)


def test_move_claude_symlink_by_id_preserves_target(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """ID lookup moves the discovered link without unlinking its target."""
    discovered = claude_home.files[2]
    external = tmp_path / "external-session.jsonl"
    external.write_bytes(discovered.read_bytes())
    original_target = external.read_bytes()
    discovered.unlink()
    discovered.symlink_to(external)
    new_project = tmp_path / "id-symlink-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            claude_home.ids[2],
            new_project,
            agent="claude",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / discovered.name
    )
    assert destination.is_file()
    assert not discovered.exists()
    assert external.is_file()
    assert external.read_bytes() == original_target
    moved_records = _json_lines(destination)
    cwd_values = [
        record["cwd"] for record in moved_records if "cwd" in record
    ]
    assert cwd_values == [str(new_project.resolve())] * len(cwd_values)


def test_move_claude_symlink_by_name_preserves_target(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Name lookup moves an in-home link without deleting its target."""
    discovered = claude_home.files[2]
    external = tmp_path / "named-external-session.jsonl"
    external.write_bytes(discovered.read_bytes())
    original_target = external.read_bytes()
    discovered.unlink()
    discovered.symlink_to(external)
    new_project = tmp_path / "name-symlink-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            "Unique Deployment Review",
            new_project,
            agent="claude",
        ),
        input="n\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / discovered.name
    )
    assert destination.is_file()
    assert not discovered.exists()
    assert external.is_file()
    assert external.read_bytes() == original_target


def test_move_name_rejects_two_links_to_external_transcript(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Two in-home links are ambiguous and preserve the external target."""
    first_link = claude_home.files[2]
    external = tmp_path / "shared-named-external.jsonl"
    external.write_bytes(first_link.read_bytes())
    original_target = external.read_bytes()
    first_link.unlink()
    first_link.symlink_to(external)
    second_project = claude_home.path / "projects" / "second-link-project"
    second_project.mkdir()
    second_link = second_project / first_link.name
    second_link.symlink_to(external)
    new_project = tmp_path / "two-links-destination"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            "Unique Deployment Review",
            new_project,
            agent="claude",
        ),
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Error: Ambiguous session 'Unique Deployment Review'" in (
        result.output
    )
    assert str(first_link.absolute()) in result.output
    assert str(second_link.absolute()) in result.output
    assert first_link.is_symlink()
    assert second_link.is_symlink()
    assert external.is_file()
    assert external.read_bytes() == original_target
    encoded = encode_claude_project_path(str(new_project.resolve()))
    assert not (claude_home.path / "projects" / encoded).exists()


def test_ambiguous_move_exits_without_mutation(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """An ambiguous name exits one and leaves every candidate untouched."""
    original_homes = {
        "claude": _tree_snapshot(claude_home.path),
        "codex": _tree_snapshot(codex_home.path),
    }
    new_project = tmp_path / "ambiguous-target"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            "Shared Plan",
            new_project,
            agent="claude",
        ),
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Error: Ambiguous session 'Shared Plan'" in result.output
    assert {
        "claude": _tree_snapshot(claude_home.path),
        "codex": _tree_snapshot(codex_home.path),
    } == original_homes
    encoded = encode_claude_project_path(str(new_project.resolve()))
    destination = claude_home.path / "projects" / encoded
    assert not destination.exists()


def test_move_rejects_direct_path_outside_agent_constraint(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A direct Codex path cannot be relabeled and moved as Claude."""
    source = codex_home.files[2]
    source_contents = source.read_bytes()
    new_project = tmp_path / "wrong-agent-target"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
            agent="claude",
        ),
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "is a codex session, but --agent claude was specified" in (
        result.output
    )
    assert source.is_file()
    assert source.read_bytes() == source_contents
    encoded = encode_claude_project_path(str(new_project.resolve()))
    assert not (claude_home.path / "projects" / encoded).exists()


def test_move_preserves_pathologically_nested_line(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Unparseable nested JSON is preserved while valid metadata is moved."""
    session_id = "11111111-1111-4111-8111-111111111111"
    nested = "[" * 1100 + "]" * 1100 + "\n"
    metadata = json.dumps(
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/tmp"},
        }
    )
    source = tmp_path / f"{session_id}.jsonl"
    source.write_text(nested + metadata + "\n", encoding="utf-8")
    new_project = tmp_path / "nested-target"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    assert source.read_text(encoding="utf-8").startswith(nested)
    moved_records = source.read_text(encoding="utf-8").splitlines()
    assert json.loads(moved_records[1])["payload"]["cwd"] == str(
        new_project.resolve()
    )


def test_move_preserves_undecodable_line_byte_for_byte(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Moving a transcript round-trips an undecodable line unchanged."""
    session_id = "11111111-1111-4111-8111-111111111111"
    source = tmp_path / f"{session_id}.jsonl"
    valid_record = json.dumps(
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": "/tmp/old",
            "message": {"role": "user", "content": "x"},
        }
    ).encode()
    source.write_bytes(b"\xff\n" + valid_record + b"\n")
    new_project = tmp_path / "new-project"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    encoded = encode_claude_project_path(str(new_project.resolve()))
    moved = claude_home.path / "projects" / encoded / source.name
    assert moved.read_bytes().startswith(b"\xff\n")
    assert str(new_project.resolve()).encode() in moved.read_bytes()


def test_move_updates_parseable_line_containing_undecodable_byte(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Moving rewrites cwd without changing an undecodable string byte."""
    session_id = "11111111-1111-4111-8111-111111111111"
    source = tmp_path / f"{session_id}.jsonl"
    source.write_bytes(
        b'{"type":"user","sessionId":"'
        + session_id.encode()
        + b'","cwd":"/tmp/old","message":{"role":"user",'
        + b'"content":"bad \xff byte"},'
        + b'"legit":"\\u005f\\u005faichat_raw_byte_dcff_0'
        + b'\\u005f\\u005f"}\n'
    )
    new_project = tmp_path / "new-project"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    encoded = encode_claude_project_path(str(new_project.resolve()))
    moved = claude_home.path / "projects" / encoded / source.name
    contents = moved.read_bytes()
    assert b"bad \xff byte" in contents
    assert b'"legit": "__aichat_raw_byte_dcff_0__"' in contents
    assert str(new_project.resolve()).encode() in contents
    assert b"/tmp/old" not in contents


def test_move_preserves_escaped_lone_surrogate_as_valid_json(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Moving leaves an escaped surrogate encoded as valid UTF-8 JSON."""
    session_id = "11111111-1111-4111-8111-111111111111"
    source = tmp_path / f"{session_id}.jsonl"
    source.write_bytes(
        b'{"type":"user","sessionId":"'
        + session_id.encode()
        + b'","cwd":"/tmp/old","message":{"role":"user",'
        + b'"content":"escaped \\udcff"}}\n'
    )
    new_project = tmp_path / "new-project"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    encoded = encode_claude_project_path(str(new_project.resolve()))
    moved = claude_home.path / "projects" / encoded / source.name
    contents = moved.read_bytes()
    parsed = json.loads(contents.decode("utf-8"))
    assert parsed["message"]["content"] == "escaped \udcff"
    assert b"escaped \\udcff" in contents
    assert b"\xff" not in contents


def test_move_uses_claude_content_id_for_destination_and_resume(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Claude move uses transcript identity when the supplied name differs."""
    session_id = "11111111-1111-4111-8111-111111111111"
    source = tmp_path / "backup.jsonl"
    source.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": "/tmp/old",
                "message": {"role": "user", "content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    new_project = tmp_path / "content-id-target"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    destination = (
        claude_home.path
        / "projects"
        / encode_claude_project_path(str(new_project.resolve()))
        / f"{session_id}.jsonl"
    )
    assert destination.is_file()
    assert not source.exists()
    assert not destination.with_name("backup.jsonl").exists()
    assert "claude --resume" in result.output
    assert session_id in result.output


def test_move_rejects_unsafe_claude_content_id(
    tmp_path: Path,
    runner: CliRunner,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Claude move rejects a content identity that is unsafe as a filename."""
    source = tmp_path / "backup.jsonl"
    source_contents = (
        json.dumps(
            {
                "type": "user",
                "sessionId": "../../escaped",
                "cwd": "/tmp/old",
                "message": {"role": "user", "content": "hello"},
            }
        )
        + "\n"
    )
    source.write_text(source_contents, encoding="utf-8")
    new_project = tmp_path / "unsafe-id-target"
    new_project.mkdir()

    result = runner.invoke(
        main,
        _move_args(
            claude_home,
            codex_home,
            str(source),
            new_project,
        ),
        catch_exceptions=False,
        input="n\n",
    )

    assert result.exit_code != 0
    assert "Invalid sessionId in Claude transcript" in result.output
    assert source.read_text(encoding="utf-8") == source_contents
    assert not (claude_home.path / "escaped.jsonl").exists()
