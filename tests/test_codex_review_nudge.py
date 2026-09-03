"""codex-review-nudge hook: a nudge, never a gate, and never slow.

Drives the real script as a subprocess with a JSON payload, inside a
temporary git repo whose ``origin`` points at GitHub, with a fake ``gh``
on PATH that answers from canned files — so every GitHub-dependent path
runs for real without the network. Pure helpers are imported directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[1]
    / "plugins" / "workflow" / "hooks" / "pr_review_nudge.py"
)

SUMMARY = (
    "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
    "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
    "| 📝 **Code Review** | {status} | `{sha}` | PR opened |\n"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("pr_review_nudge", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# -- pure helpers -------------------------------------------------------------


def test_concat_json_arrays_handles_paginated_pages() -> None:
    m = _load_module()
    assert m._concat_json_arrays('[1,2]\n[3]') == [1, 2, 3]
    assert m._concat_json_arrays('[]') == []
    assert m._concat_json_arrays('{"a":1}') is None
    assert m._concat_json_arrays('[1,') is None
    assert m._concat_json_arrays('') is None  # empty is unknown, not []


def test_parse_summary_reads_status_and_sha() -> None:
    m = _load_module()
    body = SUMMARY.format(status="✅ **Completed** 2026-09-03", sha="1e8dd8f")
    assert m.parse_summary(body) == ("completed", "1e8dd8f")
    body = SUMMARY.format(status="🔄 **Running** since", sha="ad2dcbd")
    assert m.parse_summary(body) == ("running", "ad2dcbd")
    assert m.parse_summary("no table here") == (None, None)


@pytest.mark.parametrize(
    "st,head,expect_words",
    [
        ({"status": "completed", "reviewed_sha": "1e8dd8f", "unresolved": 0},
         "1e8dd8f0000", None),
        ({"status": "completed", "reviewed_sha": "0000000", "unresolved": 0},
         "1e8dd8f0000", "no completed Codex review of the current head"),
        ({"status": "running", "reviewed_sha": "1e8dd8f", "unresolved": 0},
         "1e8dd8f0000", "still reviewing"),
        ({"status": "completed", "reviewed_sha": "1e8dd8f", "unresolved": 2},
         "1e8dd8f0000", "2 unresolved review thread"),
        ({"status": None, "reviewed_sha": None, "unresolved": 0},
         "1e8dd8f0000", "may still be starting"),
    ],
)
def test_needs_nudge_rules(st, head, expect_words) -> None:  # noqa: ANN001
    m = _load_module()
    reason = m.needs_nudge({"headRefOid": head}, st)
    if expect_words is None:
        assert reason is None
    else:
        assert reason is not None and expect_words in reason


# -- end-to-end with a fake gh ------------------------------------------------


FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
a = sys.argv[1:]
def out(name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        sys.exit(1)
    sys.stdout.write(open(p).read()); sys.exit(0)
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
if os.environ.get("FAKE_GH_STALL"):
    import time; time.sleep(60)  # a hung gh: the hook must not wait for it
if a[:2] == ["repo", "view"]:
    sys.stdout.write("acme/widgets\n"); sys.exit(0)
if a[:2] == ["pr", "list"]:
    out("pr_list_head.json" if "--head" in a else "pr_list.json")
if a[:1] == ["api"] and "graphql" in a:
    out("threads.json")
if a[:1] == ["api"] and "/comments" in " ".join(a):
    out("comments.json")
sys.exit(1)
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """Temp git repo on a feature branch with a GitHub origin + fake gh."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "git@github.com:acme/widgets.git"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/x"], cwd=root, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()
    ghdir = tmp_path / "gh"
    ghdir.mkdir()
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (ghdir / "pr_list.json").write_text(json.dumps([
        {"number": 7, "headRefName": "feat/x", "headRefOid": head,
         "url": "https://github.com/acme/widgets/pull/7"},
        {"number": 8, "headRefName": "not/checked-out", "headRefOid": "f" * 40,
         "url": "https://github.com/acme/widgets/pull/8"},
    ]))
    (ghdir / "pr_list_head.json").write_text(json.dumps([{"number": 7, "url": "u"}]))
    (ghdir / "threads.json").write_text(json.dumps({"data": {"repository": {
        "pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": []}}}}}))
    (ghdir / "comments.json").write_text("[]")  # one empty page
    state = tmp_path / "state"
    monkeypatch.setenv("FAKE_GH_DIR", str(ghdir))
    monkeypatch.setenv("CODEX_REVIEW_NUDGE_STATE", str(state))
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    return {"root": root, "gh": ghdir, "head": head, "state": state}


def _mark_completed(repo: dict, sha: str) -> None:
    """Fake gh: Codex's summary says Completed for ``sha`` (two pages, as
    ``gh api --paginate`` concatenates them)."""
    body = SUMMARY.format(status="✅ **Completed** now", sha=sha[:7])
    (repo["gh"] / "comments.json").write_text("[]" + json.dumps([{"body": body}]))


def _expire_cache(repo: dict) -> None:
    """Drop the 60 s GitHub-result cache but KEEP the once-per-state memory,
    so tests exercise the dedupe rather than a wiped slate."""
    f = repo["state"] / "acme__widgets.json"
    if f.exists():
        d = json.loads(f.read_text())
        d.pop("cache", None)
        f.write_text(json.dumps(d))


def _hook(event: str, cwd: Path, **extra) -> dict | None:  # noqa: ANN003
    payload = {"hook_event_name": event, "cwd": str(cwd),
               "session_id": "s1", **extra}
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr  # never fails the agent
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def test_stop_nudges_once_for_unreviewed_pr_on_local_branch(repo) -> None:  # noqa: ANN001
    out = _hook("Stop", repo["root"])
    assert out is not None and out["decision"] == "block"
    assert "PR #7" in out["reason"]
    assert "PR #8" not in out["reason"]  # branch not checked out here
    assert "@codex review" in out["reason"]
    assert "one-time" in out["reason"]
    # Same state again: silent. That is what makes it a nudge, not a gate.
    assert _hook("Stop", repo["root"]) is None


def test_stop_silent_when_codex_completed_on_head_and_no_threads(repo) -> None:  # noqa: ANN001
    _mark_completed(repo, repo["head"])
    assert _hook("Stop", repo["root"]) is None


def test_stop_nudges_again_for_new_head_or_new_threads(repo) -> None:  # noqa: ANN001
    _mark_completed(repo, repo["head"])
    assert _hook("Stop", repo["root"]) is None
    # A finding appears: one unresolved thread -> a fresh nudge...
    (repo["gh"] / "threads.json").write_text(json.dumps({"data": {"repository": {
        "pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"id": "T1", "isResolved": False}]}}}}}))
    _expire_cache(repo)
    out = _hook("Stop", repo["root"])
    assert out is not None and "1 unresolved review thread" in out["reason"]
    # ...and only once for that state.
    assert _hook("Stop", repo["root"]) is None


def test_stop_nudges_again_after_a_push_moves_the_head(repo) -> None:  # noqa: ANN001
    """Codex reviews are per head: a new commit is a new state."""
    _mark_completed(repo, repo["head"])
    assert _hook("Stop", repo["root"]) is None
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
                    "-q", "--allow-empty", "-m", "more"], cwd=repo["root"], check=True)
    new_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo["root"],
                              capture_output=True, text=True).stdout.strip()
    prs = json.loads((repo["gh"] / "pr_list.json").read_text())
    prs[0]["headRefOid"] = new_head  # GitHub now reports the pushed head
    (repo["gh"] / "pr_list.json").write_text(json.dumps(prs))
    out = _hook("Stop", repo["root"])
    assert out is not None
    assert "no completed Codex review of the current head" in out["reason"]
    assert "@codex review" in out["reason"]
    assert _hook("Stop", repo["root"]) is None  # once per state


def test_swapped_thread_same_count_is_a_new_state(repo) -> None:  # noqa: ANN001
    """Resolve one thread, get another: count unchanged, finding is new."""
    def threads(*ids):  # noqa: ANN002, ANN202
        (repo["gh"] / "threads.json").write_text(json.dumps({"data": {"repository": {
            "pullRequest": {"reviewThreads": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"id": i, "isResolved": False} for i in ids]}}}}}))
        _expire_cache(repo)

    _mark_completed(repo, repo["head"])
    threads("T1")
    assert _hook("Stop", repo["root"]) is not None
    assert _hook("Stop", repo["root"]) is None
    threads("T2")  # T1 resolved, T2 appeared: still exactly one unresolved
    out = _hook("Stop", repo["root"])
    assert out is not None and "1 unresolved review thread" in out["reason"]
    # And the dedupe memory survived: T1's key is still recorded.
    state = json.loads((repo["state"] / "acme__widgets.json").read_text())
    assert any(k.endswith(":T1") for k in state["nudged"])
    assert any(k.endswith(":T2") for k in state["nudged"])


def test_empty_gh_output_is_silence_not_a_nudge(repo) -> None:  # noqa: ANN001
    (repo["gh"] / "comments.json").write_text("")  # gh "succeeded" with nothing
    assert _hook("Stop", repo["root"]) is None


def test_stop_never_nudges_while_already_continuing(repo) -> None:  # noqa: ANN001
    assert _hook("Stop", repo["root"], stop_hook_active=True) is None


def test_stop_silent_outside_a_github_repo(tmp_path, repo) -> None:  # noqa: ANN001
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _hook("Stop", plain) is None
    subprocess.run(["git", "remote", "set-url", "origin",
                    "https://gitlab.com/acme/widgets.git"],
                   cwd=repo["root"], check=True)
    assert _hook("Stop", repo["root"]) is None


def test_stop_silent_when_gh_is_broken(repo, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """gh missing/offline must mean silence, not a nudge and not a hang."""
    import shutil

    git = shutil.which("git")
    assert git
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(only_git))  # git yes, gh no
    assert _hook("Stop", repo["root"]) is None


def test_stop_returns_within_budget_when_gh_hangs(repo, monkeypatch) -> None:  # noqa: ANN001
    """A hung gh must not make the hook hang: every call is bounded and
    the whole check finishes inside the host's hook timeout."""
    import time

    monkeypatch.setenv("FAKE_GH_STALL", "1")
    t0 = time.monotonic()
    assert _hook("Stop", repo["root"]) is None
    assert time.monotonic() - t0 < 19  # hooks.json gives Stop 20 s


def test_unwritable_state_means_silence_not_a_repeating_nudge(
    repo, monkeypatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("CODEX_REVIEW_NUDGE_STATE", "/dev/null/nope")
    assert _hook("Stop", repo["root"]) is None
    assert _hook("Stop", repo["root"]) is None


def test_post_tool_use_nudges_on_pr_create_and_push(repo) -> None:  # noqa: ANN001
    out = _hook("PostToolUse", repo["root"], tool_name="Bash",
                tool_input={"command": "gh pr create --title x --body y"})
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "pull request was just created" in ctx
    out = _hook("PostToolUse", repo["root"], tool_name="Bash",
                tool_input={"command": "git push -u origin feat/x"})
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "PR #7" in ctx and "@codex review" in ctx
    # Unrelated commands: nothing.
    assert _hook("PostToolUse", repo["root"], tool_name="Bash",
                 tool_input={"command": "ls -la"}) is None


@pytest.mark.parametrize(
    "cmd,expect",
    [
        ("git push", ["feat/x"]),  # bare: current branch
        ("git push -u origin feat/x", ["feat/x"]),
        ("git push origin other-branch", ["other-branch"]),
        ("git push origin HEAD:refs/heads/dst-branch", ["dst-branch"]),
        ("git push --force-with-lease origin topic", ["topic"]),
        ("git push origin main && echo done", ["main"]),
        ("git push origin feat/x\necho done", ["feat/x"]),  # multiline
        ("git push origin feat/x -o ci.skip", ["feat/x"]),  # option value
        ("git push origin feat/x >/tmp/push.log 2>&1", ["feat/x"]),  # redirect
        ("git push origin branch-a branch-b", ["branch-a", "branch-b"]),
    ],
)
def test_pushed_branches_read_the_refspecs(repo, cmd, expect) -> None:  # noqa: ANN001
    m = _load_module()
    assert m.pushed_branches(cmd, str(repo["root"])) == expect


def test_push_reminder_is_scoped_to_my_prs(repo) -> None:  # noqa: ANN001
    _hook("PostToolUse", repo["root"], tool_name="Bash",
          tool_input={"command": "git push origin feat/x"})
    calls = (repo["gh"] / "calls.log").read_text()
    head_call = [c for c in calls.splitlines() if "--head" in c][-1]
    assert "--author @me" in head_call and "--head feat/x" in head_call


def test_garbage_input_is_harmless() -> None:
    for raw in ("", "not json", "[]", json.dumps({"hook_event_name": "Weird"})):
        proc = subprocess.run([sys.executable, str(HOOK)], input=raw,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0 and proc.stdout.strip() == ""
