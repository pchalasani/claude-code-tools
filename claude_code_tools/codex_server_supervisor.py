"""Stable process owner for a detached Codex app-server process group."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from types import FrameType
from typing import Mapping, Sequence

from claude_code_tools.codex_server_models import (
    FORCED_STOP_SECONDS,
    GRACEFUL_STOP_SECONDS,
    POLL_SECONDS,
    CodexServerError,
    OwnedServer,
    ServerPaths,
    paths_from_env,
    read_state,
    write_state,
)
from claude_code_tools.codex_server_process import (
    process_group_exists,
    process_identity,
    process_matches,
)


@dataclass
class SignalRequest:
    """Record lifecycle signals without doing unsafe work in a handler."""

    first: signal.Signals | None = None
    count: int = 0

    def capture(self, signum: int, _frame: FrameType | None) -> None:
        """Record the first signal and total number received."""
        self.count += 1
        if self.first is None:
            self.first = signal.Signals(signum)


def supervise(
    codex_path: str,
    handoff_fd: int,
    env: Mapping[str, str],
) -> int:
    """Own one Codex worker until it and all descendants have exited.

    Args:
        codex_path: Exact executable selected by the launcher.
        handoff_fd: Pipe containing the launch token after state is durable.
        env: Complete worker environment.

    Returns:
        A conventional process exit status.

    Raises:
        CodexServerError: If ownership cannot be proven or published.
    """
    paths = paths_from_env(env)
    requested = SignalRequest()
    _install_handlers(requested)
    token = _read_handoff(handoff_fd)
    initial = _require_handoff_state(paths, token, codex_path)
    worker, release_fd = _spawn_worker(codex_path, token, env)
    worker_identity: str | None = None
    try:
        worker_identity = _wait_for_identity(worker)
        if worker_identity is None:
            raise CodexServerError("Codex app-server worker exited during startup")
        worker_pgid = os.getpgid(worker.pid)
        if worker_pgid != worker.pid:
            raise CodexServerError(
                "Codex app-server worker did not get a private process group"
            )
        current = _require_handoff_state(paths, token, codex_path)
        if current.pid != initial.pid:
            raise CodexServerError("app-server ownership changed during handoff")
        write_state(
            paths,
            replace(
                current,
                worker_pid=worker.pid,
                worker_pgid=worker_pgid,
                worker_started_at=worker_identity,
            ),
        )
        _release_worker(release_fd, token)
        release_fd = -1
        return _monitor(worker, worker_pgid, requested)
    except BaseException:
        if release_fd >= 0:
            _close_fd(release_fd)
            release_fd = -1
        _contain_worker(worker, worker_identity)
        raise
    finally:
        if release_fd >= 0:
            _close_fd(release_fd)


def _install_handlers(requested: SignalRequest) -> None:
    """Install minimal handlers before waiting for the state handoff."""
    for sent in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sent, requested.capture)


def _read_handoff(fd: int) -> str:
    """Read a bounded one-line launch token and close the inherited pipe."""
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            raw = stream.readline(256)
    except OSError as exc:
        raise CodexServerError(f"cannot read app-server handoff: {exc}") from exc
    if not raw.endswith(b"\n") or len(raw) > 200:
        raise CodexServerError("app-server ownership handoff was incomplete")
    try:
        token = raw[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise CodexServerError("app-server ownership token was invalid") from exc
    if not token:
        raise CodexServerError("app-server ownership token was empty")
    return token


def _require_handoff_state(
    paths: ServerPaths,
    token: str,
    codex_path: str,
) -> OwnedServer:
    """Require state that names this exact supervisor and launch."""
    state = read_state(paths)
    if state is None or state.launch_token != token:
        raise CodexServerError("app-server ownership handoff does not match state")
    if state.pid != os.getpid() or state.pgid != os.getpgrp():
        raise CodexServerError("app-server ownership state names another process")
    if state.codex_path != codex_path:
        raise CodexServerError("app-server ownership state names another Codex")
    if not process_matches(
        state.pid,
        state.pgid,
        state.process_started_at,
    ):
        raise CodexServerError("app-server supervisor identity cannot be verified")
    return state


def _spawn_worker(
    codex_path: str,
    launch_token: str,
    env: Mapping[str, str],
) -> tuple[subprocess.Popen[bytes], int]:
    """Spawn a gated worker in an independently containable group."""
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    try:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "claude_code_tools.codex_server_worker",
                "--gate-fd",
                str(read_fd),
                "--launch-token",
                launch_token,
                "--codex",
                codex_path,
            ],
            stdin=subprocess.DEVNULL,
            env=dict(env),
            start_new_session=True,
            close_fds=True,
            pass_fds=(read_fd,),
        )
    except OSError as exc:
        os.close(write_fd)
        raise CodexServerError(f"cannot start Codex app server: {exc}") from exc
    finally:
        os.close(read_fd)
    return worker, write_fd


def _release_worker(fd: int, launch_token: str) -> None:
    """Release a worker only after its identity is durably published."""
    try:
        os.write(fd, f"{launch_token}\n".encode("ascii"))
        os.close(fd)
    except OSError as exc:
        _close_fd(fd)
        raise CodexServerError(
            f"cannot release Codex app-server worker: {exc}"
        ) from exc


def _close_fd(fd: int) -> None:
    """Close a handoff descriptor without masking lifecycle cleanup."""
    try:
        os.close(fd)
    except OSError:
        pass


def _wait_for_identity(
    worker: subprocess.Popen[bytes],
    timeout: float = 2.0,
) -> str | None:
    """Wait briefly for the operating system to expose a worker identity."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            return None
        identity = process_identity(worker.pid)
        if identity is not None:
            return identity
        time.sleep(POLL_SECONDS)
    return None


def _monitor(
    worker: subprocess.Popen[bytes],
    worker_pgid: int,
    requested: SignalRequest,
) -> int:
    """Wait for the worker, cleaning its group on exit or a signal."""
    forwarded = 0
    first_forwarded_at: float | None = None
    while True:
        returncode = worker.poll()
        if returncode is not None:
            if process_group_exists(worker_pgid):
                _stop_direct_group(worker, worker_pgid)
            if requested.first is not None:
                return 0
            return _shell_status(returncode)

        now = time.monotonic()
        if requested.count > forwarded:
            _signal_direct_group(worker_pgid, signal.SIGTERM)
            forwarded = requested.count
            if first_forwarded_at is None:
                first_forwarded_at = now
        if first_forwarded_at is not None:
            elapsed = now - first_forwarded_at
            if elapsed >= GRACEFUL_STOP_SECONDS and forwarded < 2:
                _signal_direct_group(worker_pgid, signal.SIGTERM)
                forwarded = 2
            if elapsed >= GRACEFUL_STOP_SECONDS + FORCED_STOP_SECONDS:
                _signal_direct_group(worker_pgid, signal.SIGKILL)
        time.sleep(POLL_SECONDS)


def _contain_worker(
    worker: subprocess.Popen[bytes],
    identity: str | None,
) -> None:
    """Clean a directly spawned worker after supervisor setup fails."""
    if identity is not None and not process_matches(
        worker.pid,
        worker.pid,
        identity,
    ):
        if worker.poll() is None:
            raise CodexServerError("worker identity changed before startup cleanup")
    if process_group_exists(worker.pid):
        _stop_direct_group(worker, worker.pid)
    else:
        worker.poll()


def _stop_direct_group(
    worker: subprocess.Popen[bytes],
    pgid: int,
) -> None:
    """Stop a group that this supervisor directly spawned and continuously owns."""
    _signal_direct_group(pgid, signal.SIGTERM)
    if _wait_group(worker, pgid, 2.0):
        return
    _signal_direct_group(pgid, signal.SIGTERM)
    if _wait_group(worker, pgid, 1.0):
        return
    _signal_direct_group(pgid, signal.SIGKILL)
    if not _wait_group(worker, pgid, 2.0):
        raise CodexServerError(
            f"Codex app-server worker group {pgid} could not be contained"
        )


def _wait_group(
    worker: subprocess.Popen[bytes],
    pgid: int,
    timeout: float,
) -> bool:
    """Wait for a direct child and its complete process group to exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker.poll()
        if not process_group_exists(pgid):
            return True
        time.sleep(POLL_SECONDS)
    worker.poll()
    return not process_group_exists(pgid)


def _signal_direct_group(pgid: int, sent: signal.Signals) -> None:
    """Signal a process group continuously owned by this supervisor."""
    try:
        os.killpg(pgid, sent)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise CodexServerError(
            f"cannot signal Codex app-server worker group {pgid}: {exc}"
        ) from exc


def _shell_status(returncode: int) -> int:
    """Convert a Popen return code to a portable shell status."""
    if returncode < 0:
        return min(255, 128 - returncode)
    return min(255, returncode)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the internal app-server supervisor."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--handoff-fd", required=True, type=int)
    parser.add_argument("--codex", required=True)
    options = parser.parse_args(arguments)
    try:
        return supervise(options.codex, options.handoff_fd, os.environ)
    except CodexServerError as exc:
        print(f"codex-server supervisor: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
