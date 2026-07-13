"""Real-process regressions for Codex app-server lifecycle hardening."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.codex_server import (
    CodexServerError,
    _paths,
    _read_state,
    ensure_server,
    stop_server,
)
from claude_code_tools.codex_server_cli import server_cli
from claude_code_tools.codex_server_process import (
    process_group_exists,
    process_identity,
    run_diagnostic,
)


FAKE_CODEX = r"""#!__PYTHON__
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path


def control_path(name: str) -> Path:
    return Path(os.environ["CODEX_HOME"]) / "app-server-control" / name


def can_connect() -> bool:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    try:
        client.connect(str(control_path("app-server-control.sock")))
    except OSError:
        return False
    finally:
        client.close()
    return True


def core_version() -> str:
    value = os.environ.get("FAKE_CODEX_VERSION", "codex-cli 9.9.9")
    return value.split()[-1]


def run_server() -> int:
    path = control_path("app-server-control.sock")
    version_path = control_path("server-version")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_socket():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or can_connect():
            return 24
        path.unlink()
    launches = os.environ.get("FAKE_CODEX_LAUNCHES")
    if launches:
        with open(launches, "a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
    version_path.write_text(core_version(), encoding="utf-8")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    listener.settimeout(0.1)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            connection.close()
    finally:
        listener.close()
        path.unlink(missing_ok=True)
        version_path.unlink(missing_ok=True)
    return 0


def run_wrapper() -> int:
    child = subprocess.Popen(
        [sys.executable, __file__, "_native-app-server"],
        stdin=subprocess.DEVNULL,
    )
    return child.wait()


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        print(os.environ.get("FAKE_CODEX_VERSION", "codex-cli 9.9.9"))
        return 0
    if arguments == ["app-server", "daemon", "version"]:
        if not can_connect():
            return 1
        value = {"status": "running"}
        if not os.environ.get("FAKE_CODEX_HIDE_SERVER_VERSION"):
            value["appServerVersion"] = control_path("server-version").read_text()
        print(json.dumps(value))
        return 0
    if arguments == ["app-server", "--listen", "unix://"]:
        if os.environ.get("FAKE_CODEX_NODE_WRAPPER"):
            return run_wrapper()
        return run_server()
    if arguments == ["_native-app-server"]:
        return run_server()
    return 0


raise SystemExit(main())
"""


@pytest.fixture
def hardened_environment() -> Iterator[tuple[Path, dict[str, str]]]:
    """Create a fake Codex with persistent server-side version reporting."""
    root = Path(tempfile.mkdtemp(prefix="codex-hardening-", dir="/tmp"))
    codex = root / "codex"
    codex.write_text(FAKE_CODEX.replace("__PYTHON__", sys.executable), encoding="utf-8")
    codex.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        {
            "CODEX_HOME": str(root / "home"),
            "CCTOOLS_CODEX_BIN": str(codex),
            "FAKE_CODEX_LAUNCHES": str(root / "launches.txt"),
        }
    )
    try:
        yield root, environment
    finally:
        try:
            stop_server(environment)
        except Exception:
            _emergency_cleanup(environment)
        shutil.rmtree(root, ignore_errors=True)


def _emergency_cleanup(environment: dict[str, str]) -> None:
    """Clean only process groups recorded by an isolated test home."""
    try:
        state = _read_state(_paths(environment))
    except CodexServerError:
        return
    if state is None:
        return
    for pgid in (state.worker_pgid, state.pgid):
        if pgid is None:
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    """Wait for a zero-argument predicate to become true."""
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition did not become true")


def _wait_for_socket(path: Path) -> None:
    """Wait for a Unix listener to accept a connection."""

    def accepts() -> bool:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.1)
        try:
            client.connect(str(path))
        except OSError:
            return False
        finally:
            client.close()
        return True

    _wait_until(accepts)


def test_supervisor_publishes_separate_worker_ownership(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Durable state identifies both the supervisor and worker groups."""
    _root, environment = hardened_environment
    status = ensure_server(environment)
    state = _read_state(_paths(environment))

    assert state is not None
    assert state.launch_token
    assert state.phase == "running"
    assert state.pid == state.pgid == status.pid
    assert state.worker_pid == state.worker_pgid
    assert state.worker_pid != state.pid
    assert process_identity(state.pid) == state.process_started_at
    assert process_identity(state.worker_pid or 0) == state.worker_started_at


def test_supervisor_contains_native_child_after_wrapper_sigkill(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Killing an npm-like wrapper cannot strand its native server child."""
    root, environment = hardened_environment
    environment["FAKE_CODEX_NODE_WRAPPER"] = "1"
    first = ensure_server(environment)
    state = _read_state(_paths(environment))
    assert state is not None and state.worker_pid is not None
    assert state.worker_pgid is not None
    native_pid = int((root / "launches.txt").read_text(encoding="utf-8"))
    assert native_pid != state.worker_pid
    assert os.getpgid(native_pid) == state.worker_pgid

    os.kill(state.worker_pid, signal.SIGKILL)
    _wait_until(lambda: not process_group_exists(state.worker_pgid or 0))
    _wait_until(lambda: process_identity(state.pid) is None)
    _wait_until(lambda: not _paths(environment).socket_path.exists())

    recovered = ensure_server(environment)
    assert recovered.pid != first.pid
    assert len((root / "launches.txt").read_text().splitlines()) == 2


def test_timed_out_diagnostic_kills_its_complete_process_group(tmp_path: Path) -> None:
    """A timed-out version probe cannot leave descendants behind."""
    marker = tmp_path / "processes.json"
    program = (
        "import json, os, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(300)']); "
        f"open({str(marker)!r}, 'w').write(json.dumps([os.getpid(), child.pid])); "
        "time.sleep(300)"
    )

    result = run_diagnostic(
        [sys.executable, "-c", program],
        os.environ,
        timeout=0.25,
    )

    assert result is None
    leader, child = json.loads(marker.read_text(encoding="utf-8"))
    _wait_until(lambda: not process_group_exists(leader))
    _wait_until(lambda: process_identity(child) is None)


def test_signal_before_first_state_fsync_is_deferred_and_cleaned(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Termination during publication cleans the supervisor before replay."""
    root, environment = hardened_environment
    marker = root / "handoff.json"
    program = textwrap.dedent(
        f"""
        import json
        import time
        from pathlib import Path
        import claude_code_tools.codex_server_process as p
        from claude_code_tools.codex_server import ensure_server

        original = p.write_state
        marker = Path({str(marker)!r})

        def slow(paths, state):
            marker.write_text(json.dumps({{'pid': state.pid, 'pgid': state.pgid}}))
            time.sleep(1.0)
            original(paths, state)

        p.write_state = slow
        ensure_server()
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_until(marker.exists)
    ownership = json.loads(marker.read_text(encoding="utf-8"))

    os.kill(process.pid, signal.SIGTERM)
    _stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == -signal.SIGTERM, stderr
    _wait_until(lambda: not process_group_exists(ownership["pgid"]))
    assert not _paths(environment).state_path.exists()
    assert not _paths(environment).socket_path.exists()


@pytest.mark.parametrize(
    ("version", "allowed"),
    [
        ("codex-cli 0.136.0-alpha.1", False),
        ("codex-cli 0.136.0-rc.2+build.7", False),
        ("codex-cli 0.136.0", True),
        ("codex-cli 0.136.0+build.7", True),
        ("codex-cli 0.137.0-alpha.1", True),
    ],
)
def test_callback_version_floor_obeys_semver_prereleases(
    hardened_environment: tuple[Path, dict[str, str]],
    version: str,
    allowed: bool,
) -> None:
    """Only prereleases of the exact minimum core version remain too old."""
    root, environment = hardened_environment
    environment["FAKE_CODEX_VERSION"] = version
    if allowed:
        assert ensure_server(environment).status == "running"
        return
    with pytest.raises(CodexServerError, match=r"0\.136\.0 or newer"):
        ensure_server(environment)
    assert not (root / "launches.txt").exists()


def test_matching_helper_with_unknown_server_version_is_reused(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Durable helper identity avoids restart churn if probing omits a version."""
    root, environment = hardened_environment
    environment["FAKE_CODEX_HIDE_SERVER_VERSION"] = "1"

    first = ensure_server(environment)
    second = ensure_server(environment)

    assert second.pid == first.pid
    assert len((root / "launches.txt").read_text().splitlines()) == 1


def test_helper_restarts_after_cli_version_or_path_changes(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Helper listeners track both the selected executable and its version."""
    root, environment = hardened_environment
    first = ensure_server(environment)
    environment["FAKE_CODEX_VERSION"] = "codex-cli 9.9.10"
    second = ensure_server(environment)
    assert second.pid != first.pid
    assert second.server_version == "9.9.10"

    replacement = root / "replacement-codex"
    shutil.copy2(environment["CCTOOLS_CODEX_BIN"], replacement)
    replacement.chmod(0o755)
    environment["CCTOOLS_CODEX_BIN"] = str(replacement)
    third = ensure_server(environment)

    assert third.pid != second.pid
    assert third.codex_path == str(replacement.resolve())
    assert len((root / "launches.txt").read_text().splitlines()) == 3


def test_mismatched_or_unknown_external_server_is_rejected(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """External listeners must prove the same version as the selected CLI."""
    _root, environment = hardened_environment
    codex = environment["CCTOOLS_CODEX_BIN"]
    external = subprocess.Popen(
        [codex, "app-server", "--listen", "unix://"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_socket(_paths(environment).socket_path)
        changed = dict(environment)
        changed["FAKE_CODEX_VERSION"] = "codex-cli 9.9.10"
        with pytest.raises(CodexServerError, match="does not match"):
            ensure_server(changed)
        assert external.poll() is None

        unknown = dict(environment)
        unknown["FAKE_CODEX_HIDE_SERVER_VERSION"] = "1"
        with pytest.raises(CodexServerError, match="could not be verified"):
            ensure_server(unknown)
        assert external.poll() is None
    finally:
        os.killpg(external.pid, signal.SIGTERM)
        external.wait(timeout=5)


def test_invalid_home_and_fifo_log_fail_without_traceback_or_blocking(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Unsafe filesystem inputs become prompt, controlled Click failures."""
    root, environment = hardened_environment
    bad_home = root / "not-a-directory"
    bad_home.write_text("x", encoding="utf-8")
    invalid = dict(environment)
    invalid["CODEX_HOME"] = str(bad_home)

    for command in (["status"], ["start"]):
        result = CliRunner().invoke(server_cli, command, env=invalid)
        assert result.exit_code != 0
        assert "not a directory" in result.output
        assert "Traceback" not in result.output

    paths = _paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    os.mkfifo(paths.log_path, mode=0o600)
    started_at = time.monotonic()
    result = CliRunner().invoke(server_cli, ["start"], env=environment)

    assert time.monotonic() - started_at < 2.0
    assert result.exit_code != 0
    assert "app-server log" in result.output
    assert "Traceback" not in result.output
