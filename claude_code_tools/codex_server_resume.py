"""Route resume commands to a managed server that already owns the thread."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from claude_code_tools.codex_app_server_rpc import (
    AppServerQueryError,
    loaded_thread_ids,
)
from claude_code_tools.codex_server_models import (
    CodexServerError,
    OwnedServer,
    ServerPaths,
    all_server_paths,
    base_paths_from_env,
    read_state,
)
from claude_code_tools.codex_server_fingerprint import (
    plugin_configuration_snapshot,
)
from claude_code_tools.codex_server_process import (
    codex_executable_identity,
    state_controller_matches,
    state_worker_matches,
)
from claude_code_tools.codex_server_reuse import same_server_launch
from claude_code_tools.codex_server_retry import (
    PluginSnapshotChangedError,
    retry_plugin_snapshot_changes,
)
from claude_code_tools.resolve_session import ResolverError, resolve


_THREAD_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_RESUME_OPTIONS_WITH_VALUES = {
    "--add-dir",
    "--ask-for-approval",
    "--cd",
    "--config",
    "--disable",
    "--enable",
    "--image",
    "--local-provider",
    "--model",
    "--profile",
    "--remote-auth-token-env",
    "--sandbox",
    "-C",
    "-a",
    "-c",
    "-i",
    "-m",
    "-p",
    "-s",
}


@retry_plugin_snapshot_changes
def resume_server_paths(
    arguments: Sequence[str],
    environment: Mapping[str, str],
    codex_options: Sequence[str],
) -> ServerPaths | None:
    """Find a compatible managed generation already holding a resumed thread."""
    if _resume_uses_last(arguments):
        # Codex applies model-provider, source-kind, cwd, and latest-turn-cwd
        # filters when resolving --last. Do not approximate that selection and
        # risk attaching the TUI to an unrelated older generation.
        return None
    reference = _resume_reference(arguments)
    if reference is False or reference is None:
        return None
    target = _resolve_thread_id(reference, environment)
    if target is None:
        return None

    # Import here to avoid making the core lifecycle module depend on routing.
    from claude_code_tools import codex_server

    active_env = dict(environment)
    base = base_paths_from_env(active_env)
    codex_path = codex_server._resolve_codex(active_env)
    identity = codex_executable_identity(codex_path)
    child_env = codex_server._command_env(active_env, base)
    version = codex_server._require_compatible_codex(codex_path, child_env)
    plugin_snapshot = plugin_configuration_snapshot(
        base,
        codex_options,
    )
    loaded: list[tuple[ServerPaths, frozenset[str]]] = []
    for paths in all_server_paths(base):
        state = _compatible_live_state(
            paths,
            codex_path,
            identity,
            version,
            plugin_snapshot.fingerprint,
            codex_options,
        )
        if state is None:
            continue
        try:
            thread_ids = loaded_thread_ids(paths.socket_path)
        except AppServerQueryError as exc:
            raise CodexServerError(
                "cannot safely inspect a live managed App Server before resume: "
                f"{exc}"
            ) from exc
        current = read_state(paths)
        if (
            current is None
            or not same_server_launch(state, current)
            or not state_controller_matches(current)
            or not codex_server._listener_matches_worker(current, paths)
        ):
            raise CodexServerError(
                "a managed App Server changed while resume ownership was checked; "
                "retry the resume command"
            )
        if thread_ids:
            loaded.append((paths, thread_ids))

    final_snapshot = plugin_configuration_snapshot(base, codex_options)
    if final_snapshot.fingerprint != plugin_snapshot.fingerprint:
        raise PluginSnapshotChangedError(
            "the Codex plugin or marketplace snapshot changed while resume "
            "ownership was checked; retry after updates finish"
        )
    return _select_loaded_server(loaded, target)


def _select_loaded_server(
    loaded: Sequence[tuple[ServerPaths, frozenset[str]]],
    target: str,
) -> ServerPaths | None:
    """Select one authoritative generation from already certified queries."""
    matches = [paths for paths, ids in loaded if target in ids]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CodexServerError(
            f"thread {target} is loaded by multiple managed App Servers; "
            "refusing to guess which in-memory copy is authoritative"
        )
    return None


def _compatible_live_state(
    paths: ServerPaths,
    codex_path: str,
    identity: str,
    version: str,
    plugin_fingerprint: str,
    codex_options: Sequence[str],
) -> OwnedServer | None:
    """Return certified state for a compatible live managed generation."""
    from claude_code_tools import codex_server

    try:
        state = read_state(paths)
    except CodexServerError:
        return None
    if (
        state is None
        or state.phase != "running"
        or state.codex_path != codex_path
        or state.codex_executable_identity != identity
        or codex_server._version_key(state.codex_version)
        != codex_server._version_key(version)
        or state.plugin_fingerprint != plugin_fingerprint
        or state.codex_options != tuple(codex_options)
        or not state_controller_matches(state)
        or not state_worker_matches(state)
        or not codex_server._listener_matches_worker(state, paths)
    ):
        return None
    return state


def _resume_reference(
    arguments: Sequence[str],
) -> str | None | Literal[False]:
    """Return a positional resume reference, None for picker/last, or False."""
    index = _resume_command_index(arguments)
    if index is None:
        return False
    if _resume_uses_last(arguments):
        return None
    index += 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if argument in {"--last", "--all"}:
            index += 1
            continue
        option = argument.split("=", 1)[0]
        if option in _RESUME_OPTIONS_WITH_VALUES:
            index += 1 if "=" in argument else 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument
    if index < len(arguments):
        return arguments[index]
    return None


def _resume_uses_last(arguments: Sequence[str]) -> bool:
    """Return whether the actual resume subcommand selected ``--last``."""
    index = _resume_command_index(arguments)
    if index is None:
        return False
    index += 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return False
        if argument == "--last":
            return True
        option = argument.split("=", 1)[0]
        if option in _RESUME_OPTIONS_WITH_VALUES:
            index += 1 if "=" in argument else 2
        else:
            index += 1
    return False


def _resume_command_index(arguments: Sequence[str]) -> int | None:
    """Find the actual TUI subcommand without mistaking an option value."""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return None
        option = argument.split("=", 1)[0]
        if option in _RESUME_OPTIONS_WITH_VALUES:
            index += 1 if "=" in argument else 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return index if argument == "resume" else None
    return None


def _resolve_thread_id(
    reference: str | None,
    environment: Mapping[str, str],
) -> str | None:
    """Resolve a resume name or partial ID without changing persisted state."""
    if reference is None:
        return None
    if _THREAD_ID.fullmatch(reference):
        return reference.lower()
    try:
        result = resolve(
            reference,
            "codex",
            home=environment.get("CODEX_HOME"),
            fallback_on_database_error=True,
        )
    except ResolverError:
        return None
    if result.kind == "single":
        return result.records[0].session_id.lower()
    return None
