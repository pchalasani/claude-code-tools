"""Compatibility checks for reusing a supervised Codex app server."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from claude_code_tools.codex_server_models import (
    CodexServerError,
    OwnedServer,
    ServerPaths,
    ServerProbe,
)


def helper_restart_reason(
    state: OwnedServer,
    codex_path: str,
    executable_identity: str,
    codex_version: str,
    probe: ServerProbe,
    plugin_fingerprint: str,
    paths: ServerPaths,
    codex_options: Sequence[str],
    listener_matches_worker: Callable[[OwnedServer, ServerPaths], bool],
    controller_matches: Callable[[OwnedServer], bool],
    version_key: Callable[[str | None], tuple[int, int, int] | None],
) -> str | None:
    """Explain why a helper listener cannot be safely reused."""
    if not controller_matches(state):
        return "its durable supervisor exited"
    if state.codex_path != codex_path:
        return "the active Codex executable path changed"
    if state.codex_executable_identity is None:
        return "its Codex executable identity predates replacement detection"
    if state.codex_executable_identity != executable_identity:
        return "the active Codex executable was replaced"
    if version_key(state.codex_version) != version_key(codex_version):
        return "the active Codex CLI version changed"
    if probe.server_version is not None and version_key(
        probe.server_version
    ) != version_key(codex_version):
        return "the running app-server version differs from the Codex CLI"
    if (probe.running or probe.accepting) and not listener_matches_worker(
        state,
        paths,
    ):
        return "its socket listener is not owned by the supervised worker"
    if state.plugin_fingerprint is None:
        return "its plugin snapshot predates plugin-change detection"
    if state.plugin_fingerprint != plugin_fingerprint:
        return "the Codex plugin or marketplace configuration changed"
    if state.codex_options != tuple(codex_options):
        return "the app-server configuration options changed"
    return None


def disconnect_refusal(action: str) -> CodexServerError:
    """Explain why a shared-server lifecycle action needs acknowledgement."""
    return CodexServerError(
        f"refusing to {action} the shared app server without --force: this "
        "disconnects every codex-dynamic TUI on that generation, and Codex "
        "exits those sessions. Exit connected sessions first, then retry "
        "with --force; their transcripts remain resumable"
    )


def require_external_compatible(
    codex_version: str,
    probe: ServerProbe,
    version_key: Callable[[str | None], tuple[int, int, int] | None],
) -> None:
    """Reject external listeners, whose plugin snapshot is uncertifiable."""
    server_key = version_key(probe.server_version)
    cli_key = version_key(codex_version)
    if server_key is None:
        raise CodexServerError(
            "an external app server is running, but its version could not be "
            "verified; stop it before using codex-server"
        )
    if server_key != cli_key:
        raise CodexServerError(
            f"external app-server version {probe.server_version!r} does not "
            f"match the selected Codex CLI {codex_version!r}; stop or restart "
            "the external server"
        )
    raise CodexServerError(
        "an external app server is running, but codex-server cannot verify "
        "which plugin snapshot it loaded; stop it before using "
        "codex-dynamic workflow callbacks"
    )
