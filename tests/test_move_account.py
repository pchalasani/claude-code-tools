"""Tests for aichat move-account (moving sessions between config dirs)."""

import json
from pathlib import Path

import pytest

from claude_code_tools.move_account import (
    detect_home_kind,
    find_codex_sessions_in_home,
    find_sessions_in_home,
    move_codex_session_between_homes,
    move_session_between_homes,
    resume_command,
)

ENC = "-Users-me-Git-myproj"
CWD = "/Users/me/Git/myproj"
UUID_A = "aaaa1111-2222-3333-4444-555566667777"
UUID_B = "bbbb1111-2222-3333-4444-555566667777"


def _write_session(
    home: Path,
    uuid: str,
    title: str = "",
    sidecar: bool = False,
) -> Path:
    """Create a minimal Claude transcript (and optional sidecar dir)."""
    proj = home / "projects" / ENC
    proj.mkdir(parents=True, exist_ok=True)
    lines = []
    if title:
        lines.append(
            {"type": "custom-title", "customTitle": title, "sessionId": uuid}
        )
    lines.append(
        {
            "type": "user",
            "cwd": CWD,
            "sessionId": uuid,
            "message": {"role": "user", "content": "hi"},
        }
    )
    path = proj / f"{uuid}.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    if sidecar:
        sub = proj / uuid / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-abc.jsonl").write_text("{}\n")
    return path


@pytest.fixture()
def homes(tmp_path: Path):
    """A source home with two sessions, and an empty target home."""
    src = tmp_path / "claude-work"
    dst = tmp_path / "claude-personal"
    (dst / "projects").mkdir(parents=True)
    _write_session(src, UUID_A, title="cowrite-fable-21jul2026", sidecar=True)
    _write_session(src, UUID_B, title="other-session")
    return src, dst


def test_find_by_exact_name(homes):
    src, _ = homes
    got = find_sessions_in_home(src, "cowrite-fable-21jul2026")
    assert [c.session_id for c in got] == [UUID_A]


def test_find_by_name_substring(homes):
    src, _ = homes
    got = find_sessions_in_home(src, "21jul")
    assert [c.session_id for c in got] == [UUID_A]


def test_find_by_uuid_prefix(homes):
    src, _ = homes
    got = find_sessions_in_home(src, "bbbb1111")
    assert [c.session_id for c in got] == [UUID_B]


def test_find_no_match(homes):
    src, _ = homes
    assert find_sessions_in_home(src, "nope-nothing") == []


def test_move_relocates_transcript_and_sidecar(homes):
    src, dst = homes
    session = src / "projects" / ENC / f"{UUID_A}.jsonl"
    result = move_session_between_homes(session, src, dst)

    dest = dst / "projects" / ENC / f"{UUID_A}.jsonl"
    assert dest.exists()
    assert result.dest_file == dest.resolve()
    assert result.cwd == CWD
    assert result.sidecar_moved
    assert (dst / "projects" / ENC / UUID_A / "subagents").is_dir()
    # source fully removed
    assert not session.exists()
    assert not (src / "projects" / ENC / UUID_A).exists()
    # content survived intact
    assert "cowrite-fable-21jul2026" in dest.read_text()


def test_move_keep_leaves_source(homes):
    src, dst = homes
    session = src / "projects" / ENC / f"{UUID_A}.jsonl"
    result = move_session_between_homes(session, src, dst, keep=True)
    assert result.kept_source
    assert session.exists()
    assert (src / "projects" / ENC / UUID_A).is_dir()
    assert (dst / "projects" / ENC / f"{UUID_A}.jsonl").exists()


def test_move_collision_rejected(homes):
    src, dst = homes
    _write_session(dst, UUID_A, title="already-here")
    session = src / "projects" / ENC / f"{UUID_A}.jsonl"
    with pytest.raises(ValueError, match="already exists"):
        move_session_between_homes(session, src, dst)
    # source untouched on failure
    assert session.exists()


def test_move_rejects_file_outside_home(homes, tmp_path):
    src, dst = homes
    stray = tmp_path / "stray.jsonl"
    stray.write_text("{}\n")
    with pytest.raises(ValueError, match="not inside source home"):
        move_session_between_homes(stray, src, dst)


def test_resume_command_nondefault_home(homes):
    src, dst = homes
    session = src / "projects" / ENC / f"{UUID_A}.jsonl"
    result = move_session_between_homes(session, src, dst)
    cmd = resume_command(result, dst)
    assert cmd == (
        f"cd {CWD} && CLAUDE_CONFIG_DIR={dst.resolve()} "
        f"claude --resume {UUID_A}"
    )


CODEX_UUID = "019ca600-21fa-7d40-8560-d665abfca2fd"
CODEX_STEM = f"rollout-2026-06-10T14-12-32-{CODEX_UUID}"


def _write_codex_session(
    home: Path, uuid: str, stem: str, thread_name: str = ""
) -> Path:
    """Create a minimal Codex rollout file (and index entry)."""
    day = home / "sessions" / "2026" / "06" / "10"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"{stem}.jsonl"
    meta = {
        "timestamp": "2026-06-10T14:12:32.000Z",
        "type": "session_meta",
        "payload": {"id": uuid, "cwd": CWD},
    }
    path.write_text(json.dumps(meta) + "\n")
    if thread_name:
        entry = {
            "id": uuid,
            "thread_name": thread_name,
            "updated_at": "2026-06-10T14:12:33.000Z",
        }
        with (home / "session_index.jsonl").open("a") as handle:
            handle.write(json.dumps(entry) + "\n")
    return path


@pytest.fixture()
def codex_homes(tmp_path: Path):
    """A source Codex home with one named session; empty target home."""
    src = tmp_path / "codex-work"
    dst = tmp_path / "codex-personal"
    (dst / "sessions").mkdir(parents=True)
    _write_codex_session(
        src, CODEX_UUID, CODEX_STEM, thread_name="my-codex-thread"
    )
    return src, dst


def test_detect_home_kind(homes, codex_homes):
    claude_src, _ = homes
    codex_src, _ = codex_homes
    assert detect_home_kind(claude_src) == "claude"
    assert detect_home_kind(codex_src) == "codex"


def test_codex_find_by_thread_name(codex_homes):
    src, _ = codex_homes
    got = find_codex_sessions_in_home(src, "my-codex-thread")
    assert [c.session_id for c in got] == [CODEX_UUID]
    assert got[0].title == "my-codex-thread"


def test_codex_find_by_uuid_prefix(codex_homes):
    src, _ = codex_homes
    got = find_codex_sessions_in_home(src, "019ca600")
    assert [c.session_id for c in got] == [CODEX_UUID]


def test_codex_move_relocates_rollout_and_index_entry(codex_homes):
    src, dst = codex_homes
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    result = move_codex_session_between_homes(session, src, dst)

    dest = dst / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    assert dest.exists()
    assert result.agent == "codex"
    assert result.session_id == CODEX_UUID
    assert result.cwd == CWD
    assert result.index_entry_moved
    assert not session.exists()
    # thread name transferred to target index, removed from source
    assert "my-codex-thread" in (dst / "session_index.jsonl").read_text()
    assert "my-codex-thread" not in (
        (src / "session_index.jsonl").read_text()
    )


def test_codex_move_keep_leaves_source(codex_homes):
    src, dst = codex_homes
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    result = move_codex_session_between_homes(session, src, dst, keep=True)
    assert result.kept_source
    assert session.exists()
    assert "my-codex-thread" in (src / "session_index.jsonl").read_text()
    assert "my-codex-thread" in (dst / "session_index.jsonl").read_text()


def test_codex_move_collision_rejected(codex_homes):
    src, dst = codex_homes
    _write_codex_session(dst, CODEX_UUID, CODEX_STEM)
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    with pytest.raises(ValueError, match="already exists"):
        move_codex_session_between_homes(session, src, dst)
    assert session.exists()


def test_codex_move_uuid_collision_other_path_rejected(codex_homes):
    src, dst = codex_homes
    other_stem = f"rollout-2026-06-11T09-00-00-{CODEX_UUID}"
    day = dst / "sessions" / "2026" / "06" / "11"
    day.mkdir(parents=True)
    (day / f"{other_stem}.jsonl").write_text("{}\n")
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    with pytest.raises(ValueError, match="already exists"):
        move_codex_session_between_homes(session, src, dst)


def test_codex_resume_command_nondefault_home(codex_homes):
    src, dst = codex_homes
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    result = move_codex_session_between_homes(session, src, dst)
    cmd = resume_command(result, dst)
    assert cmd == (
        f"cd {CWD} && CODEX_HOME={dst.resolve()} "
        f"codex resume {CODEX_UUID}"
    )


def test_run_move_account_autodetects_codex(codex_homes, capsys):
    from claude_code_tools.move_account import run_move_account

    src, dst = codex_homes
    run_move_account("my-codex-thread", str(dst), str(src), keep=False)
    out = capsys.readouterr().out
    assert "Moving codex session" in out
    assert (
        dst / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    ).exists()


def test_run_move_account_agent_mismatch_errors(homes, codex_homes, capsys):
    from claude_code_tools.move_account import run_move_account

    claude_src, _ = homes
    _, codex_dst = codex_homes
    with pytest.raises(SystemExit):
        run_move_account(
            "cowrite-fable-21jul2026",
            str(codex_dst),
            str(claude_src),
            keep=False,
        )
    err = capsys.readouterr().err
    assert "claude home" in err and "codex home" in err


@pytest.fixture()
def fake_homedir(tmp_path, monkeypatch):
    """Point HOME at tmp_path and clear env home overrides."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return tmp_path


def test_auto_from_finds_session_in_sibling_home(fake_homedir, capsys):
    from claude_code_tools.move_account import run_move_account

    src = fake_homedir / ".claude-work"
    dst = fake_homedir / ".claude"
    (dst / "projects").mkdir(parents=True)
    _write_session(src, UUID_A, title="cowrite-fable-21jul2026")

    run_move_account(
        "cowrite-fable-21jul2026", str(dst), None, keep=False
    )
    out = capsys.readouterr().out
    assert "Moving claude session" in out
    assert str(src) in out
    assert (dst / "projects" / ENC / f"{UUID_A}.jsonl").exists()
    assert not (src / "projects" / ENC / f"{UUID_A}.jsonl").exists()


def test_auto_from_uses_env_home(fake_homedir, monkeypatch, capsys):
    from claude_code_tools.move_account import run_move_account

    src = fake_homedir / "elsewhere" / "claude-work"
    dst = fake_homedir / ".claude"
    (dst / "projects").mkdir(parents=True)
    _write_session(src, UUID_A, title="cowrite-fable-21jul2026")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(src))

    run_move_account("21jul2026", str(dst), None, keep=False)
    assert (dst / "projects" / ENC / f"{UUID_A}.jsonl").exists()


def test_auto_from_ambiguous_across_homes_errors(fake_homedir, capsys):
    from claude_code_tools.move_account import run_move_account

    src1 = fake_homedir / ".claude-work"
    src2 = fake_homedir / ".claude-other"
    dst = fake_homedir / ".claude"
    (dst / "projects").mkdir(parents=True)
    _write_session(src1, UUID_A, title="same-name")
    _write_session(src2, UUID_B, title="same-name")

    with pytest.raises(SystemExit):
        run_move_account("same-name", str(dst), None, keep=False)
    err = capsys.readouterr().err
    assert "multiple" in err
    assert str(src1) in err and str(src2) in err
    assert "--from" in err


def test_auto_from_excludes_target_home(fake_homedir, capsys):
    from claude_code_tools.move_account import run_move_account

    # session exists ONLY in the target home: nothing to move, and
    # the target itself is never searched as a source
    dst = fake_homedir / ".claude"
    _write_session(dst, UUID_A, title="already-there")

    with pytest.raises(SystemExit):
        run_move_account("already-there", str(dst), None, keep=False)
    err = capsys.readouterr().err
    assert "no local claude config dirs found to search" in err


def test_resume_command_explicit_even_for_default_home(
    fake_homedir, capsys
):
    """Pasted resume commands must be immune to lingering env vars."""
    from claude_code_tools.move_account import run_move_account

    src = fake_homedir / ".claude-work"
    dst = fake_homedir / ".claude"
    (dst / "projects").mkdir(parents=True)
    _write_session(src, UUID_A, title="cowrite-fable-21jul2026")

    run_move_account("21jul2026", str(dst), None, keep=False)
    out = capsys.readouterr().out
    assert f"CLAUDE_CONFIG_DIR={dst}" in out
    assert f"claude --resume {UUID_A}" in out


def test_auto_from_codex_sibling_home(fake_homedir, capsys):
    from claude_code_tools.move_account import run_move_account

    src = fake_homedir / ".codex-work"
    dst = fake_homedir / ".codex"
    (dst / "sessions").mkdir(parents=True)
    _write_codex_session(
        src, CODEX_UUID, CODEX_STEM, thread_name="my-codex-thread"
    )

    run_move_account("my-codex-thread", str(dst), None, keep=False)
    out = capsys.readouterr().out
    assert "Moving codex session" in out
    assert (
        dst / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    ).exists()


def test_cli_registered():
    from click.testing import CliRunner

    from claude_code_tools.aichat import main

    runner = CliRunner()
    res = runner.invoke(main, ["move-account", "--help"])
    assert res.exit_code == 0
    assert "different account" in res.output

def test_exact_name_beats_uuid_prefix(tmp_path):
    """A session named like another session's UUID prefix wins."""
    src = tmp_path / "claude-src"
    _write_session(src, UUID_A)  # UUID starts with "aaaa"
    _write_session(src, UUID_B, title="aaaa")
    got = find_sessions_in_home(src, "aaaa")
    assert [c.session_id for c in got] == [UUID_B]


def test_codex_index_append_repairs_missing_newline(codex_homes):
    """Appending to a target index lacking a trailing newline must not
    concatenate two JSON records onto one line."""
    src, dst = codex_homes
    existing = {"id": "0" * 36, "thread_name": "pre-existing"}
    (dst / "session_index.jsonl").write_text(
        json.dumps(existing)  # no trailing newline
    )
    session = (
        src / "sessions" / "2026" / "06" / "10" / f"{CODEX_STEM}.jsonl"
    )
    move_codex_session_between_homes(session, src, dst)
    lines = (dst / "session_index.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in lines if line.strip()]
    assert {e["thread_name"] for e in parsed} == {
        "pre-existing", "my-codex-thread"
    }


def test_resume_command_quotes_paths_with_spaces(tmp_path):
    from claude_code_tools.move_account import MoveResult

    result = MoveResult(
        source_file=tmp_path / "a.jsonl",
        dest_file=tmp_path / "b.jsonl",
        sidecar_moved=False,
        session_id=UUID_A,
        cwd="/Users/me/My Project",
        kept_source=False,
    )
    cmd = resume_command(result, tmp_path)
    assert "cd '/Users/me/My Project' &&" in cmd

def test_matching_is_case_insensitive(tmp_path):
    """Uppercase UUID fragments and differently cased names resolve."""
    src = tmp_path / "claude-src"
    _write_session(src, UUID_A, title="My-Session")
    got = find_sessions_in_home(src, UUID_A.upper())
    assert [c.session_id for c in got] == [UUID_A]
    got = find_sessions_in_home(src, "AAAA1111")
    assert [c.session_id for c in got] == [UUID_A]
    got = find_sessions_in_home(src, "my-session")
    assert [c.session_id for c in got] == [UUID_A]


def test_cased_exact_name_still_beats_uuid_prefix(tmp_path):
    """Exact-name tier holds even when the query casing differs."""
    src = tmp_path / "claude-src"
    _write_session(src, UUID_A)  # UUID starts with "aaaa"
    _write_session(src, UUID_B, title="AAAA")
    got = find_sessions_in_home(src, "aaaa")
    assert [c.session_id for c in got] == [UUID_B]
