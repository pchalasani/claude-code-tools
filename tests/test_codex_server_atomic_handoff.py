"""Real-process regressions for atomic app-server worker publication."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from claude_code_tools.codex_server_models import (
    CodexServerError,
    OwnedServer,
    ServerPaths,
    paths_from_env,
    prepare_runtime,
    read_state,
)
from claude_code_tools.codex_server_process import (
    process_group_exists,
    process_identity,
    process_matches,
    spawn_supervisor,
)


FAKE_CODEX = r"""#!__PYTHON__
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path


if sys.argv[1:] == ["--version"]:
    print("codex-cli 9.9.9")
    raise SystemExit(0)

if sys.argv[1:] != ["app-server", "--listen", "unix://"]:
    raise SystemExit(2)

Path(os.environ["FAKE_CODEX_STARTED"]).write_text(
    str(os.getpid()),
    encoding="utf-8",
)
stopping = False


def stop(_signum: int, _frame: object) -> None:
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while not stopping:
    time.sleep(0.05)
"""


def _write_fake_codex(path: Path) -> None:
    """Write an executable that records only an actual app-server exec."""
    path.write_text(
        FAKE_CODEX.replace("__PYTHON__", sys.executable),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    """Wait for a callable condition or fail with a bounded timeout."""
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition did not become true")


def _kill_if_matching(pid: int, pgid: int, identity: str) -> None:
    """Clean a test group only while its exact leader still matches."""
    if not process_matches(pid, pgid, identity):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _read_worker_observation(path: Path) -> tuple[int, int, str]:
    """Decode the instrumented worker identity with explicit type checks."""
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    pid: object = value.get("pid")
    pgid: object = value.get("pgid")
    identity: object = value.get("identity")
    assert isinstance(pid, int)
    assert isinstance(pgid, int)
    assert isinstance(identity, str)
    return pid, pgid, identity


def test_sigkill_before_worker_publication_never_executes_codex(
    tmp_path: Path,
) -> None:
    """Supervisor death before state fsync closes the pre-exec gate."""
    codex = tmp_path / "codex"
    started = tmp_path / "codex-started"
    observed = tmp_path / "worker-observed.json"
    home = tmp_path / "home"
    _write_fake_codex(codex)
    environment = dict(os.environ)
    environment.update(
        {
            "CODEX_HOME": str(home),
            "FAKE_CODEX_STARTED": str(started),
        }
    )
    token = "atomic-publication-test"
    harness = textwrap.dedent(
        f"""
        import datetime as dt
        import json
        import os
        import time
        from pathlib import Path

        import claude_code_tools.codex_server_supervisor as supervisor
        from claude_code_tools.codex_server_models import (
            OwnedServer,
            paths_from_env,
            prepare_runtime,
            write_state,
        )
        from claude_code_tools.codex_server_process import process_identity

        paths = paths_from_env(os.environ)
        prepare_runtime(paths)
        launch_token = {token!r}
        identity = process_identity(os.getpid())
        assert identity is not None
        initial = OwnedServer(
            pid=os.getpid(),
            pgid=os.getpgrp(),
            process_started_at=identity,
            codex_path={str(codex)!r},
            codex_version="codex-cli 9.9.9",
            launched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            phase="starting",
            launch_token=launch_token,
        )
        write_state(paths, initial)
        read_fd, write_fd = os.pipe()

        def block_publication(_paths, state):
            Path({str(observed)!r}).write_text(
                json.dumps({{
                    "pid": state.worker_pid,
                    "pgid": state.worker_pgid,
                    "identity": state.worker_started_at,
                }}),
                encoding="utf-8",
            )
            while True:
                time.sleep(300)

        supervisor.write_state = block_publication
        os.write(write_fd, f"{{launch_token}}\\n".encode("ascii"))
        os.close(write_fd)
        supervisor.supervise({str(codex)!r}, read_fd, os.environ)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", harness],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    worker_pid: int | None = None
    worker_pgid: int | None = None
    worker_identity: str | None = None
    try:
        _wait_until(observed.exists)
        worker_pid, worker_pgid, worker_identity = _read_worker_observation(
            observed,
        )
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5.0)
        _wait_until(lambda: process_identity(worker_pid) is None)
        _wait_until(lambda: not process_group_exists(worker_pgid))

        assert process.returncode == -signal.SIGKILL
        assert not started.exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)
        if (
            worker_pid is not None
            and worker_pgid is not None
            and worker_identity is not None
        ):
            _kill_if_matching(
                worker_pid,
                worker_pgid,
                worker_identity,
            )


def test_launcher_cleanup_rereads_published_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale launcher snapshot cannot discard a live published worker."""
    import claude_code_tools.codex_server_process as server_process

    codex = tmp_path / "codex"
    started = tmp_path / "codex-started"
    _write_fake_codex(codex)
    environment = dict(os.environ)
    environment.update(
        {
            "CODEX_HOME": str(tmp_path / "home"),
            "FAKE_CODEX_STARTED": str(started),
        }
    )
    paths = paths_from_env(environment)
    prepare_runtime(paths)
    published: list[OwnedServer] = []

    def fail_after_worker_exec(
        state_paths: ServerPaths,
        _initial: OwnedServer,
        supervisor: subprocess.Popen[bytes],
        timeout: float = 5.0,
    ) -> OwnedServer:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = read_state(state_paths)
            if current is not None and current.worker_pid is not None:
                published.append(current)
                break
            if supervisor.poll() is not None:
                raise AssertionError("supervisor exited before publication")
            time.sleep(0.05)
        else:
            raise AssertionError("worker identity was not published")
        _wait_until(started.exists)
        os.kill(supervisor.pid, signal.SIGKILL)
        supervisor.wait(timeout=5.0)
        raise CodexServerError("injected launcher failure")

    monkeypatch.setattr(
        server_process,
        "_wait_for_worker_state",
        fail_after_worker_exec,
    )
    try:
        with pytest.raises(CodexServerError, match="injected launcher failure"):
            spawn_supervisor(
                str(codex),
                "codex-cli 9.9.9",
                environment,
                paths,
            )

        assert published
        state = published[0]
        assert state.worker_pid is not None
        assert state.worker_pgid is not None
        _wait_until(lambda: process_identity(state.worker_pid or 0) is None)
        _wait_until(
            lambda: not process_group_exists(state.worker_pgid or 0),
        )
        assert read_state(paths) is None
    finally:
        if published:
            state = published[0]
            if (
                state.worker_pid is not None
                and state.worker_pgid is not None
                and state.worker_started_at is not None
            ):
                _kill_if_matching(
                    state.worker_pid,
                    state.worker_pgid,
                    state.worker_started_at,
                )
