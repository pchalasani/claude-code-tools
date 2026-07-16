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

import claude_code_tools.codex_server as codex_server
import claude_code_tools.codex_server_fingerprint as fingerprinting
from claude_code_tools.codex_server import (
    CodexServerError,
    _plugin_configuration_snapshot,
    _paths,
    _read_state,
    ensure_server,
    get_status,
    restart_server,
    stop_server,
)
from claude_code_tools.codex_server_cli import server_cli
from claude_code_tools.codex_server_models import (
    LOG_MAX_BYTES,
    LOG_TAIL_MAX_BYTES,
    STATE_MAX_BYTES,
    StateFileError,
    log_tail,
    read_state,
    trim_oversized_log,
)
from claude_code_tools.codex_server_process import (
    DIAGNOSTIC_OUTPUT_MAX_BYTES,
    process_group_exists,
    process_identity,
    process_matches,
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


def emit_noise(total: int) -> None:
    chunk = b"n" * 65_536
    remaining = total
    while remaining > 0:
        data = chunk[:remaining]
        os.write(1, data)
        os.write(2, data)
        remaining -= len(data)


def run_server() -> int:
    emit_noise(int(os.environ.get("FAKE_CODEX_STARTUP_NOISE", "0")))
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
    noise_process: subprocess.Popen[bytes] | None = None
    if os.environ.get("FAKE_CODEX_ACTIVE_NOISE"):
        noise_program = (
            "import os, time; chunk=b'z'*65536; "
            "delay=float(os.environ.get('FAKE_CODEX_NOISE_DELAY', '0')); "
            "exec(\"while True:\\n os.write(1, chunk); os.write(2, chunk); "
            "time.sleep(delay)\")"
        )
        noise_process = subprocess.Popen(
            [sys.executable, "-c", noise_program],
            stdin=subprocess.DEVNULL,
        )
    try:
        while not stopping:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            connection.close()
    finally:
        if noise_process is not None and noise_process.poll() is None:
            noise_process.terminate()
            noise_process.wait(timeout=5.0)
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


class _RecordingDigest:
    """Collect tree-hash inputs for traversal assertions."""

    def __init__(self) -> None:
        self.data = bytearray()

    def update(self, data: bytes, /) -> None:
        """Record one digest update."""
        self.data.extend(data)


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
            stop_server(environment, allow_disconnect=True)
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
    expected_prefix = "darwin:" if sys.platform == "darwin" else "linux:"
    assert state.process_started_at.startswith(expected_prefix)


def test_legacy_second_resolution_identity_is_never_authenticated() -> None:
    """Pre-upgrade ownership cannot authorize signals to a reused PID."""
    assert not process_matches(os.getpid(), os.getpgrp(), "Tue Jul 14 20:00:00 2026")


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
        timeout=5.0,
    )

    assert result is None
    _wait_until(marker.exists, timeout=1.0)
    leader, child = json.loads(marker.read_text(encoding="utf-8"))
    _wait_until(lambda: not process_group_exists(leader))
    _wait_until(lambda: process_identity(child) is None)


def test_diagnostic_output_is_drained_but_retained_with_a_fixed_cap() -> None:
    """A noisy probe cannot make diagnostics consume unbounded memory."""
    program = (
        "import os; "
        "[(os.write(1, b'x' * 65536), os.write(2, b'y' * 65536)) "
        "for _ in range(64)]"
    )

    result = run_diagnostic(
        [sys.executable, "-c", program],
        os.environ,
        timeout=5.0,
    )

    assert result is not None
    assert result.returncode == 0
    assert len(result.stdout.encode()) == DIAGNOSTIC_OUTPUT_MAX_BYTES
    assert len(result.stderr.encode()) == DIAGNOSTIC_OUTPUT_MAX_BYTES


def test_noisy_version_and_health_diagnostics_retain_final_results(
    tmp_path: Path,
) -> None:
    """Bounded drains retain final protocol results after noisy preambles."""
    program = tmp_path / "noisy-codex"
    program.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import sys

            noise = b"n" * ({DIAGNOSTIC_OUTPUT_MAX_BYTES} + 1024)
            os.write(1, noise)
            os.write(2, noise)
            if sys.argv[1:] == ["--version"]:
                print("\\ncodex-cli 9.9.9")
            else:
                print("\\n" + json.dumps({{"appServerVersion": "9.9.9"}}))
            """
        ),
        encoding="utf-8",
    )
    program.chmod(0o755)
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})

    version = codex_server._codex_version(str(program), os.environ)
    probe = codex_server._probe_server(str(program), os.environ, paths)

    assert version is not None
    assert version == "codex-cli 9.9.9"
    assert probe.running
    assert probe.server_version == "9.9.9"


def test_plugin_snapshot_detects_resolved_artifact_replacement(
    tmp_path: Path,
) -> None:
    """Same-ID plugin upgrades change the persisted server fingerprint."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    paths.codex_home.joinpath("config.toml").write_text(
        '[plugins."sample@example"]\nenabled = true\n',
        encoding="utf-8",
    )
    artifact = paths.codex_home / "plugins/cache/example/sample/plugin.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old plugin\n", encoding="utf-8")
    previous = artifact.stat()
    before = _plugin_configuration_snapshot(paths)

    replacement = artifact.with_name("plugin.replacement")
    replacement.write_text("new plugin\n", encoding="utf-8")
    os.utime(
        replacement,
        ns=(previous.st_atime_ns, previous.st_mtime_ns),
    )
    os.replace(replacement, artifact)
    after = _plugin_configuration_snapshot(paths)

    assert after.fingerprint != before.fingerprint


def test_plugin_snapshot_tracks_marketplace_refresh_revision(
    tmp_path: Path,
) -> None:
    """Marketplace refresh metadata participates in plugin certification."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    config.write_text(
        '[marketplaces.example]\nlast_updated = "first"\n',
        encoding="utf-8",
    )
    before = _plugin_configuration_snapshot(paths)
    config.write_text(
        '[marketplaces.example]\nlast_updated = "second"\n',
        encoding="utf-8",
    )

    assert _plugin_configuration_snapshot(paths).fingerprint != before.fingerprint


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversize", "integer", "deep"])
def test_plugin_configuration_resource_attacks_are_controlled(
    tmp_path: Path,
    kind: str,
) -> None:
    """Unsafe config inputs fail promptly with a user-facing server error."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    config = paths.codex_home / "config.toml"
    if kind == "symlink":
        target = tmp_path / "target.toml"
        target.write_text("", encoding="utf-8")
        config.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(config)
    elif kind == "oversize":
        config.write_bytes(b"#" * (1024 * 1024 + 1))
    elif kind == "integer":
        config.write_text("value = " + "9" * 10_000, encoding="utf-8")
    else:
        config.write_text(
            "value = " + "[" * 200 + "0" + "]" * 200,
            encoding="utf-8",
        )

    if kind == "fifo":
        program = textwrap.dedent(
            f"""
            from pathlib import Path
            from claude_code_tools.codex_server import (
                CodexServerError,
                _plugin_configuration_snapshot,
                _paths,
            )

            paths = _paths({{"CODEX_HOME": {str(paths.codex_home)!r}}})
            try:
                _plugin_configuration_snapshot(paths)
            except CodexServerError:
                raise SystemExit(0)
            raise SystemExit(1)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=3.0,
        )
        assert result.returncode == 0, result.stderr
        return

    started_at = time.monotonic()
    with pytest.raises(CodexServerError):
        _plugin_configuration_snapshot(paths)
    assert time.monotonic() - started_at < 1.0


def test_plugin_tree_scan_stays_on_open_directory_during_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing an opened cache path with a symlink cannot redirect a scan."""
    root = tmp_path / "plugins"
    moved = tmp_path / "original-plugins"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    root.joinpath("inside.txt").write_text("inside", encoding="utf-8")
    outside.joinpath("outside.txt").write_text("outside", encoding="utf-8")
    original_scandir = os.scandir
    swapped = False

    def swapping_scandir(path: int | str | bytes | os.PathLike[str]) -> object:
        nonlocal swapped
        if not swapped:
            swapped = True
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)
        return original_scandir(path)

    monkeypatch.setattr(codex_server.os, "scandir", swapping_scandir)
    digest = _RecordingDigest()
    with pytest.raises(CodexServerError, match="changed while being read"):
        codex_server._hash_plugin_tree(root, Path("plugins"), digest)

    assert swapped
    assert b"inside.txt" in digest.data
    assert b"outside.txt" not in digest.data


def test_plugin_tree_accepts_non_utf8_names(tmp_path: Path) -> None:
    """Surrogate-escaped artifact names produce a controlled fingerprint."""
    if os.name != "posix":
        pytest.skip("byte-oriented filenames require POSIX")
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.codex_home.mkdir(parents=True)
    plugins = paths.codex_home / "plugins"
    plugins.mkdir()
    bad_name = os.fsencode(plugins) + b"/plugin-\xff"
    try:
        os.mkdir(bad_name)
    except OSError as exc:
        pytest.skip(f"filesystem rejects non-UTF-8 names: {exc}")

    snapshot = _plugin_configuration_snapshot(paths)

    assert len(snapshot.fingerprint) == 64


def test_plugin_tree_entry_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact enumeration stops immediately after its cardinality limit."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    plugins = paths.codex_home / "plugins"
    plugins.mkdir(parents=True)
    for index in range(3):
        plugins.joinpath(str(index)).touch()
    monkeypatch.setattr(fingerprinting, "PLUGIN_TREE_MAX_ENTRIES", 2)

    with pytest.raises(CodexServerError, match="too many entries"):
        _plugin_configuration_snapshot(paths)


@pytest.mark.parametrize("kind", ["oversize", "integer", "deep"])
def test_ownership_state_resource_attacks_are_controlled(
    tmp_path: Path,
    kind: str,
) -> None:
    """State parsing has bounded bytes and controlled parser failures."""
    paths = _paths({"CODEX_HOME": str(tmp_path / "home")})
    paths.runtime_dir.mkdir(parents=True)
    if kind == "oversize":
        data = b" " * (STATE_MAX_BYTES + 1)
    elif kind == "integer":
        data = ("{" + '"version":' + "9" * 10_000 + "}").encode()
    else:
        data = ("[" * 2_000 + "0" + "]" * 2_000).encode()
    paths.state_path.write_bytes(data)

    with pytest.raises(StateFileError):
        read_state(paths)


def test_log_tail_and_retention_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Huge final lines and active logs have fixed read and disk ceilings."""
    path = tmp_path / "app-server.log"
    with path.open("wb") as stream:
        stream.write(b"old\n")
        stream.seek(LOG_MAX_BYTES)
        stream.write(b"x")
    requests: list[tuple[int, int]] = []
    original_pread = os.pread

    def recording_pread(fd: int, count: int, offset: int) -> bytes:
        requests.append((count, offset))
        return original_pread(fd, count, offset)

    monkeypatch.setattr(codex_server.os, "pread", recording_pread)

    assert log_tail(path, 0) == ""
    tail = log_tail(path, 10**9)
    assert len(tail.encode()) <= LOG_TAIL_MAX_BYTES
    assert sum(count for count, _offset in requests) <= LOG_TAIL_MAX_BYTES
    assert min(offset for _count, offset in requests) >= (
        path.stat().st_size - LOG_TAIL_MAX_BYTES
    )

    trim_oversized_log(path)
    assert path.stat().st_size < 1024
    assert "truncated" in path.read_text(encoding="utf-8")


def test_noisy_startup_drains_without_deadlock(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Output beyond pipe capacity cannot block readiness publication."""
    _root, environment = hardened_environment
    environment["FAKE_CODEX_STARTUP_NOISE"] = str(2 * 1024 * 1024)

    status = ensure_server(environment)

    assert status.status == "running"
    assert _paths(environment).log_path.stat().st_size <= LOG_MAX_BYTES


def test_active_log_is_hard_bounded_on_its_verified_inode(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Streaming noise never exceeds the cap or redirects after a path swap."""
    root, environment = hardened_environment
    environment["FAKE_CODEX_ACTIVE_NOISE"] = "1"
    environment["FAKE_CODEX_NOISE_DELAY"] = "0.002"
    status = ensure_server(environment)
    paths = _paths(environment)
    moved = root / "active-app-server.log"

    _wait_until(lambda: paths.log_path.stat().st_size > 1024 * 1024)
    paths.log_path.rename(moved)
    paths.log_path.write_bytes(b"replacement must remain untouched\n")
    previous = moved.stat().st_size
    saw_reset = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        size = moved.stat().st_size
        assert size <= LOG_MAX_BYTES
        if size < previous:
            saw_reset = True
            break
        previous = size
        time.sleep(0.005)

    assert saw_reset
    assert paths.log_path.read_bytes() == b"replacement must remain untouched\n"
    assert get_status(environment).pid == status.pid


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


def test_helper_with_unknown_server_version_is_not_certified(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """A helper cannot succeed without a final certified server version."""
    root, environment = hardened_environment
    environment["FAKE_CODEX_HIDE_SERVER_VERSION"] = "1"

    with pytest.raises(CodexServerError, match="version.*uncertified"):
        ensure_server(environment)

    assert len((root / "launches.txt").read_text().splitlines()) == 1
    assert get_status(environment).status == "stopped"


def test_helper_requires_explicit_restart_after_cli_changes(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """CLI changes never silently disconnect attached remote TUIs."""
    root, environment = hardened_environment
    first = ensure_server(environment)
    environment["FAKE_CODEX_VERSION"] = "codex-cli 9.9.10"
    with pytest.raises(CodexServerError, match="disconnect every"):
        ensure_server(environment)
    assert get_status(environment).pid == first.pid
    second = restart_server(environment, allow_disconnect=True)
    assert second.pid != first.pid
    assert second.server_version == "9.9.10"

    replacement = root / "replacement-codex"
    shutil.copy2(environment["CCTOOLS_CODEX_BIN"], replacement)
    replacement.chmod(0o755)
    environment["CCTOOLS_CODEX_BIN"] = str(replacement)
    with pytest.raises(CodexServerError, match="disconnect every"):
        ensure_server(environment)
    assert get_status(environment).pid == second.pid
    third = restart_server(environment, allow_disconnect=True)

    assert third.pid != second.pid
    assert third.codex_path == str(replacement.resolve())
    assert len((root / "launches.txt").read_text().splitlines()) == 3


def test_same_path_executable_replacement_is_not_certified(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """An inode replacement cannot inherit the old executable identity."""
    root, environment = hardened_environment
    first = ensure_server(environment)
    codex = Path(environment["CCTOOLS_CODEX_BIN"])
    incoming = root / "incoming-codex"
    shutil.copy2(codex, incoming)
    incoming.chmod(0o755)
    os.replace(incoming, codex)

    with pytest.raises(CodexServerError, match="executable was replaced"):
        ensure_server(environment)

    assert get_status(environment).pid == first.pid


def test_external_server_without_plugin_certification_is_rejected(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """Even version-matched listeners cannot prove their plugin snapshot."""
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
        with pytest.raises(CodexServerError, match="plugin snapshot"):
            ensure_server(environment)
        assert external.poll() is None

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


def test_accepting_uncertified_socket_is_refused_without_spawning(
    hardened_environment: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic protocol failure cannot launch over an accepting socket."""
    _root, environment = hardened_environment
    paths = _paths(environment)
    paths.socket_path.parent.mkdir(parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(paths.socket_path))
    listener.listen()
    spawned = False

    def spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal spawned
        spawned = True

    diagnostic = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="temporary protocol failure",
    )
    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: "/codex")
    monkeypatch.setattr(
        codex_server,
        "_codex_executable_identity",
        lambda _path: "verified executable",
    )
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: "codex-cli 9.9.9",
    )
    monkeypatch.setattr(codex_server, "_run_command", lambda *_args: diagnostic)
    monkeypatch.setattr(codex_server, "spawn_supervisor", spawn)
    try:
        with pytest.raises(CodexServerError, match="version could not be verified"):
            ensure_server(environment)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(paths.socket_path))
        finally:
            client.close()
        assert not spawned
    finally:
        listener.close()


def test_replacement_listener_cannot_inherit_helper_certification(
    hardened_environment: tuple[Path, dict[str, str]],
) -> None:
    """A live supervisor cannot certify a socket rebound by another process."""
    _root, environment = hardened_environment
    first = ensure_server(environment)
    paths = _paths(environment)
    paths.socket_path.unlink()
    replacement = subprocess.Popen(
        [environment["CCTOOLS_CODEX_BIN"], "app-server", "--listen", "unix://"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_socket(paths.socket_path)
        replacement_status = get_status(environment)
        assert replacement_status.ownership == "external"
        with pytest.raises(CodexServerError, match="not owned"):
            ensure_server(environment)
        assert replacement.poll() is None
        assert process_identity(first.pid or 0) is not None
    finally:
        os.killpg(replacement.pid, signal.SIGTERM)
        replacement.wait(timeout=5)


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
    result = subprocess.run(
        [sys.executable, "-m", "claude_code_tools.codex_server_cli", "start"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=5.0,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "app-server log" in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("input_name", "kind"),
    [
        ("configuration", "fifo"),
        ("configuration", "symlink"),
        ("state", "fifo"),
        ("state", "symlink"),
    ],
)
def test_hostile_config_and_state_are_prompt_diagnostic_failures(
    hardened_environment: tuple[Path, dict[str, str]],
    input_name: str,
    kind: str,
) -> None:
    """Hostile parsed files fail without following, blocking, or traceback."""
    root, environment = hardened_environment
    paths = _paths(environment)
    if input_name == "configuration":
        paths.codex_home.mkdir(parents=True, exist_ok=True)
        hostile = paths.codex_home / "config.toml"
    else:
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        hostile = paths.state_path
    if kind == "fifo":
        os.mkfifo(hostile)
    else:
        target = root / f"{input_name}-target"
        target.write_text("{}", encoding="utf-8")
        hostile.symlink_to(target)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "claude_code_tools.codex_server_cli", "start"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=5.0,
        )
    finally:
        hostile.unlink()

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert input_name in output.lower()
    assert "Traceback" not in output
