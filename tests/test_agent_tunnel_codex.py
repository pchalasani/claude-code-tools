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

from click.testing import CliRunner

from claude_code_tools.agent_tunnel.backends import (
    Backend,
    BackendError,
    HeadlessBackend,
    TmuxBackend,
    backend_for_record,
    backend_name_for,
    build_claude_flags,
)
from claude_code_tools.agent_tunnel.cli import _resume_argv
from claude_code_tools.agent_tunnel.cli import cli as tunnel_cli
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
        # Pre-envelope format: top-level id + cwd (no session_meta wrapper).
        meta = {"id": session_id, "timestamp": "t", "cwd": cwd,
                "instructions": None}
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
    src_bytes = src.read_bytes()
    new_id, dest = fork_codex_session(src)
    meta = json.loads(dest.read_text().splitlines()[0])
    assert meta["id"] == new_id and meta["forked_from_id"] == sid
    # BOTH provenance fields on the legacy format too (contract).
    assert meta["parent_thread_id"] == sid
    assert src.read_bytes() == src_bytes  # legacy source untouched


def test_legacy_rollout_cwd_and_discovery(tmp_path: Path) -> None:
    # Legacy (top-level id + cwd) sessions must be discoverable by cwd and
    # forkable byte-preservingly, alongside modern rollouts.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    legacy_id = uuid7()
    legacy = _write_rollout(home, legacy_id, str(proj), day="2026/07/15",
                            legacy=True)
    # rollout_cwd reads the legacy top-level cwd.
    assert rollout_cwd(legacy) == str(proj)
    # A newer session in a DIFFERENT project must not win; an excluded id is
    # skipped; the legacy match is selected.
    _write_rollout(home, uuid7(), "/elsewhere", day="2026/07/16")
    excluded = _write_rollout(home, uuid7(), str(proj), day="2026/07/16")
    import os as _os
    _os.utime(legacy, (time.time(), time.time()))  # newest matching
    found = find_latest_codex_session(
        proj, exclude={rollout_session_id(excluded)}, codex_home=home
    )
    assert found == legacy
    # Fork preserves the legacy source byte-for-byte.
    before = legacy.read_bytes()
    new_id, dest = fork_codex_session(legacy)
    assert legacy.read_bytes() == before and dest != legacy


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
    # Network is pinned OFF explicitly (not merely absent) so a write handle
    # can't inherit network_access=true from the owner's config.toml.
    assert "sandbox_workspace_write.network_access=false" in write

    bash = build_codex_flags(cfg, "bash")
    assert 'sandbox_mode="workspace-write"' in bash
    assert "sandbox_workspace_write.network_access=true" in bash

    # read-only has no workspace-write network setting at all.
    assert "network_access" not in " ".join(read)

    for flags in (read, write, bash):
        # Non-interactive turns can never answer approval prompts, at ANY
        # sandboxed level.
        assert 'approval_policy="never"' in flags
        # `codex exec resume` has NO --sandbox/-s flag: sandboxing must go
        # through -c overrides only, or every turn dies at argument parsing.
        assert "--sandbox" not in flags and "-s" not in flags
        # Non-shell tool surfaces (MCP, web search, and codex's default-on
        # action features) escape the OS sandbox, so every non-"all" handle
        # disables them.
        assert "mcp_servers={}" in flags
        assert "tools.web_search=false" in flags
        # Each action feature is disabled via a repeated `--disable <feature>`.
        assert "--disable" in flags
        for feature in ("apps", "browser_use", "computer_use", "hooks"):
            assert feature in flags

    # "all" emits the bypass flag ONLY when the [codex] gate is on; ungated
    # it falls back to the restrictive read-only sandbox.
    ungated = build_codex_flags(cfg, "all")
    assert "--dangerously-bypass-approvals-and-sandbox" not in ungated
    assert 'sandbox_mode="read-only"' in ungated
    cfg.codex.allow_skip_permissions = True
    gated = build_codex_flags(cfg, "all")
    assert "--dangerously-bypass-approvals-and-sandbox" in gated
    assert "sandbox_mode" not in " ".join(gated)
    # Gated "all" keeps MCP/web/features available (full access); non-"all"
    # disabled them above.
    assert "mcp_servers={}" not in gated
    assert "tools.web_search=false" not in gated
    assert "--disable" not in gated

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
    # The gates are per-agent BOTH ways: enabling only the CLAUDE gate must
    # still refuse a codex "all" binding (an implementation honoring either
    # gate would slip through here).
    cfg.claude.allow_skip_permissions = True
    with pytest.raises(BackendError, match=r"\[codex\]"):
        backend._require_binding("t")
    cfg.codex.allow_skip_permissions = True
    assert backend._require_binding("t").access == "all"


def test_claude_all_gate_not_opened_by_codex_gate(tmp_path: Path) -> None:
    # The other direction of gate independence: enabling only the CODEX
    # gate must not unlock a CLAUDE "all" handle.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    cfg.codex.allow_skip_permissions = True
    store = TunnelStore(cfg.state_path)
    store.bind("t", "h", "expsid", "/p", "headless", access="all")
    with pytest.raises(BackendError, match=r"\[claude\]"):
        HeadlessBackend(cfg, store)._require_binding("t")
    flags = build_claude_flags(cfg, "sid", fork=True, access="all")
    assert "--dangerously-skip-permissions" not in flags  # stays restrictive


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

    # Re-publish (same agent) with --write upgrades; without a flag it
    # preserves. The agent MUST match — identity is (agent, session_id).
    reg.publish(session_id="sid-1", cwd="/p", agent="codex", access="write")
    assert reg.get("paycodex").access == "write"  # type: ignore[union-attr]
    reg.publish(session_id="sid-1", cwd="/p", agent="codex", access=None)
    assert reg.get("paycodex").access == "write"  # type: ignore[union-attr]

    # A live handle owned by another session is a collision.
    handle2, collision2 = reg.publish(
        session_id="sid-2", cwd="/q", label="paycodex"
    )
    assert handle2 is None and collision2 == "paycodex"

    # Records written before the agent field read back as claude.
    reg.upsert(PublishRecord(handle="old", session_id="s", cwd="/p", agent=""))
    assert reg.get("old").agent == "claude"  # type: ignore[union-attr]


def test_share_cli_codex_publishes_correct_record(tmp_path: Path) -> None:
    # End-to-end boundary test for the PRIMARY codex publish path: the
    # `agent-tunnel share --agent codex` command must select the newest
    # matching session under CODEX_HOME and persist the right record.
    from click.testing import CliRunner

    from claude_code_tools.agent_tunnel.cli import cli as tunnel_cli

    home = tmp_path / "mycodex"
    proj = tmp_path / "proj"
    proj.mkdir()
    reg = tmp_path / "reg.json"
    state = tmp_path / "state.json"
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[tunnel]\nstate_path = "{state}"\nregistry_path = "{reg}"\n',
        encoding="utf-8",
    )
    _write_rollout(home, uuid7(), "/elsewhere", day="2026/07/16")  # other cwd
    older = _write_rollout(home, uuid7(), str(proj), day="2026/07/14")
    newest_id = uuid7()
    newest = _write_rollout(home, newest_id, str(proj), day="2026/07/15")
    import os as _os
    _os.utime(older, (time.time() - 100, time.time() - 100))
    _os.utime(newest, (time.time(), time.time()))

    env = {**os.environ, "CODEX_HOME": str(home)}
    result = CliRunner().invoke(
        tunnel_cli,
        ["share", "--config", str(cfg_file), "--agent", "codex",
         "--project", str(proj), "paygpt"],
        env=env,
    )
    assert result.exit_code == 0, result.output
    rec = Registry(reg).get("paygpt")
    assert rec is not None
    assert rec.agent == "codex"
    assert rec.session_id == newest_id  # newest MATCHING session selected
    assert Path(rec.cwd) == proj
    assert rec.config_dir == str(home)  # CODEX_HOME honored
    assert rec.transcript_path == str(newest)

    # --session pins an explicit (older) session instead.
    result2 = CliRunner().invoke(
        tunnel_cli,
        ["share", "--config", str(cfg_file), "--agent", "codex",
         "--session", rollout_session_id(older), "--project", str(proj),
         "payold"],
        env=env,
    )
    assert result2.exit_code == 0, result2.output
    rec2 = Registry(reg).get("payold")
    assert rec2 is not None and rec2.session_id == rollout_session_id(older)


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
        # Record this turn's argv (space-joined) so the test can assert codex
        # was actually invoked with `exec resume <fork_id>` on every turn.
        f'echo "$*" >> "{tmp_path}/argv.log"\n'
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

    # BOTH turns actually invoked codex as `exec resume <fork_id> -` — a
    # regression resuming the expert id (or a new id) would be caught here.
    turns = (tmp_path / "argv.log").read_text().splitlines()
    assert len(turns) == 2
    for line in turns:
        assert line.split()[:4] == ["exec", "resume", fork_id, "-"]


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


def test_fork_persisted_and_reused_after_failed_first_turn(
    tmp_path: Path,
) -> None:
    # A timed-out/failed first turn must NOT orphan the fork: the fork id is
    # persisted before codex runs, so the retry resumes the SAME fork (no
    # duplicate copy), auto-discovery excludes it via known_fork_ids(), and
    # the persona intro is re-sent because it never reached the fork.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _write_rollout(home, expert_id, str(proj))

    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        f'cat > "{tmp_path}/prompt.txt"\n'
        f'if [ ! -f "{tmp_path}/healthy" ]; then\n'
        f'  touch "{tmp_path}/healthy"\n'
        '  echo "simulated crash" >&2\n'
        "  exit 1\n"
        "fi\n"
        'sid="$3"\n'
        'printf \'{"type":"thread.started","thread_id":"%s"}\\n\' "$sid"\n'
        "printf '%s\\n' "
        "'{\"type\":\"item.completed\",\"item\":"
        "{\"type\":\"agent_message\",\"text\":\"recovered\"}}'\n",
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
    backend = CodexHeadlessBackend(cfg, store)

    with pytest.raises(BackendError, match="simulated crash"):
        backend.ask("t", "first question")
    rec = store.get("t")
    assert rec is not None and rec.fork_session_id  # persisted on failure
    fork_id = rec.fork_session_id
    assert rec.pending_instructions == "intro"  # persona still owed
    assert fork_id in store.known_fork_ids()  # excluded from auto-discovery
    rollouts = list((home / "sessions").glob("*/*/*/rollout-*.jsonl"))
    assert len(rollouts) == 2  # expert + exactly one fork

    # Simulate a daemon restart: brand-new store AND backend for the retry
    # (this is also how the real daemon behaves turn-to-turn).
    store2 = TunnelStore(cfg.state_path)
    backend2 = CodexHeadlessBackend(cfg, store2)
    answer = backend2.ask("t", "first question again")
    assert answer.text == "recovered"
    assert answer.fork_session_id == fork_id  # same fork, no re-copy
    rollouts = list((home / "sessions").glob("*/*/*/rollout-*.jsonl"))
    assert len(rollouts) == 2
    retry_prompt = (tmp_path / "prompt.txt").read_text()
    assert "bot mode" in retry_prompt  # intro survived failure AND restart

    backend2.ask("t", "follow-up")
    assert "bot mode" not in (tmp_path / "prompt.txt").read_text()
    cleared = TunnelStore(cfg.state_path).get("t")
    assert cleared is not None and cleared.pending_instructions == ""


def test_env_pins_codex_home_and_strips_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    backend = CodexHeadlessBackend(cfg, TunnelStore(cfg.state_path))
    rec = ThreadRecord(thread_key="t", agent="codex", config_dir="/x/.codex")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    env = backend._env(rec)
    assert env["CODEX_HOME"] == "/x/.codex"
    assert "OPENAI_API_KEY" not in env  # subscription login wins
    cfg.codex.unset_api_key = False
    assert backend._env(rec)["OPENAI_API_KEY"] == "sk-test"


def test_pending_instructions_durable_across_backend_instances(
    tmp_path: Path,
) -> None:
    # The daemon builds a FRESH backend per turn (backend_for_record without
    # a shared cache) and may restart between a failed turn and its retry,
    # so the persona/outbox markers must live on the ThreadRecord — never on
    # backend-instance memory.
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    store = TunnelStore(cfg.state_path)
    store.bind("t", "h", "sid", "/p", "headless", agent="codex")
    backend = CodexHeadlessBackend(cfg, store)

    backend._on_access_changed(store.get("t"), "read", "write")
    # Visible through a brand-new store (simulated restart)...
    persisted = TunnelStore(cfg.state_path).get("t")
    assert persisted is not None
    assert persisted.pending_instructions == "outbox"
    # ...and honored by a brand-new backend instance.
    fresh = CodexHeadlessBackend(cfg, TunnelStore(cfg.state_path))
    note = fresh._turn_preamble(persisted, "OUTBOX-NOTE")
    assert "OUTBOX-NOTE" in note and "bot mode" not in note
    # Building the preamble never clears the marker (failed turns keep it).
    still = TunnelStore(cfg.state_path).get("t")
    assert still is not None and still.pending_instructions == "outbox"

    # "intro" implies the outbox note and is never downgraded by a later
    # access change.
    rec = store.get("t")
    assert rec is not None
    rec.pending_instructions = "intro"
    store.upsert(rec)
    backend._on_access_changed(store.get("t"), "read", "bash")
    kept = store.get("t")
    assert kept is not None and kept.pending_instructions == "intro"
    intro = backend._turn_preamble(kept, "NOTE2")
    assert "bot mode" in intro and "NOTE2" in intro


def test_tmux_reaper_skips_codex_records(tmp_path: Path) -> None:
    # Even a corrupted/hand-edited codex record carrying a tmux window must
    # never be touched by the tmux reaper (guard fires before any tmux op).
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "records": {
                    "t": {
                        "thread_key": "t",
                        "agent": "codex",
                        "backend": "tmux",
                        "tmux_window": "zombie-1",
                        "last_used": 0,
                    }
                },
                "fork_ids": [],
            }
        ),
        encoding="utf-8",
    )
    cfg = TunnelConfig(state_path=path, backend="tmux")
    cfg.limits.pane_idle_ttl_min = 0.001  # everything is "idle"
    store = TunnelStore(path)
    assert TmuxBackend(cfg, store).reap_idle() == 0
    rec = store.get("t")
    assert rec is not None and rec.tmux_window == "zombie-1"  # untouched


def test_publish_derived_handle_reclaims_revoked(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "reg.json")
    # Another session's REVOKED record squats on the handle that would be
    # derived for the new session id ("zzzzzz" from "zzzzzz-1111").
    reg.upsert(
        PublishRecord(
            handle="zzzzzz",
            session_id="other-session",
            cwd="/other",
            access="bash",
            revoked=True,
        )
    )
    handle, collision = reg.publish(session_id="zzzzzz-1111", cwd="/p")
    assert (handle, collision) == ("zzzzzz", None)  # reclaimed, no x-suffix
    rec = reg.get("zzzzzz")
    assert rec is not None and rec.session_id == "zzzzzz-1111"
    assert rec.access == "read"  # nothing inherited from the old owner


def test_resume_argv_by_agent(tmp_path: Path) -> None:
    cfg = TunnelConfig(state_path=tmp_path / "s.json")
    cfg.codex.binary = "codex-x"
    cfg.claude.binary = "claude-x"
    codex_rec = ThreadRecord(
        thread_key="t",
        agent="codex",
        fork_session_id="fork-c",
        config_dir="/x/.codex",
    )
    binary, argv, env = _resume_argv(cfg, codex_rec)
    assert binary == "codex-x"
    assert argv == ["codex-x", "resume", "fork-c"]  # positional, no --resume
    assert env["CODEX_HOME"] == "/x/.codex"

    claude_rec = ThreadRecord(
        thread_key="t",
        fork_session_id="fork-a",
        config_dir="/x/.claude-work",
    )
    binary, argv, env = _resume_argv(cfg, claude_rec)
    assert argv == ["claude-x", "--resume", "fork-a"]
    assert env["CLAUDE_CONFIG_DIR"] == "/x/.claude-work"


def test_published_command_tags_codex_handles(tmp_path: Path) -> None:
    reg_path = tmp_path / "reg.json"
    state_path = tmp_path / "state.json"
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[tunnel]\nstate_path = "{state_path}"\n'
        f'registry_path = "{reg_path}"\n',
        encoding="utf-8",
    )
    reg = Registry(reg_path)
    reg.publish(session_id="sid-cx", cwd="/p", agent="codex", label="gptone")
    reg.publish(session_id="sid-cl", cwd="/p", agent="claude", label="clau")
    result = CliRunner().invoke(
        tunnel_cli, ["published", "--config", str(cfg_file)]
    )
    assert result.exit_code == 0, result.output
    lines = {ln.split(":")[0]: ln for ln in result.output.splitlines()}
    assert "[codex]" in lines["gptone"]
    assert "[codex]" not in lines["clau"]


def test_share_hook_codex_full_access_names_codex_gate(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    codex_transcript = (
        f"{tmp_path}/.codex/sessions/2026/07/16/"
        "rollout-2026-07-16T10-00-00-abc.jsonl"
    )
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": "codex-sess-2",
            "prompt": ">share --dangerously-skip-permissions codexall",
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
    reason = json.loads(result.stdout)["reason"]
    # The warning must point at the gate that actually unlocks codex forks.
    assert "[codex] allow_skip_permissions" in reason
    assert "[claude] allow_skip_permissions" not in reason
