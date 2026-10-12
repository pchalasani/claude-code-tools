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


def _kill_group() -> None:
    """SIGTERM this process group, then SIGKILL it (ends this process)."""
    group = os.getpgid(0)
    try:
        os.killpg(group, signal.SIGTERM)
    except OSError:
        return
    time.sleep(3)
    try:
        os.killpg(group, signal.SIGKILL)
    except OSError:
        pass


def _watch_parent() -> None:
    """Block until voxtype's end of the pipe closes, then kill the group."""
    try:
        sys.stdin.buffer.read()
    except OSError:
        pass
    _kill_group()


def main() -> int:
    """Run the server command; exit with its status."""
    if len(sys.argv) < 2:
        print("usage: python -m voxtype._post_supervisor CMD...", file=sys.stderr)
        return 2
    # Terminal signals belong to voxtype, which stops us deliberately;
    # SIGTERM (from the group kill) ends us by default.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    server = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    threading.Thread(target=_watch_parent, daemon=True).start()
    return server.wait()


if __name__ == "__main__":
    sys.exit(main())
