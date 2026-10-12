"""Supervisor that ties the llama-server's life to voxtype's.

voxtype runs ``python -m voxtype._post_supervisor <server command...>``
as a process-group leader with a pipe on its stdin, and never writes to
it. The supervisor starts the server in its own group and waits. When
the pipe reaches EOF — voxtype exited for ANY reason, including SIGKILL,
a crash, or a closed terminal, none of which run voxtype's cleanup — the
supervisor kills the whole group (server, and any wrapper children), so
no ~800 MB server outlives the app. A normal stop signals the group
directly (``postprocess_server._terminate``).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

# Set while the supervisor kills its own group: the SIGTERM it sends
# itself must not end it before the SIGKILL escalation.
_dying = threading.Event()


def _kill_group(server: subprocess.Popen[bytes]) -> None:
    """SIGTERM the group, then SIGKILL it if the server lingers.

    The final SIGKILL also ends this process.
    """
    _dying.set()
    group = os.getpgid(0)
    try:
        os.killpg(group, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and server.poll() is None:
        time.sleep(0.05)
    try:
        os.killpg(group, signal.SIGKILL)
    except OSError:
        pass


def _reap_leftovers() -> None:
    """After the server process exits, end anything it left in the group.

    A ``post_process_server`` wrapper may background llama-server and
    return (or crash without stopping it); the supervisor is about to
    exit, so nothing else would ever stop such a child. SIGTERM the
    group (this process ignores it while ``_dying``), then SIGKILL any
    remaining member except this process.
    """
    _dying.set()
    group = os.getpgid(0)
    me = os.getpid()
    try:
        os.killpg(group, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        others = _other_members(group, me)
        if not others:
            return
        time.sleep(0.05)
    for pid in _other_members(group, me):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _other_members(group: int, me: int) -> list[int]:
    """Live (non-zombie) processes in ``group`` other than ``me``."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if (
            len(parts) == 3
            and parts[1] == str(group)
            and parts[0] != str(me)
            and not parts[2].startswith("Z")
        ):
            pids.append(int(parts[0]))
    return pids


def _on_sigterm(_sig: int, _frame: object) -> None:
    if _dying.is_set():
        return  # our own group kill: stay alive to escalate
    # A normal stop from voxtype (it signals the whole group and
    # escalates itself).
    raise SystemExit(128 + signal.SIGTERM)


def main() -> int:
    """Run the server command; exit with its status."""
    if len(sys.argv) < 2:
        print("usage: python -m voxtype._post_supervisor CMD...", file=sys.stderr)
        return 2
    # Terminal signals belong to voxtype, which stops us deliberately.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _on_sigterm)
    server = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)

    def watch_parent() -> None:
        # Blocks until voxtype's end of the pipe closes.
        try:
            sys.stdin.buffer.read()
        except OSError:
            pass
        _kill_group(server)

    threading.Thread(target=watch_parent, daemon=True).start()
    code = server.wait()
    _reap_leftovers()
    return code


if __name__ == "__main__":
    sys.exit(main())
