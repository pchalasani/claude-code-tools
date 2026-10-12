"""Tests for github-wake delivery through Codex's native `codex queue`."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest import MonkeyPatch

import claude_code_tools
from claude_code_tools.github_wake import _ensure_watcher, cli
from claude_code_tools.github_watch_daemon import GitHubWatchDaemon
from claude_code_tools.github_watch_store import IssueWatch, WatchStore
from claude_code_tools.issue_reply_delivery import (
    DeliveryConfigurationError,
    IssueReply,
    _codex_home_for_thread,
    _codex_notification_message,
    deliver_issue_reply,
)
from tests.test_github_wake import _fake_gh

_THREAD = "019a0000-1111-7222-8333-444455556666"


def _fake_codex(directory: Path, queue_output: str | None = None) -> Path:
    """Create a codex executable that logs queue calls and prints a receipt."""
    output = (
        queue_output
        if queue_output is not None
        else "Queued message 019a0000-aaaa-7bbb-8ccc-ddddeeeeffff for thread {thread}."
    )
    executable = directory / "codex"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["queue", "--help"]:
    print("Usage: codex queue [OPTIONS] --thread <THREAD> --message <TEXT>")
    raise SystemExit(0)
if args[:2] == ["queue", "--thread"] and args[3] == "--message":
    log = Path(os.environ["FAKE_CODEX_LOG"])
    log.write_text(json.dumps({{"args": args, "home": os.environ["CODEX_HOME"]}}))
    print({output!r}.format(thread=args[2]))
    raise SystemExit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
print(f"unexpected codex arguments: {{args}}", file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _codex_home(directory: Path, thread_id: str = _THREAD) -> Path:
    """Create a Codex account directory that owns one session rollout."""
    sessions = directory / "sessions/2026/10/12"
    sessions.mkdir(parents=True)
    (sessions / f"rollout-2026-10-12T10-00-00-{thread_id}.jsonl").touch()
    return directory


def _queue_watch(tmp_path: Path, executable: Path) -> IssueWatch:
    """Build one pending native-queue watch."""
    return IssueWatch(
        watch_id="watch",
        repository="o/r",
        issue_number=42,
        issue_url="https://github.com/o/r/issues/42",
        target_kind="codex-queue-v1",
        target={
            "codexHome": str(tmp_path / "home"),
            "executable": str(executable),
            "threadId": _THREAD,
        },
        registered_at="2026-08-14T21:00:00+00:00",
        github_host="github.com",
        github_config_dir=None,
        status="pending",
        attempts=0,
    )


_REPLY = IssueReply(
    comment_id=101,
    issue_number=42,
    url="https://github.com/o/r/issues/42#issuecomment-101",
    author="octocat",
    body="looks good",
    created_at="2026-08-14T21:01:00Z",
)


def test_registration_requires_codex_thread_before_github_access(
    tmp_path: Path,
) -> None:
    """A shell outside a Codex session fails before invoking GitHub."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["https://github.com/owner/repository/issues/42"],
        env={
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": "",
        },
    )

    assert result.exit_code != 0
    assert "requires a Codex session shell" in result.output
    assert not (tmp_path / "state").exists()


def test_registration_rejects_codex_home_without_this_thread(
    tmp_path: Path,
) -> None:
    """A wrong account directory is refused instead of failing at delivery."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    _fake_codex(binary_dir)
    other = _codex_home(tmp_path / "other", "019a9999-1111-7222-8333-444455556666")

    result = CliRunner().invoke(
        cli,
        ["--codex-home", str(other), "https://github.com/owner/repository/issues/42"],
        env={
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": _THREAD,
        },
    )

    assert result.exit_code != 0
    assert "--codex-home" in result.output
    assert not (tmp_path / "state").exists()


def test_registration_rejects_codex_without_queue(tmp_path: Path) -> None:
    """An older Codex CLI without `codex queue` cannot arm a wakeup."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    old = binary_dir / "codex"
    old.write_text("#!/bin/sh\necho 'error: unrecognized subcommand' >&2\nexit 2\n")
    old.chmod(0o755)

    result = CliRunner().invoke(
        cli,
        ["https://github.com/owner/repository/issues/42"],
        env={
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": _THREAD,
            "CODEX_HOME": str(_codex_home(tmp_path / "home")),
        },
    )

    assert result.exit_code != 0
    assert "does not support 'codex queue" in result.output


def test_codex_home_conflicts_with_claude_monitor(tmp_path: Path) -> None:
    """The Codex account option is meaningless for Claude delivery."""
    result = CliRunner().invoke(
        cli,
        [
            "--claude-monitor",
            "--codex-home",
            str(tmp_path),
            "https://github.com/owner/repository/issues/42",
        ],
    )

    assert result.exit_code == 2
    assert "--codex-home applies only" in result.output


def test_registers_verified_existing_issue(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The command verifies and registers a URL without creating an issue."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    codex = _fake_codex(binary_dir)
    account = _codex_home(tmp_path / "account")
    monkeypatch.setattr(
        "claude_code_tools.github_wake._ensure_watcher",
        lambda _store: True,
    )

    result = CliRunner().invoke(
        cli,
        [
            "--codex-home",
            str(account),
            "https://github.com/owner/repository/issues/42",
        ],
        env={
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": _THREAD.upper(),
            "CODEX_HOME": str(tmp_path / "ignored"),
        },
    )

    assert result.exit_code == 0, result.output
    assert "Reply wakeup armed" in result.output
    watch = WatchStore(
        tmp_path / "state/claude-code-tools/github-watch"
    ).all_watches()[0]
    assert watch.issue_number == 42
    assert watch.issue_url == "https://github.com/owner/repository/issues/42"
    assert watch.target_kind == "codex-queue-v1"
    assert watch.target == {
        "codexHome": str(account.resolve()),
        "executable": str(codex),
        "threadId": _THREAD,
    }


def test_registration_defaults_to_codex_home_environment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Without --codex-home the session's own CODEX_HOME is recorded."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    _fake_codex(binary_dir)
    account = _codex_home(tmp_path / "account")
    monkeypatch.setattr(
        "claude_code_tools.github_wake._ensure_watcher",
        lambda _store: True,
    )

    result = CliRunner().invoke(
        cli,
        ["https://github.com/owner/repository/issues/42"],
        env={
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": _THREAD,
            "CODEX_HOME": str(account),
        },
    )

    assert result.exit_code == 0, result.output
    watch = WatchStore(
        tmp_path / "state/claude-code-tools/github-watch"
    ).all_watches()[0]
    assert watch.target["codexHome"] == str(account.resolve())


def test_codex_home_discovery_finds_unique_account_with_thread(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Without CODEX_HOME the one .codex* account holding the thread wins."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".codex").mkdir()
    account = _codex_home(tmp_path / ".codex-work")

    assert _codex_home_for_thread(_THREAD, None, tmp_path) == account.resolve()


def test_codex_home_discovery_refuses_ambiguous_or_missing_thread(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Discovery fails closed instead of guessing an account."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".codex").mkdir()

    with pytest.raises(DeliveryConfigurationError, match="no account directory"):
        _codex_home_for_thread(_THREAD, None, tmp_path)

    _codex_home(tmp_path / ".codex-a")
    _codex_home(tmp_path / ".codex-b")
    with pytest.raises(DeliveryConfigurationError, match="several account"):
        _codex_home_for_thread(_THREAD, None, tmp_path)


def test_codex_queue_delivery_runs_native_queue_under_account(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Delivery runs `codex queue` for the thread with the recorded CODEX_HOME."""
    log = tmp_path / "codex.json"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "wrong"))
    watch = _queue_watch(tmp_path, _fake_codex(tmp_path))

    deliver_issue_reply(watch, _REPLY)

    call = json.loads(log.read_text())
    assert call["home"] == str(tmp_path / "home")
    assert call["args"][:4] == ["queue", "--thread", _THREAD, "--message"]
    assert call["args"][4] == _codex_notification_message(watch, _REPLY)


def test_codex_queue_delivery_requires_acceptance(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A zero exit without Codex's receipt is not a delivery."""
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.json"))
    watch = _queue_watch(tmp_path, _fake_codex(tmp_path, queue_output=""))

    with pytest.raises(RuntimeError, match="no acceptance confirmation"):
        deliver_issue_reply(watch, _REPLY)


def test_codex_queue_delivery_reports_queue_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A failed `codex queue` raises so the daemon retries the reply."""
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.json"))
    monkeypatch.setenv("FAKE_CODEX_EXIT", "1")
    watch = _queue_watch(tmp_path, _fake_codex(tmp_path))

    with pytest.raises(RuntimeError, match="codex queue failed"):
        deliver_issue_reply(watch, _REPLY)


def test_unsupported_adapter_fails_watch_instead_of_retrying(
    tmp_path: Path,
) -> None:
    """A watch armed for a removed adapter becomes terminal on delivery."""
    store = WatchStore(tmp_path / "state")
    watch = store.add_watch(
        "o/r",
        42,
        "https://github.com/o/r/issues/42",
        "codex-app-server-v1",
        {"threadId": _THREAD, "endpoint": "unix:///tmp/app.sock"},
        "github.com",
        None,
    )

    delivered = GitHubWatchDaemon(store)._deliver(watch, _REPLY)

    assert delivered is False
    stored = store.all_watches()[0]
    assert stored.status == "failed"
    assert "re-arm" in (stored.last_error or "")
    assert store.pending_watches() == []


def test_registration_records_absolute_codex_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A relative PATH hit is stored absolute for the watcher's other cwd."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _fake_gh(binary_dir)
    codex = _fake_codex(binary_dir)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "claude_code_tools.github_wake._ensure_watcher",
        lambda _store: True,
    )

    result = CliRunner().invoke(
        cli,
        ["https://github.com/owner/repository/issues/42"],
        env={
            "PATH": f"bin:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CODEX_THREAD_ID": _THREAD,
            "CODEX_HOME": str(_codex_home(tmp_path / "account")),
        },
    )

    assert result.exit_code == 0, result.output
    watch = WatchStore(
        tmp_path / "state/claude-code-tools/github-watch"
    ).all_watches()[0]
    assert watch.target["executable"] == str(codex)


def _start_old_watcher(state: Path) -> subprocess.Popen[bytes]:
    """Run a real watcher that reports an older claude-code-tools version."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import claude_code_tools; claude_code_tools.__version__ = '0.0.0'; "
            "from claude_code_tools.github_watch_daemon import main; main()",
        ],
        env={**os.environ, "XDG_STATE_HOME": str(state)},
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    store = WatchStore(state / "claude-code-tools/github-watch")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = store.watcher_status()
        if status is not None and status.pid == process.pid:
            return process
        time.sleep(0.05)
    process.kill()
    raise AssertionError("old watcher did not start")


def test_start_replaces_old_watcher_that_wins_the_lock_race(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """An old watcher adopted after the new one loses the lock is replaced.

    The old watcher's heartbeat looks stale, so a new watcher is spawned; it
    exits on the held lock while the old one refreshes its heartbeat.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    old = _start_old_watcher(state)
    store = WatchStore(state / "claude-code-tools/github-watch")
    old_status = store.watcher_status()
    assert old_status is not None
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE watcher_status SET heartbeat_at = '2000-01-01T00:00:00+00:00'"
        )
    real_popen = subprocess.Popen
    calls: list[list[str]] = []

    def racing_popen(args: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        calls.append(args)
        if len(calls) == 1:
            store.heartbeat(
                old_status.instance_id,
                old_status.pid,
                old_status.process_identity,
                old_status.started_at,
                "0.0.0",
            )
            return real_popen([sys.executable, "-c", "pass"])
        return real_popen(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("claude_code_tools.github_wake.subprocess.Popen", racing_popen)
    try:
        assert _ensure_watcher(store) is True
        old.wait(timeout=5)
        new = store.watcher_status()
        assert new is not None
        assert new.pid != old.pid
        assert new.code_version == claude_code_tools.__version__
        assert len(calls) == 2
    finally:
        old.kill()
        CliRunner().invoke(cli, ["--stop"])
