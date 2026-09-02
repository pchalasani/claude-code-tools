"""Wake the current agent session when an existing GitHub issue gets a reply."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from urllib.parse import urlparse

import click

from claude_code_tools.codex_server_process import process_identity
from claude_code_tools.github_watch_store import IssueWatch, WatchStore
from claude_code_tools.issue_reply_delivery import (
    _MAX_MONITOR_MESSAGE_BYTES,
    _MONITOR_ACK,
    DeliveryConfigurationError,
    DeliveryTarget,
    codex_target_from_environment,
)

_DAEMON_START_SECONDS = 5.0
_DAEMON_STOP_SECONDS = 5.0
_MAX_GH_OUTPUT_BYTES = 1024 * 1024


@click.command()
@click.argument("issue_url", required=False)
@click.option("--status", "show_status", is_flag=True, help="Show watcher status.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON with --status.")
@click.option("--start", is_flag=True, help="Start the shared watcher.")
@click.option("--stop", is_flag=True, help="Stop the shared watcher.")
@click.option("--cancel", metavar="WATCH_ID", help="Cancel one pending wakeup.")
@click.option(
    "--claude-monitor",
    is_flag=True,
    help="Wait through Claude Code's Monitor tool until the reply arrives.",
)
def cli(
    issue_url: str | None,
    show_status: bool,
    as_json: bool,
    start: bool,
    stop: bool,
    cancel: str | None,
    claude_monitor: bool,
) -> None:
    """Wake this session when ISSUE_URL receives its first future comment."""
    modes = sum((show_status, start, stop, cancel is not None))
    if modes > 1 or (modes and issue_url is not None) or (modes and claude_monitor):
        raise click.UsageError(
            "provide an issue URL or exactly one management option"
        )
    if as_json and not show_status:
        raise click.UsageError("--json requires --status")
    if show_status:
        _show_status(as_json)
        return
    if start:
        started = _ensure_watcher(WatchStore())
        click.echo("Watcher started." if started else "Watcher is already running.")
        return
    if stop:
        _stop_watcher(WatchStore())
        return
    if cancel is not None:
        if not WatchStore().cancel(cancel):
            raise click.ClickException("no unique pending watch matches that ID")
        click.echo("Reply wakeup canceled.")
        return
    if issue_url is None:
        raise click.UsageError("provide a GitHub issue URL")
    if claude_monitor:
        _run_claude_monitor(issue_url)
        return
    _register(issue_url)


def _register(
    issue_url: str,
    target: DeliveryTarget | None = None,
    announce: bool = True,
    preserve_on_start_failure: bool = True,
) -> IssueWatch:
    """Validate and durably register one existing issue URL."""
    if target is None:
        try:
            target = codex_target_from_environment()
        except DeliveryConfigurationError as exc:
            raise click.ClickException(str(exc)) from exc
    repository, issue_number, host = _parse_issue_url(issue_url)
    canonical_url = _verify_issue(repository, issue_number, host)
    store = WatchStore()
    watch = store.add_watch(
        repository=repository,
        issue_number=issue_number,
        issue_url=canonical_url,
        target_kind=target.kind,
        target=target.payload,
        github_host=host,
        github_config_dir=os.environ.get("GH_CONFIG_DIR"),
    )
    try:
        _ensure_watcher(store)
    except Exception as exc:
        if not preserve_on_start_failure:
            store.cancel(watch.watch_id)
        raise click.ClickException(
            "the wakeup was durably registered, but the shared watcher did not "
            f"start: {exc}; run 'github-wake --start'"
        ) from exc
    if announce:
        click.echo(
            f"Reply wakeup armed for {canonical_url} ({watch.watch_id[:8]})."
        )
    return watch


def _run_claude_monitor(issue_url: str) -> None:
    """Register a watch and emit its reply through one live Monitor process."""
    store = WatchStore()
    with _monitor_listener() as (listener, socket_path):
        stopped = False
        notification_emitted = False
        watch: IssueWatch | None = None

        def stop_monitor(_signum: int, _frame: FrameType | None) -> None:
            nonlocal stopped
            stopped = True
            listener.close()

        previous = signal.signal(signal.SIGTERM, stop_monitor)
        try:
            watch = _register(
                issue_url,
                target=DeliveryTarget(
                    kind="claude-monitor-v1",
                    payload={"socketPath": str(socket_path)},
                ),
                announce=False,
                preserve_on_start_failure=False,
            )
            if stopped:
                return
            try:
                _receive_monitor_message(
                    listener,
                    lambda message: click.echo(message, color=False),
                )
                notification_emitted = True
            except OSError:
                if not stopped:
                    raise
                return
        finally:
            signal.signal(signal.SIGTERM, previous)
            if watch is not None and not notification_emitted:
                store.cancel(watch.watch_id)


@contextmanager
def _monitor_listener() -> Iterator[tuple[socket.socket, Path]]:
    """Create one private short-lived Unix listener for Claude delivery."""
    directory = Path(
        tempfile.mkdtemp(prefix=f"cctools-gw-{os.getuid()}-", dir="/tmp")
    )
    path = directory / "reply.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        yield listener, path
    finally:
        listener.close()
        path.unlink(missing_ok=True)
        directory.rmdir()


def _receive_monitor_message(
    listener: socket.socket,
    emit: Callable[[str], None],
) -> None:
    """Print only a fully received reply and acknowledge it to the daemon."""
    connection, _ = listener.accept()
    with connection:
        connection.settimeout(10)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = connection.recv(8192)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_MONITOR_MESSAGE_BYTES:
                raise RuntimeError("Claude monitor notification is too large")
            chunks.append(chunk)
        try:
            message = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Claude monitor notification is not UTF-8") from exc
        if not message or "\n" in message or "\r" in message:
            raise RuntimeError("Claude monitor notification is not one line")
        emit(message)
        connection.sendall(_MONITOR_ACK)


def _verify_issue(repository: str, issue_number: int, host: str) -> str:
    """Verify an issue exists and return its canonical URL."""
    completed = _run_gh(
        [
            "api",
            "--hostname",
            host,
            f"repos/{repository}/issues/{issue_number}",
        ]
    )
    try:
        payload = json.loads(completed.stdout)
        canonical_url = payload["html_url"]
        returned_number = payload["number"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise click.ClickException("gh returned invalid issue metadata") from exc
    if "pull_request" in payload:
        raise click.ClickException("the URL identifies a pull request, not an issue")
    if returned_number != issue_number or not isinstance(canonical_url, str):
        raise click.ClickException("gh returned mismatched issue metadata")
    verified_repo, verified_number, verified_host = _parse_issue_url(canonical_url)
    if (
        verified_repo.casefold() != repository.casefold()
        or verified_number != issue_number
        or verified_host.casefold() != host.casefold()
    ):
        raise click.ClickException("gh returned mismatched issue metadata")
    return canonical_url


def _run_gh(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one bounded noninteractive GitHub CLI command."""
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(f"cannot run gh: {exc}") from exc
    if completed.returncode != 0:
        diagnostic = " ".join(completed.stderr.split())[:4096]
        raise click.ClickException(f"gh failed: {diagnostic}")
    if len(completed.stdout.encode("utf-8")) > _MAX_GH_OUTPUT_BYTES:
        raise click.ClickException("gh returned unexpectedly large output")
    return completed


def _parse_issue_url(value: str) -> tuple[str, int, str]:
    """Extract repository, issue number, and hostname from an issue URL."""
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or len(parts) != 4
        or parts[2] != "issues"
    ):
        raise click.ClickException(f"invalid GitHub issue URL: {value}")
    try:
        number = int(parts[3])
    except ValueError as exc:
        raise click.ClickException(f"invalid GitHub issue URL: {value}") from exc
    if number < 1:
        raise click.ClickException(f"invalid GitHub issue URL: {value}")
    return f"{parts[0]}/{parts[1]}", number, parsed.hostname


def _show_status(as_json: bool) -> None:
    """Show daemon health and recent issue watches."""
    store = WatchStore()
    status = store.watcher_status()
    healthy = _watcher_is_healthy(status)
    watches = store.all_watches()
    if as_json:
        click.echo(
            json.dumps(
                {
                    "running": healthy,
                    "pid": status.pid if status and healthy else None,
                    "watches": [_watch_json(watch) for watch in watches],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    state = f"running (PID {status.pid})" if status and healthy else "stopped"
    click.echo(f"Watcher: {state}")
    for watch in watches:
        click.echo(
            f"{watch.watch_id[:8]}  {watch.status:<10}  "
            f"{watch.repository}#{watch.issue_number}"
        )


def _stop_watcher(store: WatchStore) -> None:
    """Stop the exact watcher process recorded in durable state."""
    status = store.watcher_status()
    if not _watcher_is_healthy(status):
        click.echo("Watcher is not running.")
        return
    assert status is not None
    os.kill(status.pid, signal.SIGTERM)
    deadline = time.monotonic() + _DAEMON_STOP_SECONDS
    while time.monotonic() < deadline:
        if process_identity(status.pid) != status.process_identity:
            click.echo("Watcher stopped.")
            return
        time.sleep(0.05)
    raise click.ClickException("watcher did not stop after SIGTERM")


def _ensure_watcher(store: WatchStore) -> bool:
    """Start one detached watcher and wait for its certified heartbeat."""
    if _watcher_is_healthy(store.watcher_status()):
        return False
    log = open(store.log_path, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "claude_code_tools.github_watch_daemon"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()
    deadline = time.monotonic() + _DAEMON_START_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            if _watcher_is_healthy(store.watcher_status()):
                return False
            raise RuntimeError(f"watcher exited with status {process.returncode}")
        status = store.watcher_status()
        if (
            status is not None
            and status.pid == process.pid
            and _watcher_is_healthy(status)
        ):
            return True
        if _watcher_is_healthy(status):
            return False
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=2)
    raise RuntimeError("watcher did not publish a healthy heartbeat")


def _watcher_is_healthy(status: object) -> bool:
    """Return whether durable status still identifies its exact process."""
    if status is None:
        return False
    identity = getattr(status, "process_identity", None)
    pid = getattr(status, "pid", None)
    heartbeat = getattr(status, "heartbeat_at", None)
    if not isinstance(pid, int) or process_identity(pid) != identity:
        return False
    if not isinstance(heartbeat, str):
        return False
    try:
        observed = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - observed.astimezone(UTC)).total_seconds() < 20


def _watch_json(watch: IssueWatch) -> dict[str, object]:
    """Project one watch to stable status JSON."""
    return {
        "id": watch.watch_id,
        "repository": watch.repository,
        "issueNumber": watch.issue_number,
        "issueUrl": watch.issue_url,
        "status": watch.status,
        "attempts": watch.attempts,
        "commentUrl": watch.comment_url,
        "deliveredAt": watch.delivered_at,
        "lastError": watch.last_error,
    }


def main() -> None:
    """Run the command-line interface."""
    cli()


if __name__ == "__main__":
    main()
