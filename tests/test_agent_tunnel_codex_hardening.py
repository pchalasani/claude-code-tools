"""Hostile-input / robustness tests for the agent-tunnel codex support.

The locked review contract: ANY field of ANY parsed JSON may be absent, null,
or of the wrong type at runtime — loaders and parsers must skip or normalize,
never crash, and identity-critical operations (forking, discovery) must fail
with their documented exceptions. Split from test_agent_tunnel_codex.py to
keep both files under the repo's file-length guideline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from click.testing import CliRunner

from claude_code_tools.agent_tunnel import codex_session as codex_session_mod
from claude_code_tools.agent_tunnel.backends import (
    BackendError,
    HeadlessBackend,
    backend_for_record,
)
from claude_code_tools.agent_tunnel.cli import cli as tunnel_cli
from claude_code_tools.agent_tunnel.codex_backend import _parse_exec_events
from claude_code_tools.agent_tunnel.codex_session import (
    codex_home_for,
    count_codex_turns,
    find_codex_session_file,
    find_latest_codex_session,
    fork_codex_session,
    rollout_session_id,
    uuid7,
)
from claude_code_tools.agent_tunnel.config import TunnelConfig
from claude_code_tools.agent_tunnel.registry import Registry
from claude_code_tools.agent_tunnel.store import TunnelStore
from tests.test_agent_tunnel_codex import HOOK, _write_rollout


def test_fork_never_clobbers_existing_rollout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deterministic: pin BOTH the UUID sequence and the timestamp so the
    # collision (and thus the retry path) always executes.
    home = tmp_path / "codexhome"
    sid = uuid7()
    src = _write_rollout(home, sid, "/proj")
    colliding, fresh = uuid7(), uuid7()
    seq = iter([colliding, fresh])
    monkeypatch.setattr(codex_session_mod, "uuid7", lambda: next(seq))
    monkeypatch.setattr(
        codex_session_mod.time, "strftime",
        lambda fmt: "2026/07/16" if "/" in fmt else "2026-07-16T10-00-00",
    )
    day_dir = home / "sessions" / "2026/07/16"
    day_dir.mkdir(parents=True, exist_ok=True)
    pre = day_dir / f"rollout-2026-07-16T10-00-00-{colliding}.jsonl"
    pre.write_text("SENTINEL", encoding="utf-8")

    new_id, dest = fork_codex_session(src)

    assert pre.read_text() == "SENTINEL"  # never overwritten
    assert new_id == fresh and dest.exists()  # retry produced the 2nd id
    # Provenance must point at the EXPERT on the retry: a retry that re-read
    # the mutated first attempt would stamp the candidate id here.
    meta = json.loads(dest.read_text().splitlines()[0])
    assert meta["payload"]["forked_from_id"] == sid
    assert meta["payload"]["parent_thread_id"] == sid
    # No leftover temp file from the collided first attempt.
    assert not list(day_dir.glob(".rollout-*.tmp"))


def test_fork_rejects_null_or_list_meta(tmp_path: Path) -> None:
    for bad in ("null", "[1, 2]", '"just a string"'):
        f = tmp_path / f"rollout-x-{abs(hash(bad))}.jsonl"
        f.write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            fork_codex_session(f)


def test_codex_home_for_handles_sessions_segment_in_home(
    tmp_path: Path,
) -> None:
    # A legitimate CODEX_HOME that itself contains a "sessions" path segment
    # must not be truncated at the first occurrence.
    home = tmp_path / "sessions" / "custom-codex-home"
    sid = uuid7()
    path = _write_rollout(home, sid, "/proj")
    assert codex_home_for(path) == home
    # And the fork lands under the SAME home, resumable by id there.
    new_id, dest = fork_codex_session(path)
    assert find_codex_session_file(new_id, home) == dest


def test_parse_exec_events_hostile_shapes() -> None:
    stream = "\n".join(
        [
            "null",
            "[]",
            '"scalar"',
            '{"type":"item.completed","item":null}',
            '{"type":"item.completed","item":"scalar"}',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":null}}',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":42}}',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"the answer"}}',
        ]
    )
    answer, errors, failed = _parse_exec_events(stream)
    assert answer == "the answer"  # non-string texts ignored
    assert errors == [] and failed is False


def test_parse_exec_events_preserves_unicode_separators() -> None:
    # An agent_message whose text contains U+2028/U+2029/U+0085 (which codex
    # emits raw) is ONE JSONL event: the parser must split on "\n" only, or
    # str.splitlines() shatters it and the answer is lost as "Empty answer".
    text = "para1 line2 para2end"
    event = json.dumps(
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": text}},
        ensure_ascii=False,
    )
    answer, errors, failed = _parse_exec_events(event + "\n")
    assert answer == text  # intact, not fragmented
    assert failed is False and errors == []


def test_parse_exec_events_bad_final_message_clears_answer() -> None:
    # The LAST agent_message is authoritative: if its text is null/absent/
    # non-string/empty, the earlier valid message must NOT be returned — the
    # answer clears so ask() takes its empty-answer failure path.
    for bad_final in (
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":null}}',
        '{"type":"item.completed","item":{"type":"agent_message"}}',
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":42}}',
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":""}}',
    ):
        stream = "\n".join(
            [
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"earlier valid"}}',
                bad_final,
            ]
        )
        answer, errors, failed = _parse_exec_events(stream)
        assert answer == "", f"stale answer leaked for: {bad_final}"


def test_parse_exec_events_returns_last_agent_message() -> None:
    # The documented rule: the answer is the LAST agent_message in the stream
    # (multiple can appear across a turn); returning the first would be wrong.
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"SECOND and final"}}',
        ]
    )
    answer, errors, failed = _parse_exec_events(stream)
    assert answer == "SECOND and final" and not failed


def test_codex_backend_kills_process_group_on_timeout(tmp_path: Path) -> None:
    # A codex wrapper that spawns a long-lived child then hangs must, on
    # timeout, have its ENTIRE group killed — not just the direct child.
    import os as _os
    import signal as _signal

    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )

    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _write_rollout(home, expert_id, str(proj))
    childpid = tmp_path / "childpid"
    stub = tmp_path / "codex"
    # The child IGNORES SIGTERM (trap "" TERM + a restart loop) and keeps the
    # inherited stdout/stderr open, while the wrapper itself dies on SIGTERM —
    # exactly the case a wrapper-only kill would leave alive. Only the group
    # SIGKILL ends it.
    stub.write_text(
        "#!/bin/sh\n"
        "cat > /dev/null\n"
        "sh -c 'trap \"\" TERM; while true; do sleep 0.5; done' &\n"
        f'echo $! > "{childpid}"\n'
        "wait\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(stub)
    cfg.limits.answer_timeout_s = 1.0
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "h", expert_id, str(proj), "headless",
        config_dir=str(home), agent="codex",
    )
    with pytest.raises(BackendError, match="Timed out"):
        CodexHeadlessBackend(cfg, store).ask("t", "q")
    pid = int(childpid.read_text().strip())
    # The SIGTERM-ignoring child must still be dead (group SIGKILL landed).
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _os.kill(pid, 0)  # still alive?
            time.sleep(0.2)
        except OSError:
            break  # gone
    with pytest.raises(OSError):
        _os.kill(pid, 0)


def test_codex_live_access_outbox_note_sent_once(tmp_path: Path) -> None:
    # Full-flow contract: after a live access change, the outbox note rides
    # the NEXT successful turn's prompt exactly once, then is cleared.
    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )

    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _write_rollout(home, expert_id, str(proj))
    cfg = TunnelConfig(
        state_path=tmp_path / "state" / "s.json",
        registry_path=tmp_path / "reg.json",
    )
    cfg.codex.binary = str(_recording_stub(tmp_path, "ok"))
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "payd", expert_id, str(proj), "headless",
        config_dir=str(home), agent="codex", access="read",
    )
    prompt_file = tmp_path / "prompt.txt"
    backend = CodexHeadlessBackend(cfg, store)
    backend.ask("t", "q0")  # fork turn (persona intro consumed)

    # Upgrade to write via the registry, then sync it onto the live thread.
    Registry(cfg.registry_path).publish(
        session_id=expert_id, cwd=str(proj), agent="codex",
        access="write", label="payd",
    )
    backend._require_binding("t")  # fires _on_access_changed → marker set
    assert store.get("t").pending_instructions == "outbox"  # type: ignore

    # First turn after the change: the prompt CARRIES the outbox note, and
    # the marker clears on success.
    backend2 = CodexHeadlessBackend(cfg, store)
    backend2.ask("t", "q1")
    assert "outbox directory" in prompt_file.read_text()
    assert store.get("t").pending_instructions == ""  # type: ignore

    # The following turn's prompt does NOT (note sent exactly once).
    backend3 = CodexHeadlessBackend(cfg, store)
    answer3 = backend3.ask("t", "q2")
    assert answer3.text == "ok"
    assert "outbox directory" not in prompt_file.read_text()
    assert store.get("t").pending_instructions == ""  # type: ignore


def test_parse_exec_events_malformed_line_is_failure() -> None:
    # A valid agent_message followed by a truncated/corrupt event (with the
    # process still exiting 0) must be reported as failed, not returned as a
    # successful answer over the torn tail.
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"stale answer"}}',
            '{"type":"item.completed","item":{"type":"agent_mess',  # truncated
        ]
    )
    answer, errors, failed = _parse_exec_events(stream)
    assert failed is True
    assert any("unparseable" in e for e in errors)


def test_native_codex_fork_is_shareable(tmp_path: Path) -> None:
    # A user's OWN `codex fork` sets codex's generic forked_from_id but NOT
    # the tunnel marker. Auto-discovery must still pick it (it can be the
    # newest matching session) — only TUNNEL forks are skipped.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    day_dir = home / "sessions" / "2026/07/16"
    day_dir.mkdir(parents=True, exist_ok=True)
    native_id = uuid7()
    native = day_dir / f"rollout-2026-07-16T11-00-00-{native_id}.jsonl"
    meta = {
        "type": "session_meta",
        "payload": {
            "id": native_id,
            "cwd": str(proj),
            "forked_from_id": uuid7(),  # native codex fork provenance
            "parent_thread_id": uuid7(),
        },
    }
    msg = {
        "type": "response_item",
        "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}]},
    }
    native.write_text(
        json.dumps(meta) + "\n" + json.dumps(msg) + "\n", encoding="utf-8"
    )
    # Another plain session in the same project, with the native fork pinned
    # as the most-recently-modified (discovery orders by global mtime).
    import os as _os
    other = _write_rollout(home, uuid7(), str(proj), day="2026/07/14")
    _os.utime(other, (time.time() - 100, time.time() - 100))
    _os.utime(native, (time.time(), time.time()))
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == native  # native fork selectable, not skipped

    # But a TUNNEL fork of it IS skipped even when newest.
    fork_id, fork_file = fork_codex_session(native)
    import os as _os
    _os.utime(fork_file, (time.time() + 10, time.time() + 10))
    again = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert again == native and again != fork_file


def test_find_latest_survives_symlink_loop_cwd(tmp_path: Path) -> None:
    # A rollout whose recorded cwd resolves through a symlink LOOP must be
    # skipped (Path.resolve raises RuntimeError on 3.11/3.12), not crash
    # discovery; a valid session in the real project still wins.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    loop = tmp_path / "loop"
    loop.symlink_to(loop)  # self-referential symlink → resolve() loops
    day_dir = home / "sessions" / "2026/07/16"
    day_dir.mkdir(parents=True, exist_ok=True)
    bad_id = uuid7()
    bad = day_dir / f"rollout-2026-07-16T12-00-00-{bad_id}.jsonl"
    meta = {"type": "session_meta",
            "payload": {"id": bad_id, "cwd": str(loop)}}
    msg = {"type": "response_item",
           "payload": {"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "x"}]}}
    bad.write_text(
        json.dumps(meta) + "\n" + json.dumps(msg) + "\n", encoding="utf-8"
    )
    import os as _os
    _os.utime(bad, (time.time() + 10, time.time() + 10))  # newest
    good = _write_rollout(home, uuid7(), str(proj), day="2026/07/15")
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == good  # loop-cwd rollout skipped, not a crash


def test_relative_codex_home_resolved_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative CODEX_HOME must be anchored to an absolute path at publish/
    # discovery time, so the stored config_dir still resolves when the daemon
    # runs from a different cwd.
    proj = tmp_path / "proj"
    proj.mkdir()
    rel_home = "relhome"
    abs_home = tmp_path / rel_home
    sid = uuid7()
    _write_rollout(abs_home, sid, str(proj))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", rel_home)  # RELATIVE
    found = find_latest_codex_session(proj, exclude=set())
    assert found is not None and found.is_absolute()
    assert codex_home_for(found) == abs_home  # absolute, not "relhome"


def test_parse_exec_events_turn_failed_is_fatal_after_message() -> None:
    # A partial agent_message followed by a top-level turn.failed must NOT
    # be treated as a successful answer.
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"partial"}}',
            '{"type":"turn.failed","error":{"message":"rate limited"}}',
        ]
    )
    answer, errors, failed = _parse_exec_events(stream)
    assert answer == "partial" and failed is True
    assert errors == ["rate limited"]


def test_codex_backend_partial_then_turn_failed_raises(tmp_path: Path) -> None:
    # End-to-end: the backend must raise (not return the partial), and leave
    # the pending-intro marker set so the retry re-sends the persona.
    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )
    from tests.test_agent_tunnel_codex import _write_rollout as _wr

    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _wr(home, expert_id, str(proj))
    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":"
        "{\"type\":\"agent_message\",\"text\":\"half an answer\"}}'\n"
        "printf '%s\\n' '{\"type\":\"turn.failed\",\"error\":"
        "{\"message\":\"context overflow\"}}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(stub)
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "h", expert_id, str(proj), "headless",
        config_dir=str(home), agent="codex",
    )
    with pytest.raises(BackendError, match="context overflow"):
        CodexHeadlessBackend(cfg, store).ask("t", "q")
    rec = store.get("t")
    assert rec is not None and rec.pending_instructions == "intro"


def test_extra_args_cannot_weaken_sandbox(tmp_path: Path) -> None:
    from claude_code_tools.agent_tunnel.codex_backend import (
        _sanitize_extra_args,
    )

    hostile = [
        "--dangerously-bypass-approvals-and-sandbox",
        "--full-auto",
        "--yolo",
        "--ephemeral",  # would break the stable-fork contract
        "-s", "danger-full-access",
        "--sandbox=danger-full-access",
        "-sdanger-full-access",  # attached short form
        "-c", 'sandbox_mode="danger-full-access"',
        "-c", "approval_policy=never",
        "-c", "sandbox_workspace_write.network_access=true",
        # equals-form long options (both codex-valid) must strip too.
        '--config=sandbox_mode="danger-full-access"',
        "--config=sandbox_workspace_write.network_access=true",
        '-c=approval_policy="never"',
        # "--" would turn the enforced (last-appended) flags into positionals.
        "--",
        # -o/--output-last-message write an owner-chosen host file (outside
        # the sandbox) — a read handle must not carry it, in any form.
        "-o", "/tmp/pwn",
        "--output-last-message", "/tmp/pwn2",
        "-o/tmp/pwn3",
        "--output-last-message=/tmp/pwn4",
    ]
    assert _sanitize_extra_args(hostile) == []
    # Benign overrides survive untouched.
    benign = ["-c", 'model="o3"', "-m", "gpt-5.2", "--json",
              "--config=model_reasoning_effort=high"]
    assert _sanitize_extra_args(benign) == benign


def test_ask_argv_enforces_sandbox_after_extra_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Boundary test: drive ask() and inspect the REAL argv. Hostile extra
    # args must be absent, and the enforced per-handle sandbox/approval flags
    # must come AFTER every retained extra arg (codex -c is last-wins). Uses
    # a REAL recording stub (ask() now runs codex via Popen, not
    # subprocess.run) so the true argv is asserted at the process boundary.
    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )
    from tests.test_agent_tunnel_codex import _write_rollout as _wr

    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _wr(home, expert_id, str(proj))
    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(_recording_stub(tmp_path, "ok"))
    cfg.codex.headless_extra_args = [
        "--dangerously-bypass-approvals-and-sandbox",
        "--config=sandbox_workspace_write.network_access=true",
        "-c", 'model="o3"',  # benign — must survive
    ]
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "h", expert_id, str(proj), "headless",
        config_dir=str(home), agent="codex", access="write",
    )
    backend = CodexHeadlessBackend(cfg, store)
    answer = backend.ask("t", "q")
    argv = (tmp_path / "argv.log").read_text().splitlines()

    # Codex is invoked with `exec resume <fork_id> -` (stable-fork contract).
    assert argv[:3] == ["exec", "resume", answer.fork_session_id]
    assert argv[3] == "-"
    # Hostile args stripped; benign kept.
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "network_access=true" not in " ".join(argv)
    assert 'model="o3"' in argv
    # Enforced write sandbox appears, AFTER the retained benign override.
    assert 'sandbox_mode="workspace-write"' in argv
    assert argv.index('model="o3"') < argv.index(
        'sandbox_mode="workspace-write"'
    )
    # Non-shell tool surfaces are disabled for a write handle.
    assert "mcp_servers={}" in argv
    assert "tools.web_search=false" in argv


def _recording_stub(tmp_path: Path, answer: str) -> Path:
    """A stub `codex` that records its argv (one per line) to argv.log, the
    stdin prompt to prompt.txt, and emits a single agent_message."""
    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        f'cat > "{tmp_path}/prompt.txt"\n'
        f'for a in "$@"; do printf "%s\\n" "$a" >> "{tmp_path}/argv.log"; done\n'
        "printf '%s\\n' "
        "'{\"type\":\"item.completed\",\"item\":"
        f'{{"type":"agent_message","text":"{answer}"}}}}\'\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_codex_backend_whitespace_answer_is_not_success(
    tmp_path: Path,
) -> None:
    # A whitespace-only agent_message is not an answer: ask() must raise and
    # keep the pending-intro marker for the retry.
    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )
    from tests.test_agent_tunnel_codex import _write_rollout as _wr

    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert_id = uuid7()
    _wr(home, expert_id, str(proj))
    stub = tmp_path / "codex"
    stub.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":"
        "{\"type\":\"agent_message\",\"text\":\"   \\n  \"}}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    cfg = TunnelConfig(state_path=tmp_path / "state" / "s.json")
    cfg.codex.binary = str(stub)
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "h", expert_id, str(proj), "headless",
        config_dir=str(home), agent="codex",
    )
    with pytest.raises(BackendError, match="Empty answer"):
        CodexHeadlessBackend(cfg, store).ask("t", "q")
    rec = store.get("t")
    assert rec is not None and rec.pending_instructions == "intro"


def test_discovery_survives_dangling_symlink(tmp_path: Path) -> None:
    # A dangling rollout symlink (or a file removed between glob and stat)
    # must not crash discovery — it sorts oldest and is skipped.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = uuid7()
    good = _write_rollout(home, sid, str(proj))
    day_dir = good.parent
    dangling = day_dir / f"rollout-2026-07-16T09-00-00-{uuid7()}.jsonl"
    dangling.symlink_to(day_dir / "does-not-exist.jsonl")
    assert not dangling.exists()  # dangling
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == good
    # find_codex_session_file also tolerates a dangling same-id symlink.
    other = uuid7()
    (day_dir / f"rollout-2026-07-16T09-00-00-{other}.jsonl").symlink_to(
        day_dir / "nope.jsonl"
    )
    assert find_codex_session_file(other, home) is None


def test_fork_never_clobbers_via_atomic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The publish step must never overwrite a dest that appears in the
    # TOCTOU window: os.link fails rather than clobbering. Simulate the race
    # by creating dest during the temp write.
    home = tmp_path / "codexhome"
    sid = uuid7()
    src = _write_rollout(home, sid, "/proj")
    first, second = uuid7(), uuid7()
    ids = iter([first, second])
    monkeypatch.setattr(codex_session_mod, "uuid7", lambda: next(ids))
    monkeypatch.setattr(
        codex_session_mod.time, "strftime",
        lambda fmt: "2026/07/16" if "/" in fmt else "2026-07-16T10-00-00",
    )
    day_dir = home / "sessions" / "2026/07/16"
    day_dir.mkdir(parents=True, exist_ok=True)
    dest1 = day_dir / f"rollout-2026-07-16T10-00-00-{first}.jsonl"

    real_open = open

    def racing_open(path, *a, **k):
        # When the first attempt's temp file is opened, a concurrent writer
        # publishes dest1 — os.link must then refuse to overwrite it.
        if str(path).endswith(f".rollout-{first}.tmp") and not dest1.exists():
            dest1.write_text("CONCURRENT", encoding="utf-8")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", racing_open)
    new_id, dest = fork_codex_session(src)

    assert dest1.read_text() == "CONCURRENT"  # never clobbered
    assert new_id == second  # first attempt lost the race, retried
    assert dest.name.endswith(f"{second}.jsonl")


def test_find_codex_session_file_rejects_glob_metachars(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codexhome"
    _write_rollout(home, uuid7(), "/proj")
    # A wildcard/bracket "id" must never be interpolated into the glob.
    assert find_codex_session_file("*", home) is None
    assert find_codex_session_file("a[bc]", home) is None
    assert find_codex_session_file("", home) is None


def test_find_latest_orders_by_global_mtime(tmp_path: Path) -> None:
    # A resumed OLD-day session (touched now) must beat a later-day one:
    # codex resume appends in place, so mtime — not the date dir — is truth.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    later_day = _write_rollout(home, uuid7(), str(proj), day="2026/07/20")
    old_day = _write_rollout(home, uuid7(), str(proj), day="2026/01/01")
    # "Resume" the old-day session: make it the most recently modified.
    now = time.time()
    os.utime(later_day, (now - 100, now - 100))
    os.utime(old_day, (now, now))
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == old_day


def test_find_latest_skips_fork_shaped_rollouts(tmp_path: Path) -> None:
    # A tunnel fork shares the expert's cwd and can be the newest file, but
    # its meta carries forked_from_id — discovery must never pick it as an
    # expert (closing the copy-then-record race independent of `exclude`).
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    expert = _write_rollout(home, uuid7(), str(proj))
    fork_id, fork_file = fork_codex_session(expert)
    os.utime(fork_file, (time.time() + 10, time.time() + 10))  # newest
    # Even with an empty exclude set, the fork is skipped structurally.
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == expert


def test_agent_normalized_to_enum(tmp_path: Path) -> None:
    # A garbage agent value ("codex " with a stray space) must dispatch as
    # claude, never be shown as codex yet run through the claude backend.
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "records": {
                    "t": {
                        "thread_key": "t",
                        "agent": "codex ",
                        "backend": "headless",
                    }
                },
                "fork_ids": [],
            }
        ),
        encoding="utf-8",
    )
    assert TunnelStore(state).get("t").agent == "claude"  # type: ignore

    reg = tmp_path / "reg.json"
    reg.write_text(
        json.dumps(
            {
                "records": {
                    "h": {
                        "handle": "h",
                        "session_id": "sid",
                        "cwd": "/p",
                        "agent": "CODEX",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert Registry(reg).get("h").agent == "claude"  # type: ignore


def test_loaders_survive_non_utf8_bytes(tmp_path: Path) -> None:
    # A single invalid UTF-8 byte in a persisted file must not crash loading
    # (UnicodeDecodeError is a ValueError, now caught).
    state = tmp_path / "state.json"
    state.write_bytes(b'{"records": {}, "fork_ids": []}\xff')
    assert TunnelStore(state).all_records() == []
    reg = tmp_path / "reg.json"
    reg.write_bytes(b'{"records": {}}\xfe')
    assert Registry(reg).active() == []


def test_count_codex_turns_survives_non_utf8(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    path = _write_rollout(home, uuid7(), "/proj", user_texts=("q1",))
    with open(path, "ab") as f:
        f.write(b"\xff\xfe garbage line\n")
    assert count_codex_turns(path) == 1  # readable turns still counted


def test_count_codex_turns_hostile_shapes(tmp_path: Path) -> None:
    lines = [
        json.dumps({"type": "session_meta", "payload": {"id": "x"}}),
        "null",
        "[]",
        json.dumps({"type": "response_item", "payload": None}),
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user",
                            "content": None},
            }
        ),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": None}],
                },
            }
        ),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "real q"}],
                },
            }
        ),
    ]
    f = tmp_path / "r.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Null-text message counts as a (blank) turn; garbage lines never crash.
    assert count_codex_turns(f) == 2


def test_find_latest_survives_null_lines(tmp_path: Path) -> None:
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = uuid7()
    path = _write_rollout(home, sid, str(proj))
    # Inject a hostile-but-valid-JSON line; validity scanning must skip it.
    with open(path, "a", encoding="utf-8") as f:
        f.write("null\n")
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == path


def test_find_latest_has_no_day_dir_horizon(tmp_path: Path) -> None:
    # The documented contract is "the newest rollout whose cwd matches" —
    # unconditionally. A project whose newest session is older than dozens
    # of newer (other-project) days must still be found.
    home = tmp_path / "codexhome"
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = uuid7()
    match = _write_rollout(home, sid, str(proj), day="2026/01/01")
    # 60 newer day dirs, each holding an unrelated-project session.
    for i in range(60):
        _write_rollout(
            home, uuid7(), "/elsewhere", day=f"2026/03/{(i % 28) + 1:02d}"
        )
    found = find_latest_codex_session(proj, exclude=set(), codex_home=home)
    assert found == match


def test_codex_home_env_var_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Discovery must find sessions wherever the owner's codex writes them:
    # CODEX_HOME (when set) wins over the ~/.codex default.
    home = tmp_path / "custom-codex-home"
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = uuid7()
    path = _write_rollout(home, sid, str(proj))
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert find_codex_session_file(sid) == path
    assert find_latest_codex_session(proj, exclude=set()) == path


def test_store_survives_hostile_containers_and_fields(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    # Truthy NON-DICT containers (a plain `or {}` would pass these through).
    state.write_text(
        json.dumps({"records": [1, 2], "fork_ids": "nope"}), encoding="utf-8"
    )
    store = TunnelStore(state)
    assert store.all_records() == [] and store.known_fork_ids() == set()

    state.write_text(
        json.dumps({"records": None, "fork_ids": None}), encoding="utf-8"
    )
    store = TunnelStore(state)
    assert store.all_records() == [] and store.known_fork_ids() == set()

    # Null entries inside fork_ids must not poison the set (or later saves).
    state.write_text(
        json.dumps({"records": {"t": None}, "fork_ids": [None, "f1", 3]}),
        encoding="utf-8",
    )
    store = TunnelStore(state)
    assert store.known_fork_ids() == {"f1"}

    # A record missing thread_key derives it from its map key; null fields
    # normalize to usable values and the record still dispatches.
    state.write_text(
        json.dumps(
            {
                "records": {
                    "th:9": {
                        "handle": None,
                        "expert_session_id": None,
                        "project_dir": None,
                        "access": None,
                        "fork_session_id": None,
                        "backend": None,
                        "agent": None,
                        "asker": None,
                        "tmux_window": None,
                        "pending_instructions": None,
                        "created_at": None,
                        "last_used": None,
                    }
                },
                "fork_ids": [],
            }
        ),
        encoding="utf-8",
    )
    store = TunnelStore(state)
    rec = store.get("th:9")
    assert rec is not None
    assert rec.thread_key == "th:9" and rec.agent == "claude"
    assert rec.access == "read" and rec.last_used == 0.0
    cfg = TunnelConfig(state_path=state)
    assert isinstance(backend_for_record(cfg, store, rec), HeadlessBackend)


def test_registry_survives_hostile_containers_and_fields(
    tmp_path: Path,
) -> None:
    reg_path = tmp_path / "reg.json"
    for hostile in ("null", '{"records": [1]}', '{"records": null}'):
        reg_path.write_text(hostile, encoding="utf-8")
        assert Registry(reg_path).active() == []

    # Null string fields normalize; a record with no usable session_id is
    # dropped at the load boundary (it can never be bound or displayed).
    reg_path.write_text(
        json.dumps(
            {
                "records": {
                    "ok": {
                        "handle": None,
                        "session_id": "sid-ok",
                        "cwd": None,
                        "config_dir": None,
                        "access": None,
                        "label": None,
                        "transcript_path": None,
                        "created_at": None,
                    },
                    "broken": {"handle": "broken", "session_id": None},
                }
            }
        ),
        encoding="utf-8",
    )
    recs = Registry(reg_path).active()
    assert [r.handle for r in recs] == ["ok"]  # handle from the map key
    rec = recs[0]
    assert rec.agent == "claude" and rec.access == "read"
    assert rec.created_at == 0.0 and rec.transcript_path == ""


def test_registry_legacy_record_without_agent_key(tmp_path: Path) -> None:
    # Pre-agent-field data: the key is genuinely ABSENT, not blank.
    reg_path = tmp_path / "reg.json"
    reg_path.write_text(
        json.dumps(
            {
                "records": {
                    "old": {
                        "handle": "old",
                        "session_id": "sid-old",
                        "cwd": "/p",
                        "access": "write",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    rec = Registry(reg_path).get("old")
    assert rec is not None and rec.agent == "claude"
    assert rec.access == "write"


def test_published_skips_unusable_records(tmp_path: Path) -> None:
    # End-to-end through the CLI: a hostile record in the file must not
    # take down `published` for the good ones.
    reg_path = tmp_path / "reg.json"
    state_path = tmp_path / "state.json"
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[tunnel]\nstate_path = "{state_path}"\n'
        f'registry_path = "{reg_path}"\n',
        encoding="utf-8",
    )
    reg_path.write_text(
        json.dumps(
            {
                "records": {
                    "bad": {"handle": "bad", "session_id": None},
                    "good": {
                        "handle": "good",
                        "session_id": "sid-good",
                        "cwd": "/p",
                        "agent": "codex",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        tunnel_cli, ["published", "--config", str(cfg_file)]
    )
    assert result.exit_code == 0, result.output
    assert "good" in result.output and "[codex]" in result.output
    assert "bad" not in result.output


def test_share_hook_codex_home_with_sessions_segment(tmp_path: Path) -> None:
    # The hook's CODEX_HOME derivation must be structural: a home path that
    # itself contains a `sessions` segment must not be truncated, or the
    # published record points at the wrong tree and can't be resumed.
    registry = tmp_path / "registry.json"
    home = f"{tmp_path}/srv/sessions/alice/.codex"
    transcript = f"{home}/sessions/2026/07/16/rollout-2026-07-16T10-00-00-x.jsonl"
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": "codex-nested",
            "prompt": ">share nested",
            "cwd": str(tmp_path),
            "transcript_path": transcript,
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
    rec = json.loads(registry.read_text())["records"]["nested"]
    assert rec["agent"] == "codex"
    assert rec["config_dir"] == home  # NOT truncated at the first /sessions/


def test_doctor_codex_only_setup(tmp_path: Path, monkeypatch) -> None:
    # A codex-only deployment (codex handle, no claude/tmux) must pass
    # doctor: claude/tmux are not required when nothing claude is published.
    import shutil

    reg_path = tmp_path / "reg.json"
    state_path = tmp_path / "state.json"
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[tunnel]\nbackend = "tmux"\nstate_path = "{state_path}"\n'
        f'registry_path = "{reg_path}"\n'
        '[discord]\nchannel_ids = [1]\n'
        f'[codex]\nbinary = "codex"\n[claude]\nbinary = "claude-absent-xyz"\n',
        encoding="utf-8",
    )
    Registry(reg_path).publish(
        session_id="sid-cx", cwd="/p", agent="codex", label="gpt"
    )
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda b: None if b == "claude-absent-xyz" else (real_which(b) or b),
    )
    monkeypatch.setattr(
        "claude_code_tools.agent_tunnel.discord_bot.resolve_token",
        lambda cfg: "tok",
    )
    result = CliRunner().invoke(
        tunnel_cli, ["doctor", "--config", str(cfg_file)]
    )
    # The missing claude binary must NOT fail a codex-only setup.
    assert result.exit_code == 0, result.output
    assert "claude-absent-xyz" not in result.output
    assert "codex binary on PATH" in result.output


def test_registry_publish_identity_is_agent_plus_session(
    tmp_path: Path,
) -> None:
    # Two sessions sharing an id but on different agents are distinct owners:
    # publishing/relabeling/revoking one must never touch the other.
    reg = Registry(tmp_path / "reg.json")
    reg.publish(session_id="dup", cwd="/p", agent="claude", label="clau")
    reg.publish(session_id="dup", cwd="/q", agent="codex", label="cdx")
    assert reg.get("clau").agent == "claude"  # type: ignore[union-attr]
    assert reg.get("cdx").agent == "codex"  # type: ignore[union-attr]

    # A same-id/other-agent re-publish keeps its OWN handle, leaves the
    # other's intact.
    reg.publish(session_id="dup", cwd="/q", agent="codex", access="write")
    assert reg.get("cdx").access == "write"  # type: ignore[union-attr]
    assert reg.get("clau").access == "read"  # type: ignore[union-attr]

    # Relabeling the codex one onto a NEW handle must not collide with, or
    # remove, the claude one.
    handle, collision = reg.publish(
        session_id="dup", cwd="/q", agent="codex", label="cdx2"
    )
    assert (handle, collision) == ("cdx2", None)
    assert reg.get("clau") is not None  # claude handle untouched


def test_share_hook_identity_is_agent_plus_session(tmp_path: Path) -> None:
    # The standalone hook mirrors (agent, session_id) identity: a codex
    # >share off must not revoke a claude session with the same id.
    registry = tmp_path / "registry.json"
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}

    def run(prompt, transcript):
        payload = json.dumps(
            {
                "session_id": "shared-id",
                "prompt": prompt,
                "cwd": str(tmp_path),
                "transcript_path": transcript,
            }
        )
        r = subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload, capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["reason"]

    claude_tx = f"{tmp_path}/.claude/projects/-x/shared-id.jsonl"
    codex_tx = (
        f"{tmp_path}/.codex/sessions/2026/07/16/"
        "rollout-2026-07-16T10-00-00-shared-id.jsonl"
    )
    run(">share clau", claude_tx)
    run(">share cdx", codex_tx)
    recs = json.loads(registry.read_text())["records"]
    assert recs["clau"]["agent"] == "claude"
    assert recs["cdx"]["agent"] == "codex"
    # Revoke the codex one; the claude one stays live.
    run(">share off", codex_tx)
    recs = json.loads(registry.read_text())["records"]
    assert recs["cdx"]["revoked"] is True
    assert recs["clau"]["revoked"] is False


def test_current_access_not_synced_across_agents(tmp_path: Path) -> None:
    # A codex thread bound under the sentinel "cli" handle must NOT inherit a
    # CLAUDE registry record sharing the same handle + session id (identity
    # is (agent, session_id)) — else a read thread is silently escalated.
    # (The positive same-agent sync is covered in test_agent_tunnel_backends.)
    from claude_code_tools.agent_tunnel.codex_backend import (
        CodexHeadlessBackend,
    )

    cfg = TunnelConfig(
        state_path=tmp_path / "s.json", registry_path=tmp_path / "reg.json"
    )
    store = TunnelStore(cfg.state_path)
    store.bind(
        "t", "cli", "shared-sid", "/p", "headless",
        access="read", agent="codex",
    )
    # A CLAUDE "cli" record with the SAME session id, granted write.
    Registry(cfg.registry_path).publish(
        session_id="shared-sid", cwd="/p", agent="claude",
        access="write", label="cli",
    )
    rec = CodexHeadlessBackend(cfg, store)._require_binding("t")
    assert rec.access == "read"  # not escalated by the other agent's record
    assert store.get("t").access == "read"  # type: ignore[union-attr]


def test_rename_rejects_cross_agent_same_id_collision(tmp_path: Path) -> None:
    # Renaming a codex handle onto a LIVE claude handle that shares its
    # session id must be refused, preserving both records.
    reg = Registry(tmp_path / "reg.json")
    reg.publish(session_id="dup", cwd="/p", agent="claude", label="clau")
    reg.publish(session_id="dup", cwd="/q", agent="codex", label="cdx")
    ok, msg = reg.rename("cdx", "clau")
    assert not ok and "already used" in msg
    assert reg.get("clau").agent == "claude"  # type: ignore[union-attr]
    assert reg.get("cdx").agent == "codex"  # type: ignore[union-attr]


def test_fork_reads_stable_snapshot_of_active_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Forking a session being appended to must not copy a torn trailing
    # record: a body captured mid-append (no trailing newline) drops its
    # last partial line so the fork is always valid JSONL.
    home = tmp_path / "codexhome"
    sid = uuid7()
    src = _write_rollout(home, sid, "/proj", user_texts=("q1",))
    # Simulate a torn read: append a partial (newline-less) record.
    with open(src, "a", encoding="utf-8") as f:
        f.write('{"type":"response_item","payload":{"type":"message"')  # torn

    new_id, dest = fork_codex_session(src)
    lines = dest.read_text().split("\n")
    # Every copied line is complete JSON (the torn tail was dropped).
    for ln in lines:
        if ln:
            json.loads(ln)
    assert dest.read_text().endswith("\n")


def test_fork_preserves_unicode_line_separators_in_json(
    tmp_path: Path,
) -> None:
    # A JSON string legitimately containing U+2028/U+2029/U+0085 is ONE JSONL
    # record: forking must not shatter it on those Unicode boundaries (only
    # "\n" delimits JSONL). Every forked line must still parse.
    home = tmp_path / "codexhome"
    sid = uuid7()
    day_dir = home / "sessions" / "2026/07/16"
    day_dir.mkdir(parents=True, exist_ok=True)
    src = day_dir / f"rollout-2026-07-16T10-00-00-{sid}.jsonl"
    meta = {"type": "session_meta", "payload": {"id": sid, "cwd": "/p"}}
    tricky = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "a b cd"}],
        },
    }
    src.write_text(
        json.dumps(meta) + "\n"
        + json.dumps(tricky, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    new_id, dest = fork_codex_session(src)
    body = dest.read_text(encoding="utf-8")
    parsed = [json.loads(ln) for ln in body.split("\n") if ln]
    assert len(parsed) == 2  # the tricky record stayed intact
    assert parsed[1]["payload"]["content"][0]["text"] == "a b cd"
    assert parsed[0]["payload"]["forked_from_id"] == sid


def test_share_hook_survives_corrupt_registry(tmp_path: Path) -> None:
    # A null registry root must not silently disable >share: the hook
    # rebuilds the file with the new record.
    registry = tmp_path / "registry.json"
    registry.write_text("null", encoding="utf-8")
    env = {**os.environ, "AGENT_TUNNEL_REGISTRY": str(registry)}
    payload = json.dumps(
        {
            "session_id": "sess-corrupt",
            "prompt": ">share revived",
            "cwd": str(tmp_path),
            "transcript_path": f"{tmp_path}/.claude/projects/-x/abc.jsonl",
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
    assert "Sharing this session as: revived" in (
        json.loads(result.stdout)["reason"]
    )
    rec = json.loads(registry.read_text())["records"]["revived"]
    assert rec["session_id"] == "sess-corrupt"
