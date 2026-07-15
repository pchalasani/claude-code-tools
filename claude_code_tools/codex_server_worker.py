"""Pre-exec gate for a durably owned Codex app-server worker."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Mapping, NoReturn, Sequence

from claude_code_tools.codex_server_models import (
    CODEX_SERVER_OPTIONS_ENV,
    ENDPOINT,
    CodexServerError,
)


def run_worker_gate(
    codex_path: str,
    gate_fd: int,
    launch_token: str,
    env: Mapping[str, str],
) -> NoReturn:
    """Wait for durable ownership publication, then replace this process.

    Args:
        codex_path: Exact Codex executable selected by the launcher.
        gate_fd: Pipe whose writer is held only by the supervisor.
        launch_token: Secret token proving that publication completed.
        env: Complete app-server environment.

    Raises:
        CodexServerError: If the supervisor exits or sends an invalid release.
        OSError: If Codex cannot be executed.
    """
    release = _read_release(gate_fd)
    if release != launch_token:
        raise CodexServerError("app-server worker release token did not match")
    options = _server_options(env)
    os.execve(
        codex_path,
        [codex_path, *options, "app-server", "--listen", ENDPOINT],
        dict(env),
    )
    raise AssertionError("os.execve returned unexpectedly")


def _server_options(env: Mapping[str, str]) -> list[str]:
    """Decode launcher-certified global options for the app server."""
    raw = env.get(CODEX_SERVER_OPTIONS_ENV, "[]")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise CodexServerError("app-server options were invalid") from exc
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(not isinstance(item, str) or len(item) > 16_384 for item in value)
    ):
        raise CodexServerError("app-server options were invalid")
    return value


def _read_release(fd: int) -> str:
    """Read one bounded release token, treating supervisor EOF as failure."""
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            raw = stream.readline(256)
    except OSError as exc:
        raise CodexServerError(f"cannot read app-server worker release: {exc}") from exc
    if not raw.endswith(b"\n") or len(raw) > 200:
        raise CodexServerError(
            "app-server supervisor exited before releasing the worker"
        )
    try:
        token = raw[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise CodexServerError("app-server worker release was invalid") from exc
    if not token:
        raise CodexServerError("app-server worker release was empty")
    return token


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the internal pre-exec worker gate."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gate-fd", required=True, type=int)
    parser.add_argument("--launch-token", required=True)
    parser.add_argument("--codex", required=True)
    options = parser.parse_args(arguments)
    try:
        run_worker_gate(
            options.codex,
            options.gate_fd,
            options.launch_token,
            os.environ,
        )
    except (CodexServerError, OSError) as exc:
        print(f"codex-server worker: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
