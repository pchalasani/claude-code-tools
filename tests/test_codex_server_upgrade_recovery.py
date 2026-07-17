"""Regression tests for app-server lifecycle behavior across upgrades."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from claude_code_tools import codex_server_legacy, codex_server_retry
from claude_code_tools.codex_server_models import (
    OwnedServer,
    ServerPaths,
    StateFileError,
    read_state,
    write_state,
)


def test_legacy_process_claim_matches_live_process() -> None:
    """The old textual identity remains available only for reauthentication."""
    pid = os.getpid()
    legacy_identity = codex_server_legacy._legacy_process_identity(pid)

    assert legacy_identity is not None
    assert codex_server_legacy._legacy_process_matches(
        pid,
        os.getpgrp(),
        legacy_identity,
    )


def test_legacy_state_reauthenticates_complete_process_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Legacy state becomes signal-capable only after full listener validation."""
    paths = _server_paths(tmp_path)
    state = _legacy_state()
    identities = {
        state.pid: "darwin:100:1",
        state.worker_pid: "darwin:100:2",
        303: "darwin:100:3",
    }
    groups = {
        state.pid: state.pgid,
        state.worker_pid: state.worker_pgid,
        303: state.worker_pgid,
    }
    monkeypatch.setattr(
        codex_server_legacy,
        "_legacy_processes_match",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_socket_identity",
        lambda _path: (10, 20),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_state_creation_barrier",
        lambda _path: (30, 40, 101_000_000),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_parent_pid",
        lambda _pid: state.pid,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "process_identity",
        identities.get,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "process_matches",
        lambda pid, pgid, identity: (
            identities.get(pid) == identity and groups.get(pid) == pgid
        ),
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        paths,
        state,
        lambda _path: 303,
        lambda _paths: (state, (30, 40, 101_000_000)),
    )

    assert recovered is not None
    assert recovered.process_started_at == "darwin:100:1"
    assert recovered.worker_started_at == "darwin:100:2"


def test_legacy_reauthentication_rejects_socket_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A replaced listener cannot authorize signals to old process groups."""
    paths = _server_paths(tmp_path)
    state = _legacy_state()
    socket_identities = iter([(10, 20), (10, 21)])
    identities = {
        state.pid: "darwin:100:1",
        state.worker_pid: "darwin:100:2",
        303: "darwin:100:3",
    }
    monkeypatch.setattr(
        codex_server_legacy,
        "_socket_identity",
        lambda _path: next(socket_identities),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_state_creation_barrier",
        lambda _path: (30, 40, 101_000_000),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_legacy_processes_match",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_parent_pid",
        lambda _pid: state.pid,
    )
    monkeypatch.setattr(codex_server_legacy, "process_identity", identities.get)
    monkeypatch.setattr(
        codex_server_legacy,
        "process_matches",
        lambda _pid, _pgid, _identity: True,
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        paths,
        state,
        lambda _path: 303,
        lambda _paths: (state, (30, 40, 101_000_000)),
    )

    assert recovered == state


@pytest.mark.parametrize(
    "failure",
    ["parent", "peer", "listener-group", "controller-identity"],
)
def test_legacy_reauthentication_rejects_process_chain_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    """Every process-chain edge must remain stable through revalidation."""
    paths = _server_paths(tmp_path)
    state = _legacy_state()
    identities = {
        state.pid: "darwin:100:1",
        state.worker_pid: "darwin:100:2",
        303: "darwin:100:3",
        304: "darwin:100:4",
    }
    groups = {
        state.pid: state.pgid,
        state.worker_pid: state.worker_pgid,
        303: state.worker_pgid,
        304: state.worker_pgid,
    }
    peer_values = iter([303, 304]) if failure == "peer" else None
    monkeypatch.setattr(
        codex_server_legacy,
        "_socket_identity",
        lambda _path: (10, 20),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_state_creation_barrier",
        lambda _path: (30, 40, 101_000_000),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_legacy_processes_match",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_parent_pid",
        lambda _pid: state.pid + 1 if failure == "parent" else state.pid,
    )
    monkeypatch.setattr(codex_server_legacy, "process_identity", identities.get)

    def process_matches(pid: int, pgid: int, identity: str) -> bool:
        if failure == "listener-group" and pid == 303:
            return False
        if failure == "controller-identity" and pid == state.pid:
            return False
        return identities.get(pid) == identity and groups.get(pid) == pgid

    monkeypatch.setattr(
        codex_server_legacy,
        "process_matches",
        process_matches,
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        paths,
        state,
        lambda _path: next(peer_values) if peer_values is not None else 303,
        lambda _paths: (state, (30, 40, 101_000_000)),
    )

    assert recovered == state


def test_legacy_reauthentication_rejects_chain_created_after_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fully replaced pre-scan chain cannot inherit historical ownership."""
    paths = _server_paths(tmp_path)
    state = _legacy_state()
    identities = {
        state.pid: "darwin:100:1",
        state.worker_pid: "darwin:100:2",
        303: "darwin:100:3",
    }
    monkeypatch.setattr(
        codex_server_legacy,
        "_state_creation_barrier",
        lambda _path: (30, 40, 99_999_999),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_socket_identity",
        lambda _path: (10, 20),
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_legacy_processes_match",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_legacy,
        "_parent_pid",
        lambda _pid: state.pid,
    )
    monkeypatch.setattr(codex_server_legacy, "process_identity", identities.get)
    monkeypatch.setattr(
        codex_server_legacy,
        "process_matches",
        lambda _pid, _pgid, _identity: True,
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        paths,
        state,
        lambda _path: 303,
        lambda _paths: (state, (30, 40, 99_999_999)),
    )

    assert recovered == state


def test_partial_legacy_state_remains_unauthenticated(tmp_path: Path) -> None:
    """Missing worker ownership can never authorize compatibility cleanup."""
    state = replace(
        _legacy_state(),
        worker_pid=None,
        worker_pgid=None,
        worker_started_at=None,
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        _server_paths(tmp_path),
        state,
        lambda _path: 303,
    )

    assert recovered == state


def test_legacy_reauthentication_rejects_replaced_state_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """State content cannot inherit creation evidence from its replacement."""
    paths = _server_paths(tmp_path)
    write_state(paths, _legacy_state())
    initial = read_state(paths)
    assert initial is not None
    write_state(paths, replace(initial, launch_token="replacement"))
    monkeypatch.setattr(
        codex_server_legacy,
        "_socket_identity",
        lambda _path: pytest.fail("process validation must not start"),
    )

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        paths,
        initial,
        lambda _path: 303,
    )

    assert recovered == initial


def test_legacy_reauthentication_fails_closed_during_state_rewrite(
    tmp_path: Path,
) -> None:
    """A concurrent malformed-state read cannot authorize process signals."""
    state = _legacy_state()

    def fail_read(
        _paths: ServerPaths,
    ) -> tuple[OwnedServer | None, tuple[int, int, int] | None]:
        raise StateFileError("state changed")

    recovered = codex_server_legacy.reauthenticate_legacy_state(
        _server_paths(tmp_path),
        state,
        lambda _path: 303,
        fail_read,
    )

    assert recovered == state


def test_snapshot_retry_recovers_from_transient_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short plugin-cache update no longer makes startup user-visible."""
    calls = 0
    delays: list[float] = []
    monkeypatch.setattr(codex_server_retry.time, "sleep", delays.append)

    @codex_server_retry.retry_plugin_snapshot_changes
    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise codex_server_retry.PluginSnapshotChangedError("updating")
        return "running"

    assert operation() == "running"
    assert calls == 3
    assert delays == [0.05, 0.15]


def test_snapshot_retry_remains_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent plugin churn still fails after a small fixed attempt budget."""
    calls = 0
    monkeypatch.setattr(codex_server_retry.time, "sleep", lambda _delay: None)

    @codex_server_retry.retry_plugin_snapshot_changes
    def operation() -> None:
        nonlocal calls
        calls += 1
        raise codex_server_retry.PluginSnapshotChangedError("still updating")

    with pytest.raises(
        codex_server_retry.PluginSnapshotChangedError,
        match="still updating",
    ):
        operation()

    assert calls == codex_server_retry.PLUGIN_SNAPSHOT_ATTEMPTS


def _legacy_state() -> OwnedServer:
    """Return ownership state written by the original callback helper."""
    return OwnedServer(
        pid=101,
        pgid=101,
        process_started_at="Tue Jul 14 10:09:10 2026",
        codex_path="/fake/codex",
        codex_version="codex-cli 0.144.4",
        launched_at="2026-07-14T14:09:10.604461+00:00",
        phase="running",
        launch_token="launch-token",
        worker_pid=202,
        worker_pgid=202,
        worker_started_at="Tue Jul 14 10:09:10 2026",
    )


def _server_paths(tmp_path: Path) -> ServerPaths:
    """Return isolated legacy server paths."""
    return ServerPaths(
        codex_home=tmp_path,
        runtime_dir=tmp_path / "runtime",
        socket_path=tmp_path / "server.sock",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
        log_path=tmp_path / "server.log",
    )
