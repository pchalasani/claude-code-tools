"""Wake the current agent session when an existing GitHub issue gets a reply."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import click

from claude_code_tools.codex_server_process import process_identity
from claude_code_tools.github_watch_store import IssueWatch, WatchStore
from claude_code_tools.issue_reply_delivery import (
    DeliveryConfigurationError,
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
def cli(
    issue_url: str | None,
    show_status: bool,
    as_json: bool,
    start: bool,
    stop: bool,
    cancel: str | None,
) -> None:
    """Wake this session when ISSUE_URL receives its first future comment."""
    modes = sum((show_status, start, stop, cancel is not None))
    if modes > 1 or (modes and issue_url is not None):
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
    _register(issue_url)


def _register(issue_url: str) -> None:
    """Validate and durably register one existing issue URL."""
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
        raise click.ClickException(
            "the wakeup was durably registered, but the shared watcher did not "
            f"start: {exc}; run 'github-wake --start'"
        ) from exc
    click.echo(f"Reply wakeup armed for {canonical_url} ({watch.watch_id[:8]}).")


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
