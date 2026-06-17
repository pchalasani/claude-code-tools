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


def test_share_hook_bash_access(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    out = _run_hook(
        ">share --dangerously-allow-bash btest", SID_A, "/p", reg_path
    )
    rec = Registry(reg_path).get("btest")
    assert rec is not None and rec.access == "bash"
    assert "BASH access" in out["reason"]

    # re-share with no flag preserves the existing bash access.
    _run_hook(">share btest", SID_A, "/p", reg_path)
    rec = Registry(reg_path).get("btest")
    assert rec is not None and rec.access == "bash"

    # --read downgrades all the way back to read-only.
    _run_hook(">share --read btest", SID_A, "/p", reg_path)
    rec = Registry(reg_path).get("btest")
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


def test_count_turns(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.session import count_turns

    f = tmp_path / "s.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": "q1"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "a1"}],
            },
        },
        {"type": "user", "message": {"role": "user", "content": "q2"}},
        {  # meta entries don't count
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "meta"},
        },
        {"type": "summary", "summary": "x"},  # nor summary lines
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert count_turns(f) == 2


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

    # bash access promotes Bash into allowed; network tools still blocked.
    b = build_claude_flags(cfg, resume_id=SID_A, fork=False, access="bash")
    b_allowed = b[b.index("--allowedTools") + 1]
    b_disallowed = b[b.index("--disallowedTools") + 1]
    assert "Bash" in b_allowed and "Write" in b_allowed
    assert "Bash" not in b_disallowed and "WebFetch" in b_disallowed

    # add_dirs emit one --add-dir each; extra_system appends to the persona.
    e = build_claude_flags(
        cfg,
        resume_id=SID_A,
        fork=False,
        add_dirs=("/tmp/up", "/tmp/out"),
        extra_system="OUTBOX HERE",
    )
    assert e.count("--add-dir") == 2
    assert "/tmp/up" in e and "/tmp/out" in e
    system_prompt = e[e.index("--append-system-prompt") + 1]
    assert "OUTBOX HERE" in system_prompt
    assert cfg.claude.persona.replace("{platform}", cfg.platform) in system_prompt


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

[attachments]
convert = "off"
""",
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.backend == "headless"
    assert cfg.discord.channel_ids == [111]
    assert cfg.limits.max_concurrent == 5
    assert cfg.attachments.convert == "off"
    cfg = load_config(path=cfg_file, backend="tmux", channel_ids=[222])
    assert cfg.backend == "tmux"
    assert cfg.discord.channel_ids == [222]


def test_load_config_resolves_relative_state_path(tmp_path: Path) -> None:
    # Relative [tunnel] paths must become absolute at load time, else inbound
    # attachments saved relative to the daemon CWD are unreadable when the fork
    # runs with cwd=project_dir (Codex P2).
    cfg_file = tmp_path / "rel.toml"
    cfg_file.write_text(
        '[tunnel]\nstate_path = "rel/s.json"\nregistry_path = "rel/r.json"\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.state_path == (tmp_path / "rel" / "s.json").resolve()


# --------------------------------------------------- attachment plumbing


def test_paths_layout(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.paths import (
        OUTBOX_DIRNAME,
        ensure_outbox,
        outbox_dir_for,
        safe_key,
        uploads_dir_for,
    )

    assert safe_key("th:123") == "th-123"
    assert safe_key("dm:99/x") == "dm-99-x"
    assert safe_key("") == "thread"

    state = tmp_path / "state"
    assert uploads_dir_for(state, "th:1") == state / "uploads" / "th-1"

    proj = tmp_path / "proj"
    proj.mkdir()
    assert outbox_dir_for(proj, "th:1") == proj / OUTBOX_DIRNAME / "th-1"

    made = ensure_outbox(proj, "th:1")
    assert made.is_dir()
    # A git-ignore-everything guard keeps the outbox out of `git status`.
    assert (proj / OUTBOX_DIRNAME / ".gitignore").read_text() == "*\n"


def test_paths_snapshot_diff(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.paths import (
        changed_files,
        snapshot_dir,
    )

    out = tmp_path / "out"
    out.mkdir()
    assert snapshot_dir(tmp_path / "missing") == {}

    a = out / "a.md"
    a.write_text("one", encoding="utf-8")
    old = time.time() - 100
    os.utime(a, (old, old))
    snap = snapshot_dir(out)
    assert set(snap) == {"a.md"}
    assert changed_files(out, snap) == []  # untouched

    # a new file in a subdir is detected.
    b = out / "sub" / "b.csv"
    b.parent.mkdir()
    b.write_text("x,y", encoding="utf-8")
    assert [p.name for p in changed_files(out, snap)] == ["b.csv"]

    # A rewrite with PRESERVED mtime (cp -p / coarse FS) is still caught now.
    a.write_text("changed", encoding="utf-8")  # different size, same mtime
    os.utime(a, (old, old))
    assert sorted(p.name for p in changed_files(out, snap)) == [
        "a.md",
        "b.csv",
    ]


def test_attachment_preamble() -> None:
    from claude_code_tools.agent_tunnel.paths import attachment_preamble

    assert attachment_preamble([], "hi") == "hi"

    one = attachment_preamble([Path("/up/r.pdf")], "summarize this")
    assert "/up/r.pdf" in one
    assert "Read tool" in one
    assert one.rstrip().endswith("summarize this")

    # no question text -> a default review instruction stands in.
    empty = attachment_preamble(
        [Path("/up/a.csv"), Path("/up/b.csv")], ""
    )
    assert "/up/a.csv" in empty and "/up/b.csv" in empty
    assert "review" in empty.lower()


def test_backend_attachment_dirs(tmp_path: Path) -> None:
    project, claude_home = _make_project(tmp_path)
    cfg = TunnelConfig(
        state_path=tmp_path / "state.json", claude_home=claude_home
    )
    store = TunnelStore(cfg.state_path)
    backend = HeadlessBackend(cfg, store)

    # read handle: no outbox, but the uploads dir is still created + exposed.
    rec_r = store.bind(
        "th:r", "h", SID_A, str(project), "headless", access="read"
    )
    add_dirs, extra, outbox, snap = backend._begin_turn(rec_r)
    assert outbox is None and extra == ""
    assert len(add_dirs) == 1 and Path(add_dirs[0]).is_dir()
    assert add_dirs[0].endswith("th-r")
    assert backend._end_turn(outbox, snap) == []

    # write handle: outbox created + named in the system prompt, and the
    # before/after diff surfaces exactly what the fork wrote this turn.
    rec_w = store.bind(
        "th:w", "h", SID_A, str(project), "headless", access="write"
    )
    add_dirs, extra, outbox, snap = backend._begin_turn(rec_w)
    assert outbox is not None and str(outbox) in extra
    assert (project / ".agent-tunnel-out" / ".gitignore").read_text() == "*\n"
    assert backend._end_turn(outbox, snap) == []  # nothing yet
    (outbox / "report.md").write_text("hello", encoding="utf-8")
    delivered = backend._end_turn(outbox, snap)
    assert [p.name for p in delivered] == ["report.md"]

    # forget cleans up both per-thread dirs.
    uploads_w = Path(add_dirs[0])
    backend.forget("th:w")
    assert not outbox.exists() and not uploads_w.exists()


def test_safe_filename() -> None:
    from claude_code_tools.agent_tunnel.discord_bot import _safe_filename

    assert _safe_filename("report.pdf") == "report.pdf"
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename("my file (1).csv") == "my_file_1_.csv"
    assert _safe_filename("") == "file"
    assert _safe_filename("   ") == "file"
    # Over-long names keep their extension (downstream picks type from suffix).
    out = _safe_filename("a" * 200 + ".docx")
    assert out.endswith(".docx") and len(out) <= 120


def test_unique_name() -> None:
    from claude_code_tools.agent_tunnel.discord_bot import _unique_name

    used: set[str] = set()
    # repeats get a numeric suffix before the extension, not overwritten.
    assert _unique_name("report.pdf", used) == "report.pdf"
    assert _unique_name("report.pdf", used) == "report-2.pdf"
    assert _unique_name("report.pdf", used) == "report-3.pdf"
    assert _unique_name("data.csv", used) == "data.csv"  # distinct name is free
    # extension-less and multi-dot names suffix correctly.
    assert _unique_name("notes", used) == "notes"
    assert _unique_name("notes", used) == "notes-2"
    assert _unique_name("a.tar.gz", used) == "a.tar.gz"
    assert _unique_name("a.tar.gz", used) == "a-2.tar.gz"


def test_leading_mention_id() -> None:
    from claude_code_tools.agent_tunnel.discord_bot import _leading_mention_id

    assert _leading_mention_id("what is X?") is None  # no mention -> answer
    assert _leading_mention_id("<@123> hey bot") == 123  # user mention
    assert _leading_mention_id("<@!123> nick form") == 123  # nickname mention
    assert _leading_mention_id("<@&456> role ping") == 456  # role mention
    assert _leading_mention_id("  <@789> leading ws") == 789
    assert _leading_mention_id("@everyone look") == -1  # broadcast
    assert _leading_mention_id("@here look") == -1
    assert _leading_mention_id("hey <@123> mid") is None  # not at the start


# ------------------------------------------------- office-file conversion


def test_convert_off_and_passthrough(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.convert import (
        CONVERTIBLE_EXTS,
        convert_attachment,
    )

    work = tmp_path / "up"
    work.mkdir()
    docx = work / "a.docx"
    docx.write_bytes(b"not really a docx")
    assert ".docx" in CONVERTIBLE_EXTS

    # "off" never converts, even for a convertible type.
    assert convert_attachment(docx, work, mode="off").path is None
    # A natively-readable type is a no-op (Read opens it directly).
    txt = work / "a.txt"
    txt.write_text("hi", encoding="utf-8")
    assert convert_attachment(txt, work, mode="auto").path is None


def test_convert_custom_command(tmp_path: Path) -> None:
    # A portable custom command (cp) exercises the run + output-discovery path
    # without depending on any Office converter being installed.
    from claude_code_tools.agent_tunnel.convert import convert_attachment

    work = tmp_path / "up"
    work.mkdir()
    src = work / "report.docx"
    src.write_bytes(b"dummy docx bytes")
    conv = convert_attachment(
        src, work, mode="auto", custom_command="cp {input} {outdir}/out.txt"
    )
    assert conv.converter == "custom"
    assert conv.path is not None and conv.path.name == "out.txt"
    assert conv.path.read_bytes() == b"dummy docx bytes"


def test_convert_same_stem_distinct_outputs(tmp_path: Path) -> None:
    # Two convertible files sharing a stem but differing in extension must not
    # share a conversion output path (else one overwrites the other).
    from claude_code_tools.agent_tunnel.convert import convert_attachment

    work = tmp_path / "up"
    work.mkdir()
    docx = work / "report.docx"
    docx.write_bytes(b"AAA")
    pptx = work / "report.pptx"
    pptx.write_bytes(b"BBB")

    cmd = "cp {input} {outdir}/out.txt"
    a = convert_attachment(docx, work, mode="auto", custom_command=cmd)
    b = convert_attachment(pptx, work, mode="auto", custom_command=cmd)
    assert a.path is not None and b.path is not None
    assert a.path != b.path
    assert a.path.read_bytes() == b"AAA"  # not clobbered by the pptx conversion
    assert b.path.read_bytes() == b"BBB"


def test_convert_auto_docx(tmp_path: Path) -> None:
    # Real auto path — guarded so it skips on hosts with no converter.
    import shutil

    import pytest

    from claude_code_tools.agent_tunnel.convert import (
        convert_attachment,
        converters_available,
    )

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc not installed; cannot build a .docx fixture")
    assert converters_available()

    md = tmp_path / "src.md"
    md.write_text(
        "# Title\n\nA word doc with token AUTO-CONV-42.\n", encoding="utf-8"
    )
    work = tmp_path / "up"
    work.mkdir()
    src = work / "doc.docx"
    subprocess.run([pandoc, str(md), "-o", str(src)], check=True)
    assert src.exists()

    conv = convert_attachment(src, work, mode="auto")
    assert conv.path is not None and conv.path.exists()
    assert conv.path.stat().st_size > 0
    assert conv.converter in ("libreoffice→pdf", "pandoc→md", "textutil→txt")


# ----------------------------------------------------------- forks table


def test_fork_row_fields() -> None:
    from claude_code_tools.agent_tunnel.cli import _fork_row
    from claude_code_tools.agent_tunnel.store import ThreadRecord

    rec = ThreadRecord(
        thread_key="th:42",
        handle="pay",
        access="bash",
        asker="bob",
        fork_session_id="abcdef123456",
        project_dir="/work/proj",
        config_dir="/home/u/.claude-rja",
        backend="tmux",
    )
    row = _fork_row(rec, status="idle")
    assert row["thread_key"] == "th:42"
    assert row["handle"] == "pay"
    assert row["access"] == "bash"
    assert row["asker"] == "bob"
    assert row["status"] == "idle"
    assert row["fork"] == "abcdef12"  # first 8 chars
    assert row["turns"] == "0"  # no transcript on disk
    assert row["project"] == "proj [.claude-rja]"


# --------------------------------------------------------------- rename


def test_registry_rename(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "registry.json")
    reg.upsert(
        PublishRecord(handle="old", session_id=SID_A, cwd="/p", access="write")
    )

    ok, _ = reg.rename("old", "new")
    assert ok
    assert reg.get("old") is None
    got = reg.get("new")
    assert got is not None
    assert got.handle == "new" and got.session_id == SID_A
    assert got.access == "write"  # access carried over

    assert reg.rename("missing", "x")[0] is False  # no such old handle
    assert reg.rename("new", "!!!")[0] is False  # malformed new handle

    # Collision with a different session's active handle is refused.
    reg.upsert(PublishRecord(handle="taken", session_id=SID_B, cwd="/q"))
    ok, msg = reg.rename("new", "taken")
    assert not ok and "already used" in msg
    assert reg.get("new") is not None  # original left intact
    # A revoked handle (after `>share off`) is treated as missing, not renamed.
    reg.revoke("new")
    assert reg.rename("new", "fresh")[0] is False


def test_registry_coerces_null_access(tmp_path: Path) -> None:
    # An old hook could write access=null; it must load as "read".
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "records": {
                    "h": {
                        "handle": "h",
                        "session_id": SID_A,
                        "cwd": "/p",
                        "access": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    rec = Registry(path).get("h")
    assert rec is not None and rec.access == "read"


def test_store_rename_handle(tmp_path: Path) -> None:
    store = TunnelStore(tmp_path / "state.json")
    store.bind("th:1", "old", SID_A, "/p", "tmux")
    store.bind("th:2", "old", SID_B, "/q", "tmux")
    store.bind("th:3", "other", SID_A, "/p", "tmux")

    renamed = store.rename_handle("old", "new")
    assert len(renamed) == 2

    reloaded = TunnelStore(tmp_path / "state.json")
    r1, r2, r3 = (reloaded.get(k) for k in ("th:1", "th:2", "th:3"))
    assert r1 is not None and r2 is not None and r3 is not None
    assert r1.handle == "new"
    assert r2.handle == "new"
    assert r3.handle == "other"  # untouched


def test_store_write_preserves_concurrent_changes(tmp_path: Path) -> None:
    # Two TunnelStore instances simulate the daemon and a CLI process. The
    # second writer must re-read under the lock so it doesn't clobber a change
    # the first one made after the second loaded its (now stale) snapshot.
    path = tmp_path / "state.json"
    daemon = TunnelStore(path)
    daemon.bind("th:1", "h", SID_A, "/p", "tmux")

    cli = TunnelStore(path)  # loads now: knows only th:1
    daemon.bind("th:2", "h", SID_B, "/q", "tmux")  # daemon adds th:2

    cli.remove("th:1")  # stale snapshot would drop th:2; reload-merge keeps it

    final = TunnelStore(path)
    assert final.get("th:1") is None  # cli's removal applied
    assert final.get("th:2") is not None  # daemon's concurrent add survived


def test_store_get_reflects_external_rename(tmp_path: Path) -> None:
    # The daemon's long-lived store must not serve a stale record after a CLI
    # process renames it on disk — else the daemon's next upsert undoes it.
    path = tmp_path / "state.json"
    daemon = TunnelStore(path)
    daemon.bind("th:1", "old", SID_A, "/p", "tmux")
    daemon.get("th:1")  # cached in the daemon's memory

    TunnelStore(path).rename_handle("old", "new")  # a separate CLI process

    rec = daemon.get("th:1")
    assert rec is not None and rec.handle == "new"  # get re-read disk
    rec.fork_session_id = SID_FORK
    daemon.upsert(rec)  # must preserve the rename, not clobber it

    final = TunnelStore(path).get("th:1")
    assert final is not None
    assert final.handle == "new" and final.fork_session_id == SID_FORK


def test_store_upsert_merges_into_external_rename(tmp_path: Path) -> None:
    # The real race: a backend holds a record fetched BEFORE a CLI `rename`,
    # runs a long turn, then upserts. The stale in-memory handle must NOT
    # overwrite the rename that landed on disk meanwhile.
    path = tmp_path / "state.json"
    daemon = TunnelStore(path)
    rec = daemon.bind("th:1", "old", SID_A, "/p", "tmux")  # handle="old"

    TunnelStore(path).rename_handle("old", "new")  # CLI renames mid-turn

    rec.fork_session_id = SID_FORK  # turn finished; record its fork id
    daemon.upsert(rec)  # stale rec.handle == "old" must not clobber

    final = TunnelStore(path).get("th:1")
    assert final is not None
    assert final.handle == "new"  # rename preserved, not undone
    assert final.fork_session_id == SID_FORK  # caller's field merged in
    assert SID_FORK in TunnelStore(path).known_fork_ids()


def test_store_upsert_does_not_resurrect_forgotten(tmp_path: Path) -> None:
    # A thread `forget`-ten while its turn runs must not be brought back by the
    # trailing upsert — but its fork id stays excluded from future reuse.
    path = tmp_path / "state.json"
    daemon = TunnelStore(path)
    rec = daemon.bind("th:1", "h", SID_A, "/p", "tmux")

    TunnelStore(path).remove("th:1")  # a CLI `forget` mid-turn

    rec.fork_session_id = SID_FORK
    daemon.upsert(rec)  # must not resurrect th:1

    other = TunnelStore(path)
    assert other.get("th:1") is None  # stays gone
    assert SID_FORK in other.known_fork_ids()  # fork id still excluded
