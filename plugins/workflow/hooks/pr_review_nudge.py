#!/usr/bin/env python3
"""Nudge the agent to run the GitHub Codex review loop on its pull requests.

A nudge, never a gate. Two hook events, one script (Claude Code and Codex
CLI both speak this payload shape):

- **PostToolUse (Bash)**: after ``gh pr create`` or a ``git push`` whose
  branch has an open PR, inject a short reminder to monitor GitHub Codex's
  review and re-request one after every push.
- **Stop**: when the agent tries to end its turn, look at GitHub — not at
  what commands ran — for open PRs authored by the user whose head branch
  is checked out in a local worktree of this repo. For each one whose
  current head has no completed Codex review, or which has unresolved
  review threads, deliver ONE nudge for that exact state (PR, head,
  unresolved count) by interrupting the stop once with the message; the
  next stop in the same state passes silently. The agent decides:
  address, defer to an issue, or dismiss with a reply.

Checking reality on Stop instead of pattern-matching commands means a PR
created via ``gh api``, by a subagent, or by hand is still covered.

Speed: at most a handful of ``gh`` calls, each bounded by a short timeout;
results are cached briefly. Anything that fails — no git repo, no GitHub
remote, ``gh`` missing or offline — makes the hook silent, never slow and
never blocking. Single file with no third-party imports, because Codex
hash-trusts the entry script only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_MAX_STDIN_BYTES = 1_000_000
_GH_TIMEOUT = 6.0  # seconds per gh call
_TOTAL_BUDGET = 12.0  # seconds for the whole Stop check
_CACHE_TTL = 60.0  # seconds a computed PR status stays fresh
_SUMMARY_MARKER = "codex-pull-request-review-summary"

STATE_DIR = Path(
    os.environ.get("CODEX_REVIEW_NUDGE_STATE")
    or os.path.expanduser("~/.local/state/codex-review-nudge")
)

# Commands that make a PR appear or change on GitHub.
_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
_GIT_PUSH_RE = re.compile(r"\bgit\s+push\b(?P<rest>[^|;&\n]*)")
# push options that consume the next token
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}


def pushed_branches(cmd: str, cwd: str) -> list[str]:
    """Branches a ``git push`` command sent, from its refspecs when given.

    ``git push origin a b`` pushes ``a`` and ``b`` (dst of ``src:dst``
    when a refspec is written), not the checked-out branch; only a bare
    ``git push`` means the current branch. Options, their values, and
    shell redirections are skipped. Best-effort: unrecognised forms fall
    back to the current branch.
    """
    m = _GIT_PUSH_RE.search(cmd)
    if not m:
        return []
    try:
        import shlex

        tokens = shlex.split(m.group("rest"))
    except ValueError:
        tokens = m.group("rest").split()
    words: list[str] = []
    skip = False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok in _PUSH_VALUE_OPTS:
            skip = True
            continue
        if tok.startswith("-") or tok.startswith((">", "<", "2>")):
            continue
        words.append(tok)
    # words: [remote, refspec...]
    branches = []
    for spec in words[1:]:
        dst = spec.split(":", 1)[1] if ":" in spec else spec
        dst = dst.replace("refs/heads/", "")
        if dst and dst != "HEAD" and not dst.startswith("+"):
            branches.append(dst)
    if branches:
        return branches
    cur = _current_branch(cwd)
    return [cur] if cur else []


_LOOP_INSTRUCTIONS = (
    "Run the GitHub Codex review loop (github-codex-review skill): watch "
    "the PR's Codex review-summary comment until it shows Completed for "
    "the CURRENT head; for each finding decide — fix it, defer it to an "
    "issue, or dismiss it with a reply — and resolve the thread; after "
    "EVERY push, comment '@codex review' to get a fresh review of the new "
    "head and check again. A converged PR plus a written deferred list is "
    "a success."
)


# -- small helpers ----------------------------------------------------------


_DEADLINE: list[float] = []  # monotonic time by which the Stop check must end


def _remaining(timeout: float) -> float:
    if _DEADLINE:
        return max(0.2, min(timeout, _DEADLINE[0] - time.monotonic()))
    return timeout


def _run(args: list[str], cwd: str | None = None, timeout: float = _GH_TIMEOUT):
    """Run a command; return stdout or None on any failure. Never raises.

    Every call is bounded by both its own timeout and the Stop deadline,
    so the whole check finishes inside the host's hook timeout.
    """
    timeout = _remaining(timeout)
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _repo_root(cwd: str) -> str | None:
    out = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=3.0)
    return out.strip() if out else None


def _github_slug(root: str) -> str | None:
    """owner/repo of the repository ``gh`` would act on, or None.

    Asks gh first so a fork setup (origin = fork, upstream = default repo)
    resolves to the same repository ``gh pr list`` uses; falls back to
    parsing origin when gh cannot answer. Non-GitHub origins yield None.
    """
    out = _run(["git", "remote", "get-url", "origin"], cwd=root, timeout=3.0)
    if not out or "github.com" not in out:
        return None
    gh = _run(["gh", "repo", "view", "--json", "nameWithOwner",
               "-q", ".nameWithOwner"], cwd=root)
    if gh and gh.strip().count("/") == 1:
        return gh.strip()
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", out.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _worktree_branches(root: str) -> set[str]:
    out = _run(["git", "worktree", "list", "--porcelain"], cwd=root, timeout=3.0)
    if not out:
        return set()
    return {
        line.split("refs/heads/", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("branch refs/heads/")
    }


def _current_branch(cwd: str) -> str | None:
    out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, timeout=3.0)
    b = out.strip() if out else ""
    return b if b and b != "HEAD" else None


def _gh_json(args: list[str], cwd: str):
    out = _run(["gh", *args], cwd=cwd)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _concat_json_arrays(text: str) -> list | None:
    """Flatten ``gh api --paginate`` output: pages arrive as JSON arrays
    concatenated back to back (``[...][...]``). Works on every gh version
    (``--slurp`` does not). None if the text is not well-formed."""
    if not text.strip():
        return None  # nothing came back: unknown, never "no comments"
    dec = json.JSONDecoder()
    i, n, items = 0, len(text), []
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            page, i = dec.raw_decode(text, i)
        except ValueError:
            return None
        if isinstance(page, list):
            items.extend(page)
        else:
            return None
    return items


# -- GitHub state -------------------------------------------------------------


def parse_summary(body: str) -> tuple[str | None, str | None]:
    """(status, sha7) from Codex's review-summary table, or (None, None).

    The table row looks like ``| 📝 **Code Review** | ✅ **Completed** ... |
    `1e8dd8f` | ...`` (or 🔄 **Running**). Only the Code Review row counts.
    """
    for line in body.splitlines():
        if "Code Review" not in line:
            continue
        status = None
        if "Completed" in line:
            status = "completed"
        elif "Running" in line:
            status = "running"
        sha = re.search(r"`([0-9a-f]{7,40})`", line)
        return status, (sha.group(1) if sha else None)
    return None, None


def open_prs_on_local_branches(root: str, cwd: str) -> tuple[list[dict], list[dict]]:
    """(PRs on locally checked-out branches, all open PRs) by the user."""
    branches = _worktree_branches(root)
    prs = _gh_json(
        [
            "pr", "list", "--author", "@me", "--state", "open",
            # gh pr list does not paginate; 200 open PRs by one author is
            # beyond any real workflow, so truncation is accepted here.
            "--limit", "200", "--json", "number,headRefName,headRefOid,url",
        ],
        cwd=cwd,
    )
    if not isinstance(prs, list):
        return [], []
    prs = [p for p in prs if isinstance(p, dict)]
    return [p for p in prs if p.get("headRefName") in branches], prs


def codex_status(slug: str, number: int, cwd: str) -> dict | None:
    """Codex review state for one PR: {status, reviewed_sha, unresolved}.

    None when GitHub could not be queried (caller stays silent: an
    unanswered question is not a finding).
    """
    raw = _run(
        ["gh", "api", "--paginate", "-X", "GET", "-f", "per_page=100",
         f"repos/{slug}/issues/{number}/comments"],
        cwd=cwd,
    )
    if raw is None:
        return None
    comments = _concat_json_arrays(raw)
    if comments is None:
        return None
    bodies = [
        str(c.get("body", "")) for c in comments
        if isinstance(c, dict) and _SUMMARY_MARKER in str(c.get("body", ""))
    ]
    status, sha = parse_summary(bodies[-1]) if bodies else (None, None)
    owner, repo = slug.split("/", 1)
    unresolved_ids: list[str] = []
    cursor = ""
    for _page in range(10):  # 1000 threads is far beyond any real PR
        after = f', after:"{cursor}"' if cursor else ""
        q = (
            '{repository(owner:"%s",name:"%s"){pullRequest(number:%d){'
            "reviewThreads(first:100%s){pageInfo{hasNextPage endCursor}"
            "nodes{id isResolved}}}}}" % (owner, repo, number, after)
        )
        threads = _gh_json(["api", "graphql", "-f", f"query={q}"], cwd=cwd)
        if not isinstance(threads, dict):
            return None
        try:
            rt = threads["data"]["repository"]["pullRequest"]["reviewThreads"]
            unresolved_ids += [
                str(n.get("id")) for n in rt["nodes"] if not n.get("isResolved")
            ]
            if not rt["pageInfo"]["hasNextPage"]:
                break
            cursor = rt["pageInfo"]["endCursor"]
        except (KeyError, TypeError):
            return None
    else:
        return None  # more pages than we will read: cannot tell
    return {
        "status": status,
        "reviewed_sha": sha,
        "unresolved": len(unresolved_ids),
        "unresolved_ids": sorted(unresolved_ids),
    }


def needs_nudge(pr: dict, st: dict) -> str | None:
    """One-line reason this PR needs attention, or None if it is settled."""
    head = str(pr.get("headRefOid") or "")
    reviewed = st.get("reviewed_sha") or ""
    on_head = bool(reviewed) and head.startswith(reviewed)
    reasons = []
    if st.get("status") == "running" and on_head:
        reasons.append("Codex is still reviewing this head — wait for it")
    elif st.get("status") != "completed" or not on_head:
        reasons.append(
            "no completed Codex review of the current head"
            + (" (request one: comment '@codex review')" if reviewed else
               " (the PR-opened review may still be starting)")
        )
    if st.get("unresolved"):
        reasons.append(f"{st['unresolved']} unresolved review thread(s)")
    return "; ".join(reasons) if reasons else None


# -- state: one nudge per exact PR state, plus a short result cache ----------


def _state_path(slug: str) -> Path:
    return STATE_DIR / (slug.replace("/", "__") + ".json")


class _StateLock:
    """Serialize load-check-save across concurrent hook processes."""

    def __init__(self, slug: str) -> None:
        self._path = _state_path(slug).with_suffix(".lock")
        self._fh = None

    def __enter__(self) -> "_StateLock":
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            fh = open(self._path, "a")
        except OSError:
            return self  # no lock: worst case one duplicate nudge
        # Never block on the lock: try briefly, then proceed unlocked.
        # A stuck holder must not make this hook overrun its deadline.
        give_up = time.monotonic() + 2.0
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return self
            except OSError:
                if time.monotonic() > give_up:
                    fh.close()
                    return self
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
            except OSError:
                pass


def _load_state(slug: str) -> dict:
    try:
        data = json.loads(_state_path(slug).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(slug: str, data: dict) -> bool:
    """Persist state; False when it could not be written."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _state_path(slug).with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, _state_path(slug))
        return True
    except OSError:
        return False


def state_key(pr: dict, st: dict) -> str:
    head = str(pr.get("headRefOid") or "")  # full SHA: prefixes can collide
    # Thread IDENTITIES, not a count: one resolved plus one new is a new
    # state even though the count is unchanged.
    ids = ",".join(st.get("unresolved_ids") or [])
    return f"{pr.get('number')}@{head}:{st.get('status')}:{ids}"


# -- events --------------------------------------------------------------------


def _emit_context(event: str, text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}))


def handle_post_tool_use(payload: dict) -> int:
    tool_input = payload.get("tool_input") or {}
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str):
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    if _PR_CREATE_RE.search(cmd):
        _emit_context(
            "PostToolUse", "A pull request was just created. " + _LOOP_INSTRUCTIONS
        )
        return 0
    if _GIT_PUSH_RE.search(cmd):
        hits = []
        for branch in pushed_branches(cmd, cwd)[:3]:
            prs = _gh_json(
                ["pr", "list", "--head", branch, "--author", "@me",
                 "--state", "open", "--json", "number,url"],
                cwd=cwd,
            )
            if isinstance(prs, list) and prs:
                hits.append(f"#{prs[0].get('number')}")
        if hits:
            _emit_context(
                "PostToolUse",
                f"You pushed to the branch of open PR {', '.join(hits)}. "
                "Codex reviews are per head: comment '@codex review' on the "
                "PR to get a fresh review of what you just pushed, then "
                "continue the loop.",
            )
    return 0


def handle_stop(payload: dict) -> int:
    if payload.get("stop_hook_active"):
        return 0  # already continuing from a stop hook: never nudge twice
    cwd = payload.get("cwd") or os.getcwd()
    _DEADLINE[:] = [time.monotonic() + _TOTAL_BUDGET]
    root = _repo_root(cwd)
    if not root:
        return 0
    slug = _github_slug(root)
    if not slug:
        return 0
    prs, all_open = open_prs_on_local_branches(root, cwd)
    if not prs:
        return 0
    # Prune against EVERY open PR, so removing a worktree does not erase
    # a PR's once-per-state memory (it would re-nudge on re-checkout).
    open_numbers = {str(p.get("number")) for p in all_open}
    with _StateLock(slug):
        state = _load_state(slug)
        cache = state.get("cache") if isinstance(state.get("cache"), dict) else {}
        nudged = state.get("nudged") if isinstance(state.get("nudged"), dict) else {}
        now = time.time()
        lines = []
        for pr in prs:
            if time.monotonic() > _DEADLINE[0]:
                break  # out of time: report what we have, never overrun
            head = str(pr.get("headRefOid") or "")
            head7 = head[:7]
            ckey = f"{pr.get('number')}@{head}"
            entry = cache.get(ckey)
            if isinstance(entry, dict) and now - float(entry.get("t", 0)) < _CACHE_TTL:
                st = entry.get("st")
            else:
                st = codex_status(slug, int(pr.get("number", 0)), cwd)
                if st is not None:
                    cache[ckey] = {"t": now, "st": st}
            if not isinstance(st, dict):
                continue  # could not tell: stay silent, never guess
            reason = needs_nudge(pr, st)
            if not reason:
                continue
            key = state_key(pr, st)
            if key in nudged:
                continue  # already nudged for exactly this state
            nudged[key] = now
            lines.append(
                f"PR #{pr.get('number')} ({pr.get('url')}, head {head7}): {reason}."
            )
        # Keep only entries for PRs that are still open: a merged or
        # closed PR can never be nudged again, and an open one keeps
        # its once-per-state memory for as long as it stays open.
        nudged = {k: v for k, v in nudged.items() if k.split("@", 1)[0] in open_numbers}
        cache = {k: v for k, v in cache.items() if k.split("@", 1)[0] in open_numbers}
        saved = _save_state(slug, {"cache": cache, "nudged": nudged})
    if not lines:
        return 0
    if not saved:
        # Without the once-per-state memory a nudge would repeat on every
        # stop, which is the one thing this hook must never do: stay
        # silent instead.
        return 0
    msg = (
        "Codex review nudge (one-time for this PR state; stopping again "
        "proceeds):\n" + "\n".join(lines) + "\n" + _LOOP_INSTRUCTIONS
    )
    print(json.dumps({"decision": "block", "reason": msg}))
    return 0


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        if not raw or len(raw) > _MAX_STDIN_BYTES:
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        if event == "PostToolUse":
            return handle_post_tool_use(payload)
        if event == "Stop":
            return handle_stop(payload)
        return 0
    except Exception:
        return 0  # a hook bug must never block or crash the agent


if __name__ == "__main__":
    sys.exit(main())
