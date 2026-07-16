"""Codex-backend tests for agent_tunnel.

Codex support forks sessions at the FILE level (codex `exec resume` appends
to the resumed rollout; its native `fork` is interactive-only), so these
tests exercise the fork/rewrite machinery on real files, the access→sandbox
flag mapping, agent-aware backend dispatch, and the `agent` field flowing
through registry/store/hook. Real files, no mocks; the one subprocess test
drives the backend through a stub `codex` executable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid as uuid_mod
from pathlib import Path

import pytest

from claude_code_tools.agent_tunnel.backends import (
    Backend,
    BackendError,
    HeadlessBackend,
    backend_for_record,
    backend_name_for,
)
from claude_code_tools.agent_tunnel.codex_backend import (
    CodexHeadlessBackend,
    build_codex_flags,
)
from claude_code_tools.agent_tunnel.codex_session import (
    count_codex_turns,
    find_codex_session_file,
    find_latest_codex_session,
    fork_codex_session,
    rollout_cwd,
    rollout_session_id,
    uuid7,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.registry import PublishRecord, Registry
from claude_code_tools.agent_tunnel.store import ThreadRecord, TunnelStore

HOOK = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "agent-tunnel"
    / "hooks"
    / "share_hook.py"
)


def _write_rollout(
    codex_home: Path,
    session_id: str,
    cwd: str,
    day: str = "2026/07/16",
    legacy: bool = False,
    user_texts: tuple[str, ...] = ("first question",),
) -> Path:
    """Create a minimal (but valid) codex rollout file."""
    day_dir = codex_home / "sessions" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-07-16T10-00-00-{session_id}.jsonl"
    if legacy:
        meta = {"id": session_id, "timestamp": "t", "instructions": None}
    else:
        meta = {
            "timestamp": "t",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd, "originator": "codex"},
        }
    lines = [json.dumps(meta)]
    # System-injected user blocks that must not count as turns.
    for synthetic in ("<user_instructions>...</user_instructions>",
                      "<environment_context>...</environment_context>"):
        lines.append(
            json.dumps(
                {
                    "timestamp": "t",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": synthetic}],
                    },
                }
            )
        )
    for text in user_texts:
        lines.append(
            json.dumps(
                {
                    "timestamp": "t",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_uuid7_is_valid_uuid_version_7() -> None:
    val = uuid7()
    parsed = uuid_mod.UUID(val)
    assert parsed.version == 7
    assert uuid7() != val  # random tail


def test_fork_codex_session_modern_format(tmp_path: Path) -> None:
    # The fork must land in the SAME codex home under a fresh id, stamp the
    # provenance fields codex's own fork writes, and never touch the source.
    home = tmp_path / "codexhome"
    sid = uuid7()
    src = _write_rollout(home, sid, "/proj")
    before = src.read_bytes()

    new_id, dest = fork_codex_session(src)

    assert new_id != sid and uuid_mod.UUID(new_id)
    assert dest.exists() and dest != src
    assert str(dest).startswith(str(home / "sessions"))
    assert dest.name == f"rollout-{dest.name.split('rollout-')[1]}"
    assert rollout_session_id(dest) == new_id
    meta = json.loads(dest.read_text().splitlines()[0])
    assert meta["payload"]["id"] == new_id
    assert meta["payload"]["forked_from_id"] == sid
    assert meta["payload"]["parent_thread_id"] == sid
    assert src.read_bytes() == before  # source untouched
    # The rest of the history is carried over verbatim.
    assert dest.read_text().splitlines()[1:] == (
        src.read_text().splitlines()[1:]
    )


def test_fork_codex_session_legacy_format(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    sid = uuid7()
    src = _write_rollout(home, sid, "/proj", legacy=True)
    new_id, dest = fork_codex_session(src)
    meta = json.loads(dest.read_text().splitlines()[0])
    assert meta["id"] == new_id and meta["forked_from_id"] == sid


def test_fork_codex_session_rejects_bad_input(tmp_path: Path) -> None:
    bad = tmp_path / "sessions" / "2026" / "07" / "16" / "rollout-x.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty rollout"):
        fork_codex_session(bad)
    outside = tmp_path / "not-a-rollout.jsonl"
    outside.write_text(json.dumps({"type": "session_meta", "payload": {}}))
    with pytest.raises(ValueError, match="sessions tree"):
        fork_codex_session(outside)


def test_find_latest_codex_session_matches_cwd(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    other = _write_rollout(home, uuid7(), "/elsewhere", day="2026/07/16")
    older = _write_rollout(home, uuid7(), str(proj), day="2026/07/14")
    newest = _write_rollout(home, uuid7(), str(proj), day="2026/07/15")
    excluded = _write_rollout(home, uuid7(), str(proj), day="2026/07/16")
    # Newest matching day-dir wins; excluded (fork) ids are skipped; a
    # different cwd never matches.
    found = find_latest_codex_session(
        proj, exclude={rollout_session_id(excluded)}, codex_home=home
    )
    assert found == newest
    assert found != other and found != older


def test_find_codex_session_file_and_cwd(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    sid = uuid7()
    path = _write_rollout(home, sid, "/proj")
    assert find_codex_session_file(sid, home) == path
    assert find_codex_session_file(uuid7(), home) is None
    assert rollout_cwd(path) == "/proj"


def test_count_codex_turns_skips_synthetic_user_blocks(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    path = _write_rollout(
        home, uuid7(), "/proj", user_texts=("q1", "q2", "q3")
    )
    assert count_codex_turns(path) == 3  # instructions/env context skipped


def test_build_codex_flags_access_mapping(tmp_path: Path) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json")

    read = build_codex_flags(cfg, "read")
    assert 'sandbox_mode="read-only"' in read
    assert 'approval_policy="never"' in read
    assert "--json" in read and "--skip-git-repo-check" in read

    write = build_codex_flags(cfg, "write")
    assert 'sandbox_mode="workspace-write"' in write
    assert "sandbox_workspace_write.network_access=true" not in write

    bash = build_codex_flags(cfg, "bash")
    assert 'sandbox_mode="workspace-write"' in bash
    assert "sandbox_workspace_write.network_access=true" in bash

    # "all" emits the bypass flag ONLY when the [codex] gate is on; ungated
    # it falls back to the restrictive read-only sandbox.
    ungated = build_codex_flags(cfg, "all")
    assert "--dangerously-bypass-approvals-and-sandbox" not in ungated
    assert 'sandbox_mode="read-only"' in ungated
    cfg.codex.allow_skip_permissions = True
    gated = build_codex_flags(cfg, "all")
    assert "--dangerously-bypass-approvals-and-sandbox" in gated
    assert "sandbox_mode" not in " ".join(gated)

    cfg.codex.model = "gpt-5.2"
    assert "gpt-5.2" in build_codex_flags(cfg, "read")


def test_backend_dispatch_by_record_agent(tmp_path: Path) -> None:
    # A codex record gets the codex backend even under a tmux-configured
    # daemon, and never shares a cache slot with the claude headless backend.
    cfg = TunnelConfig(state_path=tmp_path / "s.json", backend="tmux")
    store = TunnelStore(cfg.state_path)
    cache: dict[str, Backend] = {}

    codex_rec = ThreadRecord(thread_key="c", agent="codex", backend="headless")
    claude_rec = ThreadRecord(thread_key="h", backend="headless")
    assert isinstance(
        backend_for_record(cfg, store, codex_rec, cache), CodexHeadlessBackend
    )
    assert isinstance(
        backend_for_record(cfg, store, claude_rec, cache), HeadlessBackend
    )
    assert set(cache) == {"codex:headless", "claude:headless"}
    # Legacy records (no agent key on disk) load as claude.
    assert claude_rec.agent == "claude"


def test_backend_name_for_codex_is_headless(tmp_path: Path) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json", backend="tmux")
    assert backend_name_for(cfg, "codex") == "headless"
    assert backend_name_for(cfg, "claude") == "tmux"


def test_codex_all_access_gate_message_names_codex(tmp_path: Path) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "h", "expsid", "/p", "headless", access="all", agent="codex"
    )
    backend = CodexHeadlessBackend(cfg, store)
    with pytest.raises(BackendError, match=r"\[codex\]"):
        backend._require_binding("t")
    cfg.codex.allow_skip_permissions = True
    assert backend._require_binding("t").access == "all"
    # The claude gate stays independent: flipping codex must not open claude.
    assert cfg.claude.allow_skip_permissions is False


def test_store_bind_stamps_agent_and_legacy_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = TunnelStore(path)
    store.bind("t", "h", "sid", "/p", "headless", agent="codex")
    rec = store.get("t")
    assert rec is not None and rec.agent == "codex"
    # A legacy record written before the field loads as claude.
    data = json.loads(path.read_text())
    del data["records"]["t"]["agent"]
    path.write_text(json.dumps(data))
    legacy = TunnelStore(path).get("t")
    assert legacy is not None and legacy.agent == "claude"


def test_registry_publish_and_agent_backfill(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "reg.json")
    handle, collision = reg.publish(
        session_id="sid-1",
        cwd="/p",
        config_dir="/home/x/.codex",
        agent="codex",
        access=None,
        label="paycodex",
        transcript_path="/home/x/.codex/sessions/2026/07/16/r.jsonl",
    )
    assert (handle, collision) == ("paycodex", None)
    rec = reg.get("paycodex")
    assert rec is not None and rec.agent == "codex" and rec.access == "read"

    # Re-publish with --write upgrades; without a flag it preserves.
    reg.publish(session_id="sid-1", cwd="/p", access="write", label="")
    assert reg.get("paycodex").access == "write"  # type: ignore[union-attr]
    reg.publish(session_id="sid-1", cwd="/p", access=None, label="")
    assert reg.get("paycodex").access == "write"  # type: ignore[union-attr]

    # A live handle owned by another session is a collision.
    handle2, collision2 = reg.publish(
        session_id="sid-2", cwd="/q", label="paycodex"
    )
    assert handle2 is None and collision2 == "paycodex"

    # Records written before the agent field read back as claude.
    reg.upsert(PublishRecord(handle="old", session_id="s", cwd="/p", agent=""))
    assert reg.get("old").agent == "claude"  # type: ignore[union-attr]


def test_share_hook_detects_codex_transcript(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    codex_transcript = (
        f"{tmp_path}/.codex/sessions/2026/07/16/"
        "rollout-2026-07-16T10-00-00-abc.jsonl"
    )
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": "codex-sess-1",
            "prompt": ">share codexy",
            "cwd": str(tmp_path),
            "transcript_path": codex_transcript,
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
    rec = json.loads(registry.read_text())["records"]["codexy"]
    assert rec["agent"] == "codex"
    assert rec["config_dir"] == f"{tmp_path}/.codex"


def test_share_hook_claude_transcript_still_claude(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    claude_transcript = f"{tmp_path}/.claude/projects/-x/abc.jsonl"
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": "claude-sess-1",
            "prompt": ">share clau",
            "cwd": str(tmp_path),
            "transcript_path": claude_transcript,
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
    rec = json.loads(registry.read_text())["records"]["clau"]
    assert rec["agent"] == "claude"
    assert rec["config_dir"] == f"{tmp_path}/.claude"


def _stub_codex(tmp_path: Path) -> Path:
    """A stand-in `codex` executable emitting the real event stream shape.

    Echoes the resumed session id from argv and records the stdin prompt to
    a file so the test can assert on the persona preamble.
    """
    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        f'cat > "{tmp_path}/prompt.txt"\n'
        # argv: exec resume <id> - --json ...
        'sid="$3"\n'
        'printf \'{"type":"thread.started","thread_id":"%s"}\\n\' "$sid"\n'
        "printf '%s\\n' "
        "'{\"type\":\"item.completed\",\"item\":"
        "{\"type\":\"agent_message\",\"text\":\"stub answer\"}}'\n"
        "printf '%s\\n' '{\"type\":\"turn.completed\",\"usage\":{}}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_codex_backend_ask_forks_then_reuses(tmp_path: Path) -> None:
    # Full ask() flow against a stub codex: first turn forks the rollout at
    # the file level (fresh id, source untouched) and prepends the persona;
    # the follow-up resumes the SAME fork id with a bare prompt.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    expert = _write_rollout(home, expert_id, str(proj))
    expert_bytes = expert.read_bytes()

    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(_stub_codex(tmp_path))
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t",
        "h",
        expert_id,
        str(proj),
        "headless",
        config_dir=str(home),
        agent="codex",
    )
    backend = CodexHeadlessBackend(cfg, store)

    answer = backend.ask("t", "what changed?")
    assert answer.text == "stub answer" and answer.new_thread
    fork_id = answer.fork_session_id
    assert fork_id != expert_id
    assert find_codex_session_file(fork_id, home) is not None
    assert expert.read_bytes() == expert_bytes  # expert never touched
    first_prompt = (tmp_path / "prompt.txt").read_text()
    assert "bot mode" in first_prompt  # persona rides the fork turn
    assert first_prompt.strip().endswith("what changed?")

    again = backend.ask("t", "and now?")
    assert not again.new_thread
    assert again.fork_session_id == fork_id  # stable across turns
    follow_up = (tmp_path / "prompt.txt").read_text()
    assert "bot mode" not in follow_up  # persona only on the fork turn
    rec = store.get("t")
    assert rec is not None and rec.fork_session_id == fork_id


def test_codex_backend_reports_codex_errors(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _write_rollout(home, expert_id, str(proj))

    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        "printf '%s\\n' '{\"type\":\"turn.failed\","
        "\"error\":{\"message\":\"boom\"}}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(stub)
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t",
        "h",
        expert_id,
        str(proj),
        "headless",
        config_dir=str(home),
        agent="codex",
    )
    with pytest.raises(BackendError, match="boom"):
        CodexHeadlessBackend(cfg, store).ask("t", "q")
