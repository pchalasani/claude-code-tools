"""Headless follow-ups re-fork from the LATEST expert session.

A thread's fork is a copy of the expert session taken when the thread
opened. When the expert keeps working, a follow-up should see that newer
work, so the headless backend re-forks from the expert and carries the
thread's earlier Q&A forward as a recap.

These tests drive the real HeadlessBackend against a stand-in ``claude``
executable (a small script) that records each invocation, so the argv and
stdin the backend produces are checked end to end without a real model.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from claude_code_tools.agent_tunnel.backends import HeadlessBackend, build_recap
from claude_code_tools.agent_tunnel.config import TunnelConfig, load_config
from claude_code_tools.agent_tunnel.session import transcript_dir
from claude_code_tools.agent_tunnel.store import ThreadRecord, TunnelStore

EXPERT = "11111111-2222-3333-4444-555555555555"

FAKE_CLAUDE = """\
import json, os, sys, uuid
argv = sys.argv[1:]
prompt = sys.stdin.read()
resume = argv[argv.index("--resume") + 1]
sid = str(uuid.uuid4()) if "--fork-session" in argv else resume
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": prompt, "sid": sid}) + "\\n")
n = sum(1 for _ in open(os.environ["FAKE_CLAUDE_LOG"], encoding="utf-8"))
print(json.dumps({"result": f"answer {n}", "session_id": sid}))
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A config, store, fake claude, and an expert transcript on disk."""
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    launcher = tmp_path / "claude"
    launcher.write_text(
        f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n", encoding="utf-8"
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    tdir = transcript_dir(project, home)
    tdir.mkdir(parents=True)
    expert_file = tdir / f"{EXPERT}.jsonl"
    expert_file.write_text('{"type":"user"}\n', encoding="utf-8")

    cfg = TunnelConfig(state_path=tmp_path / "state" / "state.json")
    cfg.claude.binary = str(launcher)
    store = TunnelStore(cfg.state_path)
    store.bind(
        "th:1",
        handle="h",
        expert_session_id=EXPERT,
        project_dir=str(project),
        config_dir=str(home),
        backend="headless",
    )

    def calls() -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text().splitlines()]

    return cfg, store, expert_file, calls


def _grow(expert_file: Path) -> None:
    with open(expert_file, "a", encoding="utf-8") as f:
        f.write('{"type":"assistant","more":"work"}\n')


def test_first_turn_forks_and_records_base(env) -> None:
    cfg, store, expert_file, calls = env
    ans = HeadlessBackend(cfg, store).ask("th:1", "Q1")
    assert ans.new_thread and not ans.refreshed
    (call,) = calls()
    assert call["argv"][call["argv"].index("--resume") + 1] == EXPERT
    assert "--fork-session" in call["argv"]
    assert call["stdin"] == "Q1"
    rec = store.get("th:1")
    assert rec.fork_session_id == ans.fork_session_id
    assert rec.fork_base_size == expert_file.stat().st_size
    assert rec.history == [{"q": "Q1", "a": "answer 1"}]


def test_followup_resumes_fork_when_expert_unchanged(env) -> None:
    cfg, store, _, calls = env
    backend = HeadlessBackend(cfg, store)
    first = backend.ask("th:1", "Q1")
    second = backend.ask("th:1", "Q2")
    assert not second.refreshed and not second.new_thread
    call = calls()[1]
    assert "--fork-session" not in call["argv"]
    assert call["argv"][call["argv"].index("--resume") + 1] == (
        first.fork_session_id
    )
    assert call["stdin"] == "Q2"


def test_followup_reforks_from_latest_expert_with_recap(env) -> None:
    cfg, store, expert_file, calls = env
    backend = HeadlessBackend(cfg, store)
    first = backend.ask("th:1", "Q1")
    _grow(expert_file)
    second = backend.ask("th:1", "Q2")

    assert second.refreshed
    assert second.fork_session_id != first.fork_session_id
    call = calls()[1]
    assert "--fork-session" in call["argv"]
    assert call["argv"][call["argv"].index("--resume") + 1] == EXPERT
    # The earlier exchange rides along, then the new question.
    assert "Q1" in call["stdin"] and "answer 1" in call["stdin"]
    assert call["stdin"].rstrip().endswith("Q2")
    rec = store.get("th:1")
    assert rec.fork_session_id == second.fork_session_id
    assert rec.fork_base_size == expert_file.stat().st_size
    # History records the bare question, never the recap wrapper.
    assert rec.history[-1] == {"q": "Q2", "a": "answer 2"}

    # The fresh fork is now current: the next follow-up resumes it.
    backend.ask("th:1", "Q3")
    call = calls()[2]
    assert "--fork-session" not in call["argv"]
    assert call["argv"][call["argv"].index("--resume") + 1] == (
        second.fork_session_id
    )


def test_refresh_can_be_turned_off(env) -> None:
    cfg, store, expert_file, calls = env
    cfg.refresh_forks = False
    backend = HeadlessBackend(cfg, store)
    first = backend.ask("th:1", "Q1")
    _grow(expert_file)
    second = backend.ask("th:1", "Q2")
    assert not second.refreshed
    assert second.fork_session_id == first.fork_session_id
    assert "--fork-session" not in calls()[1]["argv"]


def test_legacy_record_without_base_records_baseline(env) -> None:
    # A thread forked before this feature has no recorded base size. Its
    # next turn cannot tell whether the expert moved, so it resumes the old
    # fork and records a baseline; a later expert change then re-forks.
    cfg, store, expert_file, calls = env
    rec = store.get("th:1")
    rec.fork_session_id = "legacy-fork"
    store.upsert(rec)
    backend = HeadlessBackend(cfg, store)
    ans = backend.ask("th:1", "Q1")
    assert not ans.refreshed
    assert calls()[0]["argv"][calls()[0]["argv"].index("--resume") + 1] == (
        "legacy-fork"
    )
    assert store.get("th:1").fork_base_size == expert_file.stat().st_size
    _grow(expert_file)
    assert backend.ask("th:1", "Q2").refreshed


def test_missing_expert_transcript_resumes_fork(env) -> None:
    cfg, store, expert_file, _ = env
    backend = HeadlessBackend(cfg, store)
    first = backend.ask("th:1", "Q1")
    os.remove(expert_file)
    second = backend.ask("th:1", "Q2")
    assert not second.refreshed
    assert second.fork_session_id == first.fork_session_id


def test_build_recap_keeps_latest_within_budget() -> None:
    history = [{"q": f"question {i}", "a": "x" * 300} for i in range(10)]
    recap = build_recap(history, max_chars=1000)
    assert len(recap) <= 1000
    assert "question 9" in recap  # newest kept
    assert "question 0" not in recap  # oldest dropped first
    assert build_recap([], max_chars=1000) == ""


def test_build_recap_trims_single_huge_answer() -> None:
    recap = build_recap([{"q": "q", "a": "y" * 50_000}], max_chars=2000)
    assert 0 < len(recap) <= 2000
    assert "q" in recap


def test_history_is_bounded_in_state(env) -> None:
    cfg, store, _, _ = env
    cfg.limits.recap_max_chars = 500
    backend = HeadlessBackend(cfg, store)
    for i in range(30):
        backend.ask("th:1", f"Q{i} " + "z" * 200)
    rec = store.get("th:1")
    total = sum(len(t["q"]) + len(t["a"]) for t in rec.history)
    assert total <= 2 * cfg.limits.recap_max_chars
    assert rec.history[-1]["q"].startswith("Q29")


def test_config_loads_refresh_settings(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[tunnel]\nrefresh_forks = false\n[limits]\nrecap_max_chars = 1234\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.refresh_forks is False
    assert cfg.limits.recap_max_chars == 1234
    assert TunnelConfig().refresh_forks is True


def test_store_roundtrips_new_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    store = TunnelStore(path)
    store.bind("k", "h", EXPERT, "/p", "headless")
    rec = store.get("k")
    rec.fork_session_id = "f"
    rec.fork_base_size = 42
    rec.history = [{"q": "a", "a": "b"}]
    store.upsert(rec)
    again = TunnelStore(path).get("k")
    assert isinstance(again, ThreadRecord)
    assert again.fork_base_size == 42
    assert again.history == [{"q": "a", "a": "b"}]


def test_build_recap_never_exceeds_tiny_budget() -> None:
    from claude_code_tools.agent_tunnel.backends import (
        RECAP_FOOTER,
        RECAP_HEADER,
    )

    tiny = len(RECAP_HEADER) + len(RECAP_FOOTER) + 5
    recap = build_recap([{"q": "q", "a": "z" * 5000}], max_chars=tiny)
    assert recap == ""  # no room for any turn: no empty shell


def test_state_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    TunnelStore(path).bind("k", "h", EXPERT, "/p", "headless")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
