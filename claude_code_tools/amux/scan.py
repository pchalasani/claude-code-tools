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


def _child_argv_by_ppid() -> dict[int, str]:
    """Map each PID to the joined command lines of its direct children.

    One ``ps`` call for the whole process table; the alternative is a fork per
    pane, which dominated the old runtime.
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

    children: dict[int, list[str]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid = int(parts[0])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(parts[2])
    return {ppid: "\n".join(cmds) for ppid, cmds in children.items()}


def _child_pid(ppid: int, kind: str) -> int:
    """PID of the agent process under a pane's shell.

    Matches on *kind* rather than taking the first child: a pane running
    ``sleep 600 &`` alongside an agent would otherwise report the sleep's PID.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(ppid), "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    fallback = 0
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        fallback = fallback or pid
        if detect.classify_argv(parts[1]) == kind:
            return pid
    return fallback


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
            # tmux permits newlines in titles and paths, which would split one
            # -F record across lines and make the pane vanish. Strip them at
            # the source with tmux's own substitution.
            "#{s/\n/ /:pane_title}",
            "#{s/\n/ /:pane_current_path}",
        ]
    )
    listing = _tmux("list-panes", "-a", "-F", fmt)
    if not listing:
        return []

    argv_map = _child_argv_by_ppid()
    candidates: list[tuple[str, str, int, str, str, str]] = []
    for line in listing.splitlines():
        parts = line.split(_SEP)
        if len(parts) != 5:
            continue
        pane, session, pid_s, title, cwd = parts
        try:
            ppid = int(pid_s)
        except ValueError:
            continue
        argv = argv_map.get(ppid, "")
        kind = detect.classify_argv(argv)
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
                name=detect.extract_name(argv_map.get(ppid, ""), title, screen),
                cwd=cwd,
                repo=repo,
                branch=branch,
                model=detect.extract_model(screen, kind),  # type: ignore[arg-type]
                info=detect.extract_info(screen, kind),  # type: ignore[arg-type]
                pid=_child_pid(ppid, kind),
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
