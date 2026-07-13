"""Regression tests for non-destructive helper health probes."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import pytest

import claude_code_tools.codex_server as codex_server
from claude_code_tools.codex_server import OwnedServer, ServerProbe, ensure_server


Diagnostic = subprocess.CompletedProcess[str] | None


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
        worker_pid=12_346,
        worker_pgid=12_346,
        worker_started_at="verified worker",
    )


def _forbid_mutation(*_args: object, **_kwargs: object) -> NoReturn:
    """Fail if a probe-only reuse path mutates server ownership."""
    raise AssertionError("live helper ownership was mutated")


def _arrange_live_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    diagnostics: Callable[[], Diagnostic],
) -> tuple[dict[str, str], list[float], list[Sequence[str]]]:
    """Install deterministic helper ownership and diagnostic responses."""
    state = _live_state()
    sleeps: list[float] = []
    commands: list[Sequence[str]] = []

    def run_command(
        command: Sequence[str],
        _env: Mapping[str, str],
    ) -> Diagnostic:
        commands.append(command)
        return diagnostics()

    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: state.codex_path)
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
    monkeypatch.setattr(codex_server, "_run_command", run_command)
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


@pytest.mark.parametrize("first", [None, _nonzero()])
def test_transient_probe_failure_retries_without_restarting_live_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first: Diagnostic,
) -> None:
    """A timeout or nonzero diagnostic gets a bounded reuse retry."""
    results = iter([first, _success()])
    environment, sleeps, commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        lambda: next(results),
    )

    status = ensure_server(environment)

    assert status.status == "running"
    assert status.ownership == "helper"
    assert status.pid == 12_345
    assert len(commands) == 2
    assert sleeps == [codex_server.POLL_SECONDS]


def test_sustained_probe_failure_preserves_live_helper_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exhausted health retries cannot destroy verified live ownership."""
    environment, sleeps, commands = _arrange_live_helper(
        monkeypatch,
        tmp_path,
        _nonzero,
    )

    status = ensure_server(environment)

    assert status.status == "degraded"
    assert status.ownership == "helper"
    assert status.pid == 12_345
    assert status.detail is not None
    assert "ownership was preserved" in status.detail
    assert "codex-server restart" in status.detail
    assert len(commands) == codex_server.REUSE_PROBE_ATTEMPTS
    assert sleeps == [
        codex_server.POLL_SECONDS,
        codex_server.POLL_SECONDS * 2,
    ]


def test_upgrade_metadata_still_requests_intentional_rollover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A CLI version change remains a safe reason to replace the helper."""
    state = _live_state(codex_version="codex-cli 9.9.8")
    terminated: list[OwnedServer] = []
    probes = 0

    def probe(
        _path: str | None,
        _env: Mapping[str, str],
        _paths: object,
    ) -> ServerProbe:
        nonlocal probes
        probes += 1
        return ServerProbe(running=False)

    def terminate(
        owned: OwnedServer,
        graceful_seconds: float,
        forced_seconds: float,
    ) -> None:
        del graceful_seconds, forced_seconds
        terminated.append(owned)

    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: state.codex_path)
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: "codex-cli 9.9.9",
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
    monkeypatch.setattr(codex_server, "_terminate_owned", terminate)
    monkeypatch.setattr(codex_server, "_remove_state", lambda _paths: None)
    monkeypatch.setattr(
        codex_server,
        "spawn_supervisor",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("replacement requested")),
    )

    with pytest.raises(RuntimeError, match="replacement requested"):
        ensure_server({"CODEX_HOME": str(tmp_path / "home")})

    assert terminated == [state]
    assert probes == 1
