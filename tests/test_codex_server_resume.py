"""Focused tests for safe cross-generation resume routing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_code_tools import codex_server, codex_server_resume
from claude_code_tools.codex_server_models import (
    CodexServerError,
    OwnedServer,
    ServerPaths,
)
from claude_code_tools.codex_server_resume import (
    _compatible_live_state,
    _resume_reference,
    _resume_uses_last,
    _select_loaded_server,
    resume_server_paths,
)


def _paths(tmp_path: Path, generation: str) -> ServerPaths:
    return ServerPaths(
        codex_home=tmp_path,
        runtime_dir=tmp_path / generation,
        socket_path=tmp_path / f"{generation}.sock",
        state_path=tmp_path / generation / "state.json",
        lock_path=tmp_path / generation / "lock",
        log_path=tmp_path / generation / "log",
        generation=generation,
    )


def test_explicit_resume_uses_generation_that_already_owns_thread(
    tmp_path: Path,
) -> None:
    """A newer current generation cannot steal a loaded older thread."""
    older = _paths(tmp_path, "a" * 24)
    newer = _paths(tmp_path, "b" * 24)
    loaded = [
        (older, frozenset({"wanted-thread"})),
        (newer, frozenset({"different-thread"})),
    ]

    assert _select_loaded_server(loaded, "wanted-thread") == older


def test_unknown_thread_falls_back_to_current_generation(tmp_path: Path) -> None:
    """Normal startup remains available when no live server owns the thread."""
    older = _paths(tmp_path, "a" * 24)

    assert _select_loaded_server(
        [(older, frozenset({"other-thread"}))],
        "new-thread",
    ) is None


def test_duplicate_owners_refuse_to_guess_between_generations(
    tmp_path: Path,
) -> None:
    """An explicit thread cannot silently choose between duplicate owners."""
    loaded = [
        (_paths(tmp_path, "a" * 24), frozenset({"wanted-thread"})),
        (_paths(tmp_path, "b" * 24), frozenset({"wanted-thread"})),
    ]

    with pytest.raises(CodexServerError, match="multiple managed App Servers"):
        _select_loaded_server(loaded, "wanted-thread")


def test_bare_resume_does_not_inspect_or_route_loaded_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native picker stays on the current generation."""
    monkeypatch.setattr(
        codex_server_resume,
        "_resolve_thread_id",
        lambda *_args: pytest.fail("bare resume must not resolve a thread"),
    )

    assert resume_server_paths(["resume"], {}, ()) is None


def test_compatible_state_requires_current_plugin_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loaded thread cannot route to a stale plugin generation."""
    paths = _paths(tmp_path, "a" * 24)
    state = OwnedServer(
        pid=101,
        pgid=101,
        process_started_at="controller-start",
        codex_path="/codex",
        codex_version="0.147.0",
        launched_at="now",
        phase="running",
        plugin_fingerprint="old-plugin-snapshot",
        codex_executable_identity="codex-identity",
        worker_pid=102,
        worker_pgid=102,
        worker_started_at="worker-start",
    )
    monkeypatch.setattr(codex_server_resume, "read_state", lambda _paths: state)
    monkeypatch.setattr(
        codex_server_resume,
        "state_controller_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server_resume,
        "state_worker_matches",
        lambda _state: True,
    )
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: True,
    )

    assert (
        _compatible_live_state(
            paths,
            "/codex",
            "codex-identity",
            "0.147.0",
            "current-plugin-snapshot",
            (),
        )
        is None
    )


def test_resume_rechecks_supervisor_after_ownership_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker left behind by a dead supervisor is not selected."""
    paths = _paths(tmp_path, "a" * 24)
    thread_id = "01a00124-9417-79a1-a34b-17b30984051b"
    state = OwnedServer(
        pid=101,
        pgid=101,
        process_started_at="controller-start",
        codex_path="/codex",
        codex_version="0.147.0",
        launched_at="now",
        phase="running",
        plugin_fingerprint="plugin-snapshot",
        codex_executable_identity="codex-identity",
        worker_pid=102,
        worker_pgid=102,
        worker_started_at="worker-start",
    )
    snapshot = SimpleNamespace(fingerprint="plugin-snapshot")
    monkeypatch.setattr(
        codex_server_resume,
        "base_paths_from_env",
        lambda _env: paths,
    )
    monkeypatch.setattr(
        codex_server_resume,
        "codex_executable_identity",
        lambda _path: "codex-identity",
    )
    monkeypatch.setattr(
        codex_server_resume,
        "plugin_configuration_snapshot",
        lambda _paths, _options: snapshot,
    )
    monkeypatch.setattr(
        codex_server_resume,
        "all_server_paths",
        lambda _paths: [paths],
    )
    monkeypatch.setattr(
        codex_server_resume,
        "_compatible_live_state",
        lambda *_args: state,
    )
    monkeypatch.setattr(
        codex_server_resume,
        "loaded_thread_ids",
        lambda _path: frozenset({thread_id}),
    )
    monkeypatch.setattr(codex_server_resume, "read_state", lambda _paths: state)
    monkeypatch.setattr(
        codex_server_resume,
        "same_server_launch",
        lambda _expected, _current: True,
    )
    monkeypatch.setattr(
        codex_server_resume,
        "state_controller_matches",
        lambda _state: False,
    )
    monkeypatch.setattr(codex_server, "_resolve_codex", lambda _env: "/codex")
    monkeypatch.setattr(codex_server, "_command_env", lambda _env, _paths: {})
    monkeypatch.setattr(
        codex_server,
        "_require_compatible_codex",
        lambda _path, _env: "0.147.0",
    )
    monkeypatch.setattr(
        codex_server,
        "_listener_matches_worker",
        lambda _state, _paths: True,
    )

    with pytest.raises(CodexServerError, match="changed while resume ownership"):
        resume_server_paths(["resume", thread_id], {}, ())


def test_resume_reference_preserves_explicit_id_and_last_mode() -> None:
    """Routing distinguishes explicit resume targets from the last-session mode."""
    assert _resume_reference(["resume", "thread-id"]) == "thread-id"
    assert _resume_reference(["-m", "resume", "resume", "thread-id"]) == (
        "thread-id"
    )
    assert _resume_reference(["resume", "-m", "model", "thread-id"]) == (
        "thread-id"
    )
    assert _resume_reference(["resume", "--last"]) is None
    assert _resume_reference(["exec", "task"]) is False
    assert _resume_uses_last(["resume", "--last"])
    assert not _resume_uses_last(["resume", "--cd=--last", "thread-id"])
    assert not _resume_uses_last(["resume", "--", "--last"])
    assert not _resume_uses_last(["resume"])
