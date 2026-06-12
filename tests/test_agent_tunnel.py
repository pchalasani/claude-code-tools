"""Tests for agent_tunnel: registry, store, session parsing, the >share hook,
chunking, flag building, config. Real files in tmp dirs — no mocks. Live
backend paths (real claude/tmux/Discord) are exercised manually via
`agent-tunnel ask`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from claude_code_tools.agent_tunnel.backends import (
    HeadlessBackend,
    _window_name,
    build_claude_flags,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig, load_config
from claude_code_tools.agent_tunnel.discord_bot import (
    is_close_command,
    is_list_command,
    resolve_token,
    split_chunks,
)
from claude_code_tools.agent_tunnel.registry import (
    Registry,
    PublishRecord,
    derive_handle,
    sanitize_label,
)
from claude_code_tools.agent_tunnel.session import (
    extract_answer,
    find_latest_session,
    make_marker,
    transcript_dir,
)
from claude_code_tools.agent_tunnel.store import TunnelStore
from claude_code_tools.session_utils import encode_claude_project_path

SID_A = "11111111-2222-3333-4444-555555555555"
SID_B = "66666666-7777-8888-9999-aaaaaaaaaaaa"
SID_FORK = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"

HOOK = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "agent-tunnel"
    / "hooks"
    / "share_hook.py"
)


def _user_entry(text: str, sid: str) -> dict:
    return {
        "type": "user",
        "sessionId": sid,
        "message": {"role": "user", "content": text},
    }


def _assistant_entry(blocks: list[dict], sid: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": sid,
        "message": {"role": "assistant", "content": blocks},
    }


def _write_session(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    project.mkdir()
    claude_home = tmp_path / "claude_home"
    tdir = claude_home / "projects" / encode_claude_project_path(str(project))
    tdir.mkdir(parents=True)
    return project, claude_home


# ----------------------------------------------------------------- registry


def test_sanitize_and_derive() -> None:
    assert sanitize_label("Payments Auth!") == "payments-auth"
    assert sanitize_label("  --Foo__Bar-- ") == "foo-bar"
    assert sanitize_label("!!!") is None
    assert sanitize_label("a") is None  # too short (needs >= 2)
    assert derive_handle(SID_A) == "111111"


def test_registry_roundtrip(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "registry.json")
    reg.upsert(
        PublishRecord(
            handle="pay", session_id=SID_A, cwd="/p", label="payments"
        )
    )
    got = reg.get("pay")
    assert got is not None and got.session_id == SID_A
    assert reg.get("PAY") is not None  # case-insensitive
    assert reg.get("missing") is None

    assert reg.revoke("pay") is True
    assert reg.get("pay") is None  # revoked hidden
    assert reg.active() == []
    assert reg.revoke("pay-nope") is False


def test_registry_backfills_config_dir(tmp_path: Path) -> None:
    # An old record with no config_dir but a transcript path gets it derived.
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "records": {
                    "h": {
                        "handle": "h",
                        "session_id": SID_A,
                        "cwd": "/proj",
                        "transcript_path": (
                            f"/u/.claude-rja/projects/-proj/{SID_A}.jsonl"
                        ),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    rec = Registry(path).get("h")
    assert rec is not None and rec.config_dir == "/u/.claude-rja"


# -------------------------------------------------------------- >share hook


def _run_hook(
    prompt: str,
    session_id: str,
    cwd: str,
    registry: Path,
    transcript_path: str = "",
) -> dict:
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": session_id,
            "prompt": prompt,
            "cwd": cwd,
            "transcript_path": transcript_path,
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_share_hook_publishes_current_session(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"

    # Non-trigger prompt passes through silently (no output).
    out = _run_hook("hello there", SID_A, "/work/proj", reg_path)
    assert out == {}
    assert not reg_path.exists()

    # >share publishes THIS session, detecting its config dir from the
    # transcript path (.../<config-dir>/projects/...).
    out = _run_hook(
        ">share",
        SID_A,
        "/work/proj",
        reg_path,
        transcript_path=f"/home/u/.claude-work/projects/-work-proj/{SID_A}.jsonl",
    )
    assert out["decision"] == "block"
    reg = Registry(reg_path)
    handle = derive_handle(SID_A)
    rec = reg.get(handle)
    assert rec is not None
    assert rec.session_id == SID_A and rec.cwd == "/work/proj"
    assert rec.config_dir == "/home/u/.claude-work"

    # Idempotent: same session, same handle.
    _run_hook(">share", SID_A, "/work/proj", reg_path)
    assert len(reg.active()) == 1

    # A different session in the SAME folder gets a DIFFERENT handle.
    _run_hook(">share", SID_B, "/work/proj", reg_path)
    assert len(reg.active()) == 2
    assert reg.get(derive_handle(SID_B)) is not None

    # Labels, status, revoke.
    _run_hook(">share my-label", SID_A, "/work/proj", reg_path)
    assert reg.get("my-label") is not None
    status = _run_hook(">share status", SID_A, "/work/proj", reg_path)
    assert "my-label" in status["reason"]
    _run_hook(">share off", SID_A, "/work/proj", reg_path)
    assert reg.get("my-label") is None


def test_share_hook_write_access(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    _run_hook(">share --write wtest", SID_A, "/p", reg_path)
    rec = Registry(reg_path).get("wtest")
    assert rec is not None and rec.access == "write"

    # re-share with no flag preserves the existing write access.
    _run_hook(">share wtest", SID_A, "/p", reg_path)
    rec = Registry(reg_path).get("wtest")
    assert rec is not None and rec.access == "write"

    # --read downgrades back to read-only.
    _run_hook(">share --read wtest", SID_A, "/p", reg_path)
    rec = Registry(reg_path).get("wtest")
    assert rec is not None and rec.access == "read"


def test_share_hook_label_collision(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    _run_hook(">share shared", SID_A, "/p", reg_path)
    out = _run_hook(">share shared", SID_B, "/q", reg_path)
    assert "already used" in out["reason"]
    # Original owner still holds it.
    held = Registry(reg_path).get("shared")
    assert held is not None and held.session_id == SID_A


# -------------------------------------------------------------------- store


def test_store_bind_and_followup(tmp_path: Path) -> None:
    store = TunnelStore(tmp_path / "state.json")
    rec = store.bind("th:1", "pay", SID_A, "/p", "tmux", asker="alice")
    assert rec.fork_session_id == ""  # pending until first answer

    # Re-bind is a no-op (keeps the original).
    again = store.bind("th:1", "other", SID_B, "/q", "tmux")
    assert again.expert_session_id == SID_A

    # Record a fork id (first answer completed).
    rec.fork_session_id = SID_FORK
    store.upsert(rec)
    reloaded = TunnelStore(tmp_path / "state.json")
    got = reloaded.get("th:1")
    assert got is not None
    assert got.fork_session_id == SID_FORK and got.handle == "pay"
    assert SID_FORK in reloaded.known_fork_ids()


def test_headless_requires_binding(tmp_path: Path) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    backend = HeadlessBackend(cfg, store)
    try:
        backend.ask("th:unbound", "hi?")
        assert False, "expected BackendError"
    except Exception as exc:  # BackendError
        assert "not bound" in str(exc)


# ----------------------------------------------------- session discovery


def test_find_latest_session_excludes_forks(tmp_path: Path) -> None:
    project, claude_home = _make_project(tmp_path)
    tdir = transcript_dir(project, claude_home)
    older = tdir / f"{SID_A}.jsonl"
    _write_session(older, [_user_entry("hello", SID_A)])
    newer_fork = tdir / f"{SID_B}.jsonl"
    _write_session(newer_fork, [_user_entry("fork", SID_B)])
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer_fork, (now - 10, now - 10))

    found = find_latest_session(
        project, exclude={SID_B}, claude_home=claude_home
    )
    assert found is not None and found.stem == SID_A
    found = find_latest_session(project, exclude=set(), claude_home=claude_home)
    assert found is not None and found.stem == SID_B


# ------------------------------------------------------ answer extraction


def test_extract_answer_multipart_with_tools(tmp_path: Path) -> None:
    question = "What does the frobnicator do?\nDetails please."
    marker = make_marker(question)
    assert marker == "What does the frobnicator do?"
    session = tmp_path / f"{SID_FORK}.jsonl"
    _write_session(
        session,
        [
            _user_entry("earlier", SID_FORK),
            _assistant_entry([{"type": "text", "text": "old"}], SID_FORK),
            _user_entry(question, SID_FORK),
            _assistant_entry(
                [
                    {"type": "text", "text": "Part one."},
                    {"type": "tool_use", "id": "t1", "name": "Read"},
                ],
                SID_FORK,
            ),
            {
                "type": "user",
                "sessionId": SID_FORK,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1"}],
                },
            },
            _assistant_entry([{"type": "text", "text": "Part two."}], SID_FORK),
        ],
    )
    complete, text = extract_answer(session, marker)
    assert complete
    assert "Part one." in text and "Part two." in text
    assert "old" not in text


def test_extract_answer_incomplete(tmp_path: Path) -> None:
    session = tmp_path / f"{SID_FORK}.jsonl"
    question = "Pending question"
    marker = make_marker(question)
    _write_session(session, [_user_entry("other", SID_FORK)])
    assert extract_answer(session, marker) == (False, "")
    _write_session(session, [_user_entry(question, SID_FORK)])
    assert extract_answer(session, marker) == (False, "")
    _write_session(
        session,
        [
            _user_entry(question, SID_FORK),
            _assistant_entry(
                [
                    {"type": "text", "text": "Working"},
                    {"type": "tool_use", "id": "t1", "name": "Grep"},
                ],
                SID_FORK,
            ),
        ],
    )
    complete, _ = extract_answer(session, marker)
    assert not complete


# ------------------------------------------------------------- chunking


def test_split_chunks_roundtrip() -> None:
    assert split_chunks("") == []
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(100))
    chunks = split_chunks(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text
    long_line = "y" * 4500
    chunks = split_chunks(long_line, limit=2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == long_line


# ------------------------------------------------------------ claude flags


def test_ensure_folder_trusted(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.trust import (
        TRUST_KEYS,
        ensure_folder_trusted,
    )

    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps({"projects": {"/other": {"foo": 1}}, "top": 5}),
        encoding="utf-8",
    )
    proj = tmp_path / "myproj"
    proj.mkdir()

    assert ensure_folder_trusted(proj, cfg) is True
    data = json.loads(cfg.read_text())
    assert data["top"] == 5  # unrelated content preserved
    assert data["projects"]["/other"] == {"foo": 1}  # other projects preserved
    assert all(data["projects"][str(proj)][k] is True for k in TRUST_KEYS)

    assert ensure_folder_trusted(proj, cfg) is False  # idempotent

    fresh = tmp_path / "fresh.json"  # missing file -> created
    assert ensure_folder_trusted(proj, fresh) is True
    assert json.loads(fresh.read_text())["projects"][str(proj)][
        "hasTrustDialogAccepted"
    ]

    bad = tmp_path / "bad.json"  # corrupt -> left untouched
    bad.write_text("{not json", encoding="utf-8")
    assert ensure_folder_trusted(proj, bad) is False
    assert bad.read_text() == "{not json"


def test_is_close_command() -> None:
    for ok in ("!done", "!close", "!end", "  !DONE ", "!End"):
        assert is_close_command(ok)
    for no in ("done", "!finished", "!done now", "what is done?", ""):
        assert not is_close_command(no)


def test_is_list_command() -> None:
    for ok in ("!list", "!handles", "  !LIST ", "!Handles"):
        assert is_list_command(ok)
    for no in ("list", "!list me", "what's the list?", ""):
        assert not is_list_command(no)


def test_window_name() -> None:
    w = _window_name("3dd5d0", "th:288325525675")
    assert w.startswith("3dd5d0-") and " " not in w
    # dashes in a label handle are preserved.
    assert _window_name("payments-auth", "th:99").startswith("payments-auth-")
    # unique per thread even with the same handle.
    assert _window_name("h", "th:1111") != _window_name("h", "th:2222")


def test_resolve_token(tmp_path: Path, monkeypatch) -> None:
    cfg = TunnelConfig()
    cfg.discord.token_env = "AGENT_TUNNEL_TEST_TOKEN_X"
    monkeypatch.delenv("AGENT_TUNNEL_TEST_TOKEN_X", raising=False)
    assert resolve_token(cfg) == ""  # nothing set

    tf = tmp_path / "tok.txt"
    tf.write_text("file-token-123\n", encoding="utf-8")
    cfg.discord.token_file = str(tf)
    assert resolve_token(cfg) == "file-token-123"  # file fallback

    monkeypatch.setenv("AGENT_TUNNEL_TEST_TOKEN_X", "env-token-999")
    assert resolve_token(cfg) == "env-token-999"  # env wins


def test_build_claude_flags() -> None:
    cfg = TunnelConfig()
    flags = build_claude_flags(cfg, resume_id=SID_A, fork=True)
    joined = " ".join(flags)
    assert f"--resume {SID_A}" in joined
    assert "--fork-session" in joined
    assert "--allowedTools Read,Grep,Glob" in joined
    assert "--disallowedTools" in joined and "Bash" in joined
    assert "--permission-mode dontAsk" in joined
    flags = build_claude_flags(cfg, resume_id=SID_B, fork=False)
    assert "--fork-session" not in flags

    # write access adds file tools but never Bash.
    w = build_claude_flags(cfg, resume_id=SID_A, fork=False, access="write")
    allowed = w[w.index("--allowedTools") + 1]
    disallowed = w[w.index("--disallowedTools") + 1]
    assert "Write" in allowed and "Edit" in allowed
    assert "Bash" in disallowed and "Write" not in disallowed


# --------------------------------------------------------------- config


def test_load_config_overrides(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
[tunnel]
backend = "headless"

[discord]
channel_ids = [111]

[limits]
max_concurrent = 5
""",
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.backend == "headless"
    assert cfg.discord.channel_ids == [111]
    assert cfg.limits.max_concurrent == 5
    cfg = load_config(path=cfg_file, backend="tmux", channel_ids=[222])
    assert cfg.backend == "tmux"
    assert cfg.discord.channel_ids == [222]
