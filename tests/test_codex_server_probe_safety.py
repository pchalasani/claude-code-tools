"""Regression tests for non-destructive helper health probes."""

from __future__ import annotations

import errno
import json
import struct
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

import claude_code_tools.codex_server as codex_server
import claude_code_tools.codex_server_retry as codex_server_retry
from claude_code_tools.codex_server import (
    CodexServerError,
    OwnedServer,
    ServerProbe,
    ensure_server,
)
from claude_code_tools.codex_server_models import StateFileError


Diagnostic = subprocess.CompletedProcess[str] | None


class _PeerSocket:
    """Return one deterministic Darwin peer-credential outcome."""

    def __init__(self, credentials: bytes | OSError) -> None:
        self._credentials = credentials

    def settimeout(self, _timeout: float) -> None:
        """Accept the production timeout configuration."""

    def connect(self, _path: str) -> None:
        """Model a successful connection before credential sampling."""

    def getsockopt(self, *_args: object) -> bytes:
        """Return or raise the configured credential result."""
        if isinstance(self._credentials, OSError):
            raise self._credentials
        return self._credentials

    def close(self) -> None:
        """Accept production cleanup."""


def _install_peer_sockets(
    monkeypatch: pytest.MonkeyPatch,
    credentials: Iterator[bytes | OSError],
) -> list[_PeerSocket]:
    """Install deterministic Darwin peer sockets and return those opened."""
    clients: list[_PeerSocket] = []

    def socket_factory(*_args: object) -> _PeerSocket:
        client = _PeerSocket(next(credentials))
        clients.append(client)
        return client

    monkeypatch.setattr(codex_server.sys, "platform", "darwin")
    monkeypatch.setattr(codex_server.socket, "socket", socket_factory)
    return clients


def test_transient_darwin_peer_lookup_retries_stable_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENOTCONN is indeterminate and needs a new authenticated sample."""
    peer_pid = 42_424
    clients = _install_peer_sockets(
        monkeypatch,
        iter(
            [
                OSError(errno.ENOTCONN, "not connected"),
                struct.pack("i", peer_pid),
            ]
        ),
    )
    monkeypatch.setattr(
        codex_server,
        "_socket_identity",
        lambda _path: (7, 11),
    )

    assert codex_server._socket_peer_pid(Path("/stable.sock")) == peer_pid
    assert len(clients) == 2


def test_peer_lookup_retry_rejects_replaced_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An indeterminate sample cannot bridge a socket-identity replacement."""
    clients = _install_peer_sockets(
        monkeypatch,
        iter(
            [
                OSError(errno.ENOTCONN, "not connected"),
                struct.pack("i", 42_424),
            ]
        ),
    )
    identities = iter([(7, 11), (7, 11), (7, 12)])
    monkeypatch.setattr(
        codex_server,
        "_socket_identity",
        lambda _path: next(identities),
    )

    assert codex_server._socket_peer_pid(Path("/replaced.sock")) is None
    assert len(clients) == 1


def test_concrete_peer_pid_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concrete foreign peer remains available for immediate rejection."""
    foreign_pid = 31_313
    clients = _install_peer_sockets(
        monkeypatch,
        iter([struct.pack("i", foreign_pid), struct.pack("i", 42_424)]),
    )
    monkeypatch.setattr(
        codex_server,
        "_socket_identity",
        lambda _path: (7, 11),
    )

    assert codex_server._socket_peer_pid(Path("/foreign.sock")) == foreign_pid
    assert len(clients) == 1


def _live_state(codex_version: str = "codex-cli 9.9.9") -> OwnedServer:
    """Return stable helper ownership for an isolated unit test."""
    return OwnedServer(
        pid=12_345,
        pgid=12_345,
        process_started_at="verified supervisor",
        codex_path="/fake/codex",
        codex_version=codex_version,
        launched_at="2026-07-13T00:00:00+00:00",
        phase="running",
        launch_token="test launch",
        plugin_fingerprint="plugin snapshot",
        codex_executable_identity="verified executable",
        worker_pid=12_346,
        worker_pgid=12_346,
        worker_started_at="verified worker",
    )


def _forbid_mutation(*_args: object, **_kwargs: object) -> NoReturn:
    """Fail if a probe-only reuse path mutates server ownership."""
    raise AssertionError("live helper ownership was mutated")


def _snapshot(
    fingerprint: str = "plugin snapshot",
    generation: str = "stable generation",
) -> codex_server._PluginSnapshot:
    """Return a deterministic plugin snapshot for lifecycle tests."""
    return codex_server._PluginSnapshot(fingerprint, generation)


def _arrange_live_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    diagnostics: Callable[[], Diagnostic],
) -> tuple[dict[str, str], list[float], list[Sequence[str]]]:
    """Install deterministic helper ownership and diagnostic responses."""
    state = _live_state()
    sleeps: list[float] = []
    commands: list[Sequence[str]] = []

    def probe(*_args: object) -> ServerProbe:
        commands.append(("socket-probe",))
        diagnostic = diagnostics()
        if diagnostic is None or diagnostic.returncode != 0:
            return ServerProbe(running=False)
        return ServerProbe(
            running=True,
            server_version=codex_server._server_version_from_output(diagnostic.stdout),
            method="socket",
            accepting=True,
        )

    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: state.codex_path)
    monkeypatch.setattr(
        codex_server,
        "_codex_executable_identity",
        lambda _path: "verified executable",
    )
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: state.codex_version,
    )
    monkeypatch.setattr(codex_server, "_read_state", lambda _paths: state)
    monkeypatch.setattr(
        codex_server,
        "state_controller_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "state_worker_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: True,
    )
    monkeypatch.setattr(codex_server, "_probe_server", probe)
    monkeypatch.setattr(
        codex_server,
        "_plugin_configuration_snapshot",
        lambda _paths, _options=(): _snapshot(),
    )
    monkeypatch.setattr(codex_server.time, "sleep", sleeps.append)
    for name in (
        "_terminate_owned",
        "_remove_state",
        "_remove_stale_ownership",
        "_quarantine_invalid_state",
        "spawn_supervisor",
    ):
        monkeypatch.setattr(codex_server, name, _forbid_mutation)

    environment = {"CODEX_HOME": str(tmp_path / "home")}
    return environment, sleeps, commands


def _success() -> subprocess.CompletedProcess[str]:
    """Return a successful app-server protocol diagnostic."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"appServerVersion": "9.9.9"}),
        stderr="",
    )


def _nonzero() -> subprocess.CompletedProcess[str]:
    """Return a transient nonzero app-server protocol diagnostic."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="temporary probe failure",
    )


def test_stop_preserves_invalid_state_for_accepting_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invalid ownership cannot be discarded while any listener accepts."""
    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: "/codex")
    monkeypatch.setattr(
        codex_server,
        "_read_state",
        lambda _paths: (_ for _ in ()).throw(StateFileError("invalid state")),
    )
    monkeypatch.setattr(
        codex_server,
        "_probe_server",
        lambda *_args: ServerProbe(running=False, accepting=True),
    )
    monkeypatch.setattr(
        codex_server,
        "_quarantine_invalid_state",
        _forbid_mutation,
    )

    with pytest.raises(CodexServerError, match="ownership state is invalid"):
        codex_server.stop_server({"CODEX_HOME": str(tmp_path / "home")})


def test_stop_rejects_replacement_accepting_listener(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A degraded replacement listener prevents a false stopped result."""
    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: "/codex")
    monkeypatch.setattr(codex_server, "_read_state", lambda _paths: None)
    monkeypatch.setattr(
        codex_server,
        "_probe_server",
        lambda *_args: ServerProbe(running=False, accepting=True),
    )

    with pytest.raises(CodexServerError, match="refusing to stop"):
        codex_server.stop_server({"CODEX_HOME": str(tmp_path / "home")})


@pytest.mark.parametrize("first", [None, _nonzero()])
def test_transient_probe_failure_retries_without_restarting_live_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first: Diagnostic,
) -> None:
    """A timeout or nonzero diagnostic gets a bounded reuse retry."""
    results = iter([first, _success(), _success()])
    environment, sleeps, commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        lambda: next(results),
    )

    status = ensure_server(environment)

    assert status.status == "running"
    assert status.ownership == "helper"
    assert status.pid == 12_345
    assert len(commands) == 3
    assert sleeps == [codex_server.POLL_SECONDS]


def test_sustained_probe_failure_preserves_ownership_but_fails_certification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exhausted probes cannot return a vanished listener as helper success."""
    environment, sleeps, commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _nonzero,
    )

    with pytest.raises(CodexServerError, match="listener vanished"):
        ensure_server(environment)

    assert len(commands) == codex_server.REUSE_PROBE_ATTEMPTS + 1
    assert sleeps == [
        codex_server.POLL_SECONDS,
        codex_server.POLL_SECONDS * 2,
    ]


def test_plugin_change_during_reuse_probe_is_revalidated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reuse retries and certifies against the replacement snapshot."""
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _success,
    )
    snapshots = iter(
        [
            _snapshot(),
            _snapshot(),
            _snapshot(generation="changed during probe"),
            _snapshot(generation="changed during probe"),
            _snapshot(generation="changed during probe"),
            _snapshot(generation="changed during probe"),
        ]
    )
    observed: list[str] = []

    def snapshot(
        _paths: object,
        _options: object = (),
    ) -> codex_server._PluginSnapshot:
        value = next(snapshots)
        observed.append(value.generation)
        return value

    monkeypatch.setattr(
        codex_server,
        "_plugin_configuration_snapshot",
        snapshot,
    )

    status = ensure_server(environment)

    assert status.status == "running"
    assert observed == [
        "stable generation",
        "stable generation",
        "changed during probe",
        "changed during probe",
        "changed during probe",
        "changed during probe",
    ]


def test_listener_replacement_during_final_snapshot_is_not_certified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The final config recheck cannot create a listener ownership ABA gap."""
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _success,
    )
    snapshots = 0
    replaced = False
    listener_checks = 0

    def snapshot(
        _paths: object,
        _options: object = (),
    ) -> codex_server._PluginSnapshot:
        nonlocal replaced, snapshots
        snapshots += 1
        if snapshots == 3:
            replaced = True
        return _snapshot()

    def listener_matches(_state: OwnedServer, _paths: object) -> bool:
        nonlocal listener_checks
        listener_checks += 1
        return not replaced

    monkeypatch.setattr(codex_server, "_plugin_configuration_snapshot", snapshot)
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        listener_matches,
    )

    with pytest.raises(CodexServerError, match="changed during app-server reuse"):
        ensure_server(environment)

    assert snapshots == 3
    assert listener_checks >= 3


def test_final_reuse_revalidates_selected_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A replaced selected executable cannot inherit reuse certification."""
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _success,
    )
    paths = iter(["/fake/codex", "/replacement/codex"])
    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: next(paths))

    with pytest.raises(CodexServerError, match="executable"):
        ensure_server(environment)


def test_final_reuse_revalidates_server_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A changed selected Codex version cannot inherit certification."""
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _success,
    )
    versions = iter(["codex-cli 9.9.9", "codex-cli 9.9.10"])
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: next(versions),
    )

    with pytest.raises(CodexServerError, match="version"):
        ensure_server(environment)


def test_final_reuse_rejects_a_vanished_listener(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A listener that vanishes after the first probe is not helper success."""
    diagnostics = iter([_success(), _nonzero()])
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        lambda: next(diagnostics),
    )

    with pytest.raises(CodexServerError, match="listener"):
        ensure_server(environment)


def test_final_reuse_revalidates_worker_liveness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A worker that vanishes during the final snapshot cannot be reused."""
    environment, _sleeps, _commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _success,
    )
    snapshots = 0
    vanished = False

    def snapshot(
        _paths: object,
        _options: object = (),
    ) -> codex_server._PluginSnapshot:
        nonlocal snapshots, vanished
        snapshots += 1
        if snapshots == 3:
            vanished = True
        return _snapshot()

    monkeypatch.setattr(codex_server, "_plugin_configuration_snapshot", snapshot)
    monkeypatch.setattr(
        codex_server,
        "state_worker_matches",
        lambda _state: not vanished,
    )

    with pytest.raises(CodexServerError, match="worker vanished"):
        ensure_server(environment)


def test_plugin_change_during_startup_is_not_certified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Persistent startup churn cleans every attempt and remains bounded."""
    starting = replace(
        _live_state(),
        phase="starting",
        plugin_fingerprint="startup snapshot",
    )
    snapshot_calls = 0
    observed: list[str] = []
    state_reads = 0
    terminated: list[OwnedServer] = []
    removals: list[object] = []

    def snapshot(
        _paths: object,
        _options: object = (),
    ) -> codex_server._PluginSnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        generation = (
            "changed during startup"
            if snapshot_calls % 3 == 0
            else "startup generation"
        )
        value = _snapshot("startup snapshot", generation)
        observed.append(value.generation)
        return value

    def read_state(_paths: object) -> OwnedServer | None:
        nonlocal state_reads
        state_reads += 1
        return None if state_reads % 2 == 1 else starting

    def spawn(*args: object) -> OwnedServer:
        assert args[-3] == "startup snapshot"
        return starting

    def terminate(state: OwnedServer, **_kwargs: object) -> None:
        terminated.append(state)

    monkeypatch.setattr(
        codex_server,
        "_resolve_codex",
        lambda _env: starting.codex_path,
    )
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: starting.codex_version,
    )
    monkeypatch.setattr(
        codex_server,
        "_codex_executable_identity",
        lambda _path: "verified executable",
    )
    monkeypatch.setattr(
        codex_server,
        "_plugin_configuration_snapshot",
        snapshot,
    )
    monkeypatch.setattr(codex_server, "_read_state", read_state)
    monkeypatch.setattr(
        codex_server,
        "_probe_server",
        lambda *_args: ServerProbe(running=False),
    )
    monkeypatch.setattr(codex_server, "_check_socket_path", lambda _paths: None)
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: True,
    )
    monkeypatch.setattr(codex_server, "spawn_supervisor", spawn)
    monkeypatch.setattr(
        codex_server,
        "_wait_until_ready",
        lambda *_args: ServerProbe(running=True, server_version="9.9.9"),
    )
    monkeypatch.setattr(codex_server, "_write_state", _forbid_mutation)
    monkeypatch.setattr(codex_server, "_terminate_owned", terminate)
    monkeypatch.setattr(
        codex_server,
        "_remove_state",
        lambda paths: removals.append(paths),
    )
    monkeypatch.setattr(codex_server_retry.time, "sleep", lambda _delay: None)

    with pytest.raises(CodexServerError, match="changed during app-server startup"):
        ensure_server({"CODEX_HOME": str(tmp_path / "home")})

    expected_attempts = codex_server_retry.PLUGIN_SNAPSHOT_ATTEMPTS
    assert observed == [
        item
        for _attempt in range(expected_attempts)
        for item in (
            "startup generation",
            "startup generation",
            "changed during startup",
        )
    ]
    assert terminated == [starting] * expected_attempts
    assert len(removals) == expected_attempts


def test_listener_replacement_during_startup_snapshot_is_not_certified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The final startup snapshot cannot create a listener ownership gap."""
    starting = replace(
        _live_state(),
        phase="starting",
        plugin_fingerprint="startup snapshot",
    )
    snapshots = 0
    replaced = False
    states = iter([None, starting])
    terminated: list[OwnedServer] = []
    removals: list[object] = []

    def snapshot(
        _paths: object,
        _options: object = (),
    ) -> codex_server._PluginSnapshot:
        nonlocal replaced, snapshots
        snapshots += 1
        if snapshots == 3:
            replaced = True
        return _snapshot("startup snapshot", "startup generation")

    monkeypatch.setattr(
        codex_server,
        "_resolve_codex",
        lambda _env: starting.codex_path,
    )
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: starting.codex_version,
    )
    monkeypatch.setattr(
        codex_server,
        "_codex_executable_identity",
        lambda _path: "verified executable",
    )
    monkeypatch.setattr(codex_server, "_plugin_configuration_snapshot", snapshot)
    monkeypatch.setattr(codex_server, "_read_state", lambda _paths: next(states))
    monkeypatch.setattr(
        codex_server,
        "_probe_server",
        lambda *_args: ServerProbe(running=False),
    )
    monkeypatch.setattr(codex_server, "_check_socket_path", lambda _paths: None)
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: not replaced,
    )
    monkeypatch.setattr(
        codex_server,
        "spawn_supervisor",
        lambda *_args: starting,
    )
    monkeypatch.setattr(
        codex_server,
        "_wait_until_ready",
        lambda *_args: ServerProbe(running=True, server_version="9.9.9"),
    )
    monkeypatch.setattr(codex_server, "_write_state", _forbid_mutation)
    monkeypatch.setattr(
        codex_server,
        "_terminate_owned",
        lambda state, **_kwargs: terminated.append(state),
    )
    monkeypatch.setattr(
        codex_server,
        "_remove_state",
        lambda paths: removals.append(paths),
    )

    with pytest.raises(CodexServerError, match="changed during startup checks"):
        ensure_server({"CODEX_HOME": str(tmp_path / "home")})

    assert snapshots == 3
    assert terminated == [starting]
    assert len(removals) == 1


def test_listener_replacement_after_running_state_is_not_certified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The running-state write cannot be the last startup ownership check."""
    starting = replace(
        _live_state(),
        phase="starting",
        plugin_fingerprint="startup snapshot",
    )
    states: list[OwnedServer | None] = [None, starting]
    replaced = False
    terminated: list[OwnedServer] = []

    def read_state(_paths: object) -> OwnedServer | None:
        return states.pop(0)

    def write_state(_paths: object, state: OwnedServer) -> None:
        nonlocal replaced
        replaced = True
        states.append(state)

    probes = iter(
        [
            ServerProbe(running=False),
            ServerProbe(running=True, server_version="9.9.9"),
        ]
    )
    monkeypatch.setattr(
        codex_server,
        "_resolve_codex",
        lambda _env: starting.codex_path,
    )
    monkeypatch.setattr(
        codex_server,
        "_codex_executable_identity",
        lambda _path: "verified executable",
    )
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: starting.codex_version,
    )
    monkeypatch.setattr(
        codex_server,
        "_plugin_configuration_snapshot",
        lambda _paths, _options=(): _snapshot(
            "startup snapshot",
            "startup generation",
        ),
    )
    monkeypatch.setattr(codex_server, "_read_state", read_state)
    monkeypatch.setattr(
        codex_server,
        "_probe_server",
        lambda *_args: next(probes),
    )
    monkeypatch.setattr(codex_server, "_check_socket_path", lambda _paths: None)
    monkeypatch.setattr(
        codex_server,
        "state_controller_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "state_worker_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: not replaced,
    )
    monkeypatch.setattr(
        codex_server,
        "spawn_supervisor",
        lambda *_args: starting,
    )
    monkeypatch.setattr(
        codex_server,
        "_wait_until_ready",
        lambda *_args: ServerProbe(running=True, server_version="9.9.9"),
    )
    monkeypatch.setattr(codex_server, "_write_state", write_state)
    monkeypatch.setattr(
        codex_server,
        "_terminate_owned",
        lambda state, **_kwargs: terminated.append(state),
    )
    monkeypatch.setattr(codex_server, "_remove_state", lambda _paths: None)

    with pytest.raises(CodexServerError, match="listener changed"):
        ensure_server({"CODEX_HOME": str(tmp_path / "home")})

    assert terminated == [starting]


def test_generation_collision_refuses_mismatched_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mismatched state at a selected generation fails closed."""
    state = _live_state(codex_version="codex-cli 9.9.8")
    probes = 0

    def probe(
        _path: str | None,
        _env: Mapping[str, str],
        _paths: object,
    ) -> ServerProbe:
        nonlocal probes
        probes += 1
        return ServerProbe(running=False)

    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: state.codex_path)
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
    monkeypatch.setattr(
        codex_server,
        "_plugin_configuration_snapshot",
        lambda _paths, _options=(): _snapshot(),
    )
    monkeypatch.setattr(codex_server, "_read_state", lambda _paths: state)
    monkeypatch.setattr(
        codex_server,
        "state_controller_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "state_worker_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(codex_server, "_probe_server", probe)
    monkeypatch.setattr(codex_server, "_terminate_owned", _forbid_mutation)
    monkeypatch.setattr(codex_server, "_remove_state", _forbid_mutation)
    monkeypatch.setattr(codex_server, "spawn_supervisor", _forbid_mutation)

    with pytest.raises(CodexServerError, match="Codex CLI version changed"):
        ensure_server({"CODEX_HOME": str(tmp_path / "home")})

    assert probes == 1
