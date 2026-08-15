"""Scan live tmux panes for running coding agents.

Performance notes, measured on a 160-pane server where the original shell
implementation took ~8.5s:

* One ``ps`` snapshot replaces ~160 ``pgrep``+``ps`` forks (was ~5.6s).
* ``capture-pane`` runs only for panes that actually host an agent, and in a
  thread pool rather than serially (was ~2.9s for all panes).

Together these turn a multi-second scan into a fraction of a second, which is
what makes the picker feel instant even before the cache layer.
"""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import detect
from .model import Agent

#: Field separator for tmux -F output; chosen to not occur in paths or titles.
_SEP = "\x1f"

#: Explicit record terminator. tmux permits newlines in pane titles and in
#: paths, so line-based parsing drops those panes entirely. Emitting an ASCII
#: RS and splitting on it keeps records intact WITHOUT altering the data --
#: tmux's own "#{s/\n/ /:...}" substitution is not the answer here: its
#: pattern is a literal string, so it rewrites every letter "n"
#: (/Users/pchalasani/Git/avon -> "/Users/pchalasa i/Git/avo ").
_REC = "\x1e"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


def _tmux(*args: str) -> str:
    """Run a tmux command, returning stdout (empty string on failure)."""
    try:
        out = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _children_by_ppid() -> dict[int, list[tuple[int, str]]]:
    """Map each PID to its direct children as ``(pid, command_line)`` pairs.

    One ``ps`` call for the whole process table; a fork per pane dominated the
    original runtime. Keeping the child PIDs here (rather than a second
    ``pgrep``) also means agent-PID selection sees full command lines --
    ``pgrep -l`` reports only the executable name, which is ``node`` for Codex
    and therefore unidentifiable.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "ppid=,pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}

    children: dict[int, list[tuple[int, str]]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid, pid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append((pid, parts[2]))
    return children


def descendants(
    tree: dict[int, list[tuple[int, str]]], root: int, limit: int = 200
) -> list[tuple[int, str]]:
    """Every process beneath *root*, breadth-first.

    Direct children are not enough: a pane whose shell launches a wrapper
    (``direnv exec``, a login shell, a `just` recipe) which then launches the
    agent would otherwise show only the wrapper, and the agent would be
    invisible. The whole process table is already snapshotted, so walking it
    costs nothing extra.

    Args:
        tree: Map of ppid to ``(pid, command)`` pairs.
        root: PID whose descendants are wanted.
        limit: Safety bound on nodes visited, in case of a cycle.
    """
    out: list[tuple[int, str]] = []
    queue = list(tree.get(root, []))
    seen: set[int] = set()
    while queue and len(out) < limit:
        pid, cmd = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, cmd))
        queue.extend(tree.get(pid, []))
    return out


def argv_of(children: list[tuple[int, str]]) -> str:
    """Joined command lines, for harness classification."""
    return "\n".join(cmd for _, cmd in children)


def agent_pid(children: list[tuple[int, str]], kind: str) -> int:
    """PID of the process matching *kind*, else the first, else 0.

    Matching on kind matters: a pane running ``sleep 600 &`` alongside an
    agent would otherwise report the sleep's PID.
    """
    for pid, cmd in children:
        if detect.classify_argv(cmd) == kind:
            return pid
    return children[0][0] if children else 0


def capture(pane: str, lines: int = 40) -> str:
    """Return a pane's recent screen text with escape sequences stripped."""
    raw = _tmux("capture-pane", "-p", "-t", pane)
    text = _ANSI.sub("", raw)
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _git_context(cwd: str) -> tuple[str, str]:
    """Return ``(repo, branch)`` for a directory, without forking git.

    Reads ``.git/HEAD`` directly by walking up from *cwd*; a subprocess per
    pane would reintroduce the cost this module exists to avoid.
    """
    path = Path(cwd) if cwd else None
    while path and path != path.parent:
        git = path / ".git"
        head = git / "HEAD" if git.is_dir() else None
        if git.is_file():  # worktree: ".git" is a pointer file
            try:
                target = git.read_text().strip().removeprefix("gitdir: ")
                # Worktree pointers may be relative, and are relative to the
                # directory holding .git -- not to amux's cwd.
                head = (path / target).resolve() / "HEAD"
            except OSError:
                head = None
        if head and head.exists():
            try:
                ref = head.read_text().strip()
            except OSError:
                return path.name, ""
            branch = ref.removeprefix("ref: refs/heads/") if "ref:" in ref else ""
            return path.name, branch
        path = path.parent
    return "", ""


def scan(workers: int = 16) -> list[Agent]:
    """Find every tmux pane currently running a Claude or Codex agent.

    Args:
        workers: Thread-pool size for the ``capture-pane`` calls.

    Returns:
        Agents sorted by urgency (waiting-on-you first), then by pane.
    """
    fmt = _SEP.join(
        [
            "#{session_name}:#{window_index}.#{pane_index}",
            "#{session_name}",
            "#{pane_pid}",
            "#{pane_title}",
            "#{pane_current_path}",
        ]
    )
    listing = _tmux("list-panes", "-a", "-F", fmt + _REC)
    if not listing:
        return []

    child_map = _children_by_ppid()
    candidates: list[tuple[str, str, int, str, str, str]] = []
    for record in listing.split(_REC):
        # tmux terminates each -F line with a newline AFTER our record marker,
        # so exactly one leading newline is separator; anything else is data
        # (a path may legitimately end in a newline).
        record = record[1:] if record.startswith("\n") else record
        if not record:
            continue
        parts = record.split(_SEP)
        if len(parts) != 5:
            continue
        pane, session, pid_s, title, cwd = parts
        try:
            ppid = int(pid_s)
        except ValueError:
            continue
        children = descendants(child_map, ppid)
        kind = detect.classify_argv(argv_of(children))
        if kind is None:
            continue
        candidates.append((pane, session, ppid, title, cwd, kind))

    if not candidates:
        return []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        screens = list(pool.map(lambda c: capture(c[0]), candidates))

    agents: list[Agent] = []
    for (pane, session, ppid, title, cwd, kind), screen in zip(candidates, screens):
        # A pane that closed between list-panes and capture-pane yields "".
        # Listing it would offer a jump target that no longer exists.
        if not screen.strip():
            continue
        repo, branch = _git_context(cwd)
        agents.append(
            Agent(
                pane=pane,
                session=session,
                kind=kind,  # type: ignore[arg-type]
                state=detect.detect_state(screen, kind),  # type: ignore[arg-type]
                name=detect.extract_name(
                    argv_of(descendants(child_map, ppid)), title, screen
                ),
                cwd=cwd,
                repo=repo,
                branch=branch,
                model=detect.extract_model(screen, kind),  # type: ignore[arg-type]
                info=detect.extract_info(screen, kind),  # type: ignore[arg-type]
                pid=agent_pid(descendants(child_map, ppid), kind),
            )
        )

    agents.sort(key=lambda a: (a.rank, a.pane))
    return agents


def tmux_available() -> bool:
    """Whether a tmux server is reachable."""
    return bool(_tmux("list-sessions"))


def switch_to(pane: str) -> None:
    """Focus *pane*: select its window and pane, then switch/attach the client."""
    window = pane.rsplit(".", 1)[0]
    session = pane.split(":", 1)[0]
    _tmux("select-window", "-t", window)
    _tmux("select-pane", "-t", pane)
    if os.environ.get("TMUX"):
        _tmux("switch-client", "-t", session)
    else:
        os.execvp("tmux", ["tmux", "attach", "-t", session])
