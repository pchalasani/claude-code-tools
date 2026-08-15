"""Runtime callback configuration for managed Codex App Servers."""

from __future__ import annotations

import json
from collections.abc import MutableMapping, Sequence

from claude_code_tools.codex_server_models import (
    CALLBACK_ENDPOINT_ENV,
    CODEX_SERVER_OPTIONS_ENV,
)


def configure_app_server_callback(
    environment: MutableMapping[str, str],
    codex_options: Sequence[str],
    endpoint: str,
) -> None:
    """Inject callback routing into the worker process and its tool shells."""
    environment[CALLBACK_ENDPOINT_ENV] = endpoint
    environment[CODEX_SERVER_OPTIONS_ENV] = json.dumps(
        app_server_worker_options(codex_options, endpoint),
        separators=(",", ":"),
    )


def app_server_worker_options(
    codex_options: Sequence[str],
    endpoint: str,
) -> list[str]:
    """Add the generation endpoint to App Server tool-shell configuration."""
    return [
        *codex_options,
        "--config",
        (
            f"shell_environment_policy.set.{CALLBACK_ENDPOINT_ENV}="
            f"{json.dumps(endpoint)}"
        ),
    ]
