"""Subprocess and Unix-socket tests for the Codex app-server helper."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

import claude_code_tools.codex_server as codex_server
import claude_code_tools.codex_server_models as server_models
import claude_code_tools.codex_server_process as codex_server_process
from claude_code_tools.codex_server import (
    CodexServerError,
    OwnedServer,
    ServerPaths,
    _paths,
    _process_group_exists,
    ensure_server,
    get_status,
    stop_server,
)
from claude_code_tools.codex_server_cli import server_cli


FAKE_CODEX = r"""#!__PYTHON__
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
from pathlib import Path


def socket_path(endpoint: str = "unix://") -> Path:
    if endpoint != "unix://":
        return Path(endpoint.removeprefix("unix://"))
    home = Path(os.environ["CODEX_HOME"])
    return home / "app-server-control" / "app-server-control.sock"


def can_connect(path: Path | None = None) -> bool:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    try:
        client.connect(str(path or socket_path()))
    except OSError:
        return False
    finally:
        client.close()
    return True


def run_server(path: Path) -> int:
    if os.environ.get("FAKE_CODEX_FAIL_START"):
        print("intentional startup failure", flush=True)
        return 23
    delay = float(os.environ.get("FAKE_CODEX_START_DELAY", "0"))
    if delay:
        import time

        time.sleep(delay)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_socket():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode):
            print("refusing non-socket path", flush=True)
            return 24
        if can_connect(path):
            print("listener already exists", flush=True)
            return 25
        path.unlink()
    launches = os.environ.get("FAKE_CODEX_LAUNCHES")
    if launches:
        with open(launches, "a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
    child_pid_path = os.environ.get("FAKE_CODEX_CHILD_PID")
    if child_pid_path:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(300)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        Path(child_pid_path).write_text(str(child.pid), encoding="utf-8")
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
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        print(os.environ.get("FAKE_CODEX_VERSION", "codex-cli 9.9.9"))
        return 0
    if arguments == ["app-server", "daemon", "version"]:
        if not can_connect():
            print("app server is not running", file=sys.stderr)
            return 1
        cli_version = os.environ.get("FAKE_CODEX_VERSION", "codex-cli 9.9.9")
        print(json.dumps({
            "status": "running",
            "appServerVersion": cli_version.split()[-1],
        }))
        return 0
    if len(arguments) >= 3 and arguments[-3:-1] == ["app-server", "--listen"]:
        endpoint = arguments[-1]
        if not endpoint.startswith("unix://"):
            return 26
        destination = os.environ.get("FAKE_CODEX_SERVER_ARGS")
        if destination:
            Path(destination).write_text(
                json.dumps(arguments[:-3]),
                encoding="utf-8",
            )
        return run_server(socket_path(endpoint))
    destination = os.environ.get("FAKE_CODEX_ARGS")
    if destination:
        Path(destination).write_text(json.dumps({
            "args": arguments,
            "callbackEndpoint": os.environ.get(
                "CCTOOLS_CODEX_CALLBACK_ENDPOINT"
            ),
            "codexHome": os.environ.get("CODEX_HOME"),
            "stdinIsTty": sys.stdin.isatty(),
            "stdoutIsTty": sys.stdout.isatty(),
        }), encoding="utf-8")
    return int(os.environ.get("FAKE_CODEX_EXIT", "0"))


raise SystemExit(main())
"""


@pytest.fixture
def server_environment() -> Iterator[tuple[Path, dict[str, str]]]:
    """Create a short-path fake Codex installation and isolated home."""
    root = Path(tempfile.mkdtemp(prefix="codex-server-", dir="/tmp"))
    codex = root / "codex"
    script = FAKE_CODEX.replace("__PYTHON__", sys.executable)
    codex.write_text(script, encoding="utf-8")
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
            stop_server(environment, allow_disconnect=True)
        except Exception:
            state_path = _paths(environment).state_path
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    os.kill(int(state["pid"]), signal.SIGKILL)
                except (KeyError, OSError, ValueError, json.JSONDecodeError):
                    pass
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def process_identity_without_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide stable test identities where the outer sandbox denies ``ps``."""

    def process_identity(pid: int) -> str | None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        return f"test-process-{pid}"

    monkeypatch.setattr(codex_server, "_process_identity", process_identity)


def _invoke(
    arguments: list[str],
    environment: dict[str, str],
) -> tuple[int, str]:
    """Invoke the Click lifecycle CLI under an isolated environment."""
    result = CliRunner().invoke(server_cli, arguments, env=environment)
    return result.exit_code, result.output


def _wait_for_socket(path: Path, timeout: float = 5.0) -> None:
    """Wait until a fake app server accepts a connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.1)
        try:
            client.connect(str(path))
        except OSError:
            time.sleep(0.05)
        else:
            client.close()
            return
        finally:
            client.close()
    raise AssertionError(f"socket did not become ready: {path}")


def _wait_for_group_exit(pgid: int, timeout: float = 5.0) -> None:
    """Wait until a process group has no surviving descendants."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_group_exists(pgid):
            return
        time.sleep(0.05)
    raise AssertionError(f"process group {pgid} is still alive")


def test_start_status_restart_and_stop(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Lifecycle commands are idempotent and expose stable JSON status."""
    root, environment = server_environment

    code, output = _invoke(["start", "--json"], environment)
    assert code == 0, output
    started = json.loads(output)
    assert started["status"] == "running"
    assert started["ownership"] == "helper"
    assert started["serverVersion"] == "9.9.9"
    assert Path(started["socketPath"]).is_socket()

    code, output = _invoke(["start", "--json"], environment)
    assert code == 0, output
    repeated = json.loads(output)
    assert repeated["pid"] == started["pid"]

    code, output = _invoke(["status", "--json"], environment)
    assert code == 0, output
    assert json.loads(output)["pid"] == started["pid"]

    code, output = _invoke(["restart", "--json"], environment)
    assert code != 0
    assert "refusing to restart" in output
    assert "disconnects every codex-dynamic TUI" in output

    code, output = _invoke(["status", "--json"], environment)
    assert code == 0, output
    assert json.loads(output)["pid"] == started["pid"]

    code, output = _invoke(["restart", "--force", "--json"], environment)
    assert code == 0, output
    restarted = json.loads(output)
    assert restarted["pid"] != started["pid"]
    launches = (root / "launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 2

    code, output = _invoke(["stop", "--json"], environment)
    assert code != 0
    assert "refusing to stop" in output
    assert "disconnects every codex-dynamic TUI" in output

    code, output = _invoke(["stop", "--force", "--json"], environment)
    assert code == 0, output
    assert json.loads(output)["status"] == "stopped"
    assert not _paths(environment).state_path.exists()

    code, output = _invoke(["stop", "--json"], environment)
    assert code == 0, output
    assert json.loads(output)["status"] == "stopped"


def test_plugin_configuration_change_rolls_to_a_new_generation(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """A new TUI gets fresh plugins without disconnecting the old server."""
    root, environment = server_environment
    started = ensure_server(environment)
    config_path = _paths(environment).codex_home / "config.toml"
    config_path.write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )

    rolled = ensure_server(environment)

    assert rolled.pid != started.pid
    assert rolled.paths.endpoint != started.paths.endpoint
    assert _process_group_exists(started.pid)
    _wait_for_socket(started.paths.socket_path)
    assert get_status(environment).pid == rolled.pid
    launches = (root / "launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 2


def test_failed_rollover_preserves_the_current_generation(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """A failed replacement cannot redirect future clients from a live server."""
    _root, environment = server_environment
    first = ensure_server(environment)
    config_path = first.paths.codex_home / "config.toml"
    config_path.write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )
    environment["FAKE_CODEX_FAIL_START"] = "1"

    with pytest.raises(CodexServerError, match="app-server"):
        ensure_server(environment)

    assert _paths(environment) == first.paths
    assert _process_group_exists(first.pid or 0)
    _wait_for_socket(first.paths.socket_path)


def test_force_stop_cleans_every_retained_generation(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Explicit cleanup stops old and current generations together."""
    _root, environment = server_environment
    first = ensure_server(environment)
    config_path = first.paths.codex_home / "config.toml"
    config_path.write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )
    second = ensure_server(environment)

    stopped = stop_server(environment, allow_disconnect=True)

    assert stopped.status == "stopped"
    assert not _process_group_exists(first.pid or 0)
    assert not _process_group_exists(second.pid or 0)
    assert not first.paths.socket_path.exists()
    assert not second.paths.socket_path.exists()
    assert _paths(environment).generation is None


def test_force_cleanup_releases_generation_capacity(
    server_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped historical directories do not consume the live-server cap."""
    _root, environment = server_environment
    monkeypatch.setattr(server_models, "MAX_SERVER_GENERATIONS", 1)
    first = ensure_server(environment)
    config_path = first.paths.codex_home / "config.toml"
    config_path.write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )
    with pytest.raises(CodexServerError, match="generation limit"):
        ensure_server(environment)

    stop_server(environment, allow_disconnect=True)
    second = ensure_server(environment)

    assert second.paths.generation != first.paths.generation


def test_non_plugin_configuration_change_reuses_server(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """Unrelated configuration edits do not create restart churn."""
    _root, environment = server_environment
    config_path = _paths(environment).codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '[marketplaces.example]\nlast_updated = "first"\n'
        'source_type = "local"\nsource = "/example"\n\n'
        '[tui]\ntheme = "dark"\n',
        encoding="utf-8",
    )
    started = ensure_server(environment)
    config_path.write_text(
        '[marketplaces.example]\nlast_updated = "first"\n'
        'source_type = "local"\nsource = "/example"\n\n'
        '[tui]\ntheme = "light"\n',
        encoding="utf-8",
    )

    reused = ensure_server(environment)

    assert reused.pid == started.pid


def test_server_children_ignore_a_shadow_package_in_the_working_directory(
    server_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older checkout cannot shadow installed supervisor modules."""
    root, environment = server_environment
    shadow_package = root / "shadow" / "claude_code_tools"
    shadow_package.mkdir(parents=True)
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(shadow_package.parent)

    started = ensure_server(environment)

    assert started.status == "running"
    assert started.ownership == "helper"
    stop_server(environment, allow_disconnect=True)
    assert not _paths(environment).state_path.exists()


def test_stop_terminates_the_entire_owned_process_group(
    server_environment: tuple[Path, dict[str, str]],
    process_identity_without_ps: None,
) -> None:
    """Stopping a leader also terminates descendants in its process group."""
    root, environment = server_environment
    child_pid_path = root / "child.pid"
    environment["FAKE_CODEX_CHILD_PID"] = str(child_pid_path)
    ensure_server(environment)
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    state = codex_server._read_state(_paths(environment))

    assert state is not None
    assert state.worker_pgid is not None
    assert os.getpgid(child_pid) == state.worker_pgid
    stop_server(environment, allow_disconnect=True)

    _wait_for_group_exit(state.worker_pgid)
    assert not _paths(environment).state_path.exists()


def test_owned_group_cleanup_escalates_the_verified_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graceful signals target the wrapper; final escalation targets its group."""
    state = OwnedServer(
        pid=12345,
        pgid=12345,
        process_started_at="identity",
        codex_path="/codex",
        codex_version="codex-cli 0.136.0",
        launched_at="2026-01-01T00:00:00+00:00",
        phase="running",
    )
    signals: list[tuple[str, int, signal.Signals]] = []
    waits = iter([False, False, True])
    monkeypatch.setattr(
        codex_server_process,
        "state_controller_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_process,
        "process_matches",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        codex_server_process,
        "wait_for_process_group_exit",
        lambda *_args, **_kwargs: next(waits),
    )
    monkeypatch.setattr(
        codex_server_process,
        "process_group_exists",
        lambda _pgid: False,
    )
    monkeypatch.setattr(
        codex_server.os,
        "kill",
        lambda pid, sent: signals.append(("leader", pid, sent)),
    )
    monkeypatch.setattr(
        codex_server.os,
        "killpg",
        lambda pgid, sent: signals.append(("group", pgid, sent)),
    )

    codex_server._terminate_owned(state)

    assert signals == [
        ("leader", state.pid, signal.SIGTERM),
        ("leader", state.pid, signal.SIGTERM),
        ("group", state.pgid, signal.SIGKILL),
    ]


def test_owned_group_cleanup_retains_state_if_leader_exits_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leader-exit race cannot discard ownership of live descendants."""
    state = OwnedServer(
        pid=12345,
        pgid=12345,
        process_started_at="identity",
        codex_path="/codex",
        codex_version="codex-cli 0.128.0",
        launched_at="2026-01-01T00:00:00+00:00",
        phase="running",
    )
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        codex_server_process,
        "state_controller_matches",
        lambda _state: False,
    )
    monkeypatch.setattr(
        codex_server_process,
        "process_group_exists",
        lambda _pgid: True,
    )
    monkeypatch.setattr(
        codex_server.os,
        "killpg",
        lambda pgid, sent: signals.append((pgid, sent)),
    )

    with pytest.raises(CodexServerError, match="ownership state was retained"):
        codex_server._terminate_owned(state)

    assert signals == []


def test_stop_retains_state_when_group_cleanup_fails(
    server_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    process_identity_without_ps: None,
) -> None:
    """Ownership remains durable until every owned process has exited."""
    _root, environment = server_environment
    started = ensure_server(environment)
    original = codex_server._terminate_owned

    def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise CodexServerError("group still alive")

    monkeypatch.setattr(codex_server, "_terminate_owned", fail_cleanup)
    with pytest.raises(CodexServerError, match="group still alive"):
        stop_server(environment, allow_disconnect=True)
    assert _paths(environment).state_path.is_file()
    assert _process_group_exists(started.pid or 0)

    monkeypatch.setattr(codex_server, "_terminate_owned", original)
    stop_server(environment, allow_disconnect=True)


def test_stop_retains_state_for_descendants_after_leader_exit(
    server_environment: tuple[Path, dict[str, str]],
) -> None:
    """A surviving group is not forgotten when its recorded leader is gone."""
    _root, environment = server_environment
    paths = _paths(environment)
    paths.runtime_dir.mkdir(mode=0o700, parents=True)
    state = OwnedServer(
        pid=999_999_999,
        pgid=os.getpgrp(),
        process_started_at="missing leader",
        codex_path=environment["CCTOOLS_CODEX_BIN"],
        codex_version="codex-cli 9.9.9",
        launched_at="2026-01-01T00:00:00+00:00",
        phase="running",
    )
    codex_server._write_state(paths, state)

    with pytest.raises(CodexServerError, match="still have descendants"):
        stop_server(environment, allow_disconnect=True)
    assert paths.state_path.is_file()


@pytest.mark.parametrize("failure", [OSError("disk full"), KeyboardInterrupt()])
def test_post_spawn_failures_clean_up_the_new_process_group(
    server_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    process_identity_without_ps: None,
    failure: BaseException,
) -> None:
    """State-write failures and interruptions cannot orphan a new server."""
    root, environment = server_environment
    writes = 0
    original = codex_server._write_state
    original_wait = codex_server_process.wait_for_process_identity
    launches = root / "launches.txt"
    supervisor_pids: list[int] = []

    def wait_for_identity(
        process: subprocess.Popen[bytes],
        timeout: float = 2.0,
    ) -> str | None:
        supervisor_pids.append(process.pid)
        return original_wait(process, timeout)

    def fail_state_write(paths: ServerPaths, state: OwnedServer) -> None:
        nonlocal writes
        writes += 1
        if isinstance(failure, OSError) or writes == 2:
            raise failure
        original(paths, state)

    monkeypatch.setattr(codex_server, "_write_state", fail_state_write)
    monkeypatch.setattr(
        codex_server_process,
        "write_state",
        fail_state_write,
    )
    monkeypatch.setattr(
        codex_server_process,
        "wait_for_process_identity",
        wait_for_identity,
    )
    expected = CodexServerError if isinstance(failure, OSError) else KeyboardInterrupt
    with pytest.raises(expected):
        ensure_server(environment)

    _wait_for_group_exit(supervisor_pids[0])
    if launches.exists():
        pid = int(launches.read_text(encoding="utf-8").splitlines()[0])
        _wait_for_group_exit(pid)
    assert not _paths(environment).state_path.exists()


def test_immediate_post_spawn_interruption_cleans_up_the_process_group(
    server_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    process_identity_without_ps: None,
) -> None:
    """An interruption before identity persistence cannot orphan the server."""
    root, environment = server_environment
    launches = root / "launches.txt"
    supervisor_pids: list[int] = []

    def interrupt_after_launch(
        process: subprocess.Popen[bytes],
        timeout: float = 2.0,
    ) -> str | None:
        del timeout
        supervisor_pids.append(process.pid)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        codex_server_process,
        "wait_for_process_identity",
        interrupt_after_launch,
    )
    with pytest.raises(KeyboardInterrupt):
        ensure_server(environment)

    _wait_for_group_exit(supervisor_pids[0])
    assert not launches.exists()
    assert not _paths(environment).state_path.exists()
