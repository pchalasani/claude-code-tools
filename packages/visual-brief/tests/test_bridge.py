"""Focused tests for the shared Visual Brief agent bridge."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from visual_brief import bridge
from visual_brief.bridge import follow_questions, resolve_codex_target
from visual_brief.server.queue import (
    MAX_QUEUE_RECORD_BYTES,
    build_question_record,
    build_signal_record,
)
from visual_brief.writes.runfiles import CliError


def _question(text: str, message_id: str | None = "message-1") -> dict[str, object]:
    """Build one queue-shaped test question."""
    record: dict[str, object] = {
        "anchor_id": "created",
        "parent_id": None,
        "text": text,
        "timestamp": "2026-08-04T12:00:00Z",
        "type": "question",
    }
    if message_id is not None:
        record["message_id"] = message_id
    return record


def _append(path: Path, record: dict[str, object]) -> None:
    """Append one complete test record."""
    with path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(record) + "\n")
        queue.flush()


def _stub_codex_records(
    monkeypatch: pytest.MonkeyPatch,
    *records: dict[str, Any],
) -> None:
    """Replace the durable follower when a test only concerns delivery."""
    positioned = [
        (record, bridge._QueueCursor(device=1, inode=1, offset=index))
        for index, record in enumerate(records, start=1)
    ]

    def follow(
        run_dir: Path,
        queue_path: Path,
        poll_interval: float = bridge.POLL_INTERVAL_SECONDS,
    ) -> tuple[
        Iterator[tuple[dict[str, Any], bridge._QueueCursor]],
        Path,
        bridge._CodexQueueState,
    ]:
        del queue_path, poll_interval
        acknowledged = bridge._QueueCursor(device=1, inode=1, offset=0)
        return (
            iter(positioned),
            run_dir / bridge.CODEX_CURSOR_FILE,
            bridge._CodexQueueState(acknowledged),
        )

    monkeypatch.setattr(bridge, "_follow_codex_questions", follow)


def _make_run(tmp_path: Path) -> tuple[Path, Path]:
    """Create the run files needed by a bridge watcher."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    queue_path = run_dir / "questions.jsonl"
    queue_path.touch()
    (run_dir / "meta.json").write_text(
        json.dumps({"instance_id": "instance-1"}),
        encoding="utf-8",
    )
    return run_dir, queue_path


def _limit_real_codex_records(
    monkeypatch: pytest.MonkeyPatch,
    limits: list[int],
) -> None:
    """Make each real durable follower stop after a deterministic count."""
    original = bridge._follow_codex_questions
    remaining = iter(limits)

    def follow(
        run_dir: Path,
        queue_path: Path,
        poll_interval: float = bridge.POLL_INTERVAL_SECONDS,
    ) -> tuple[
        Iterator[tuple[dict[str, Any], bridge._QueueCursor]],
        Path,
        bridge._CodexQueueState,
    ]:
        del poll_interval
        records, cursor_path, state = original(
            run_dir,
            queue_path,
            poll_interval=0.001,
        )
        return islice(records, next(remaining)), cursor_path, state

    monkeypatch.setattr(bridge, "_follow_codex_questions", follow)


def test_follower_starts_at_end_and_follows_replacement(tmp_path: Path) -> None:
    """Ignore history, emit appends, and reopen a replaced queue at its start."""
    queue = tmp_path / "questions.jsonl"
    _append(queue, _question("old", "old-id"))
    records = follow_questions(queue, poll_interval=0.001)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(next, records)
        _append(queue, _question("appended"))
        assert first.result(timeout=2)["text"] == "appended"

        second = executor.submit(next, records)
        replacement = tmp_path / "replacement.jsonl"
        _append(replacement, _question("replacement", "message-2"))
        replacement.replace(queue)
        assert second.result(timeout=2)["text"] == "replacement"
    records.close()


def test_follower_recovers_after_in_place_truncation(tmp_path: Path) -> None:
    """Seek to the beginning when the open queue is truncated in place."""
    queue = tmp_path / "questions.jsonl"
    _append(queue, _question("history that is longer than the new record"))
    records = follow_questions(queue, poll_interval=0.001)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(next, records)
        queue.write_text(
            json.dumps(_question("new", "message-2")) + "\n",
            encoding="utf-8",
        )
        assert pending.result(timeout=2)["text"] == "new"
    records.close()


def test_follower_detects_truncation_that_regrows_past_the_old_offset(
    tmp_path: Path,
) -> None:
    """Do not skip a rewritten first record merely because the file regrew."""
    queue = tmp_path / "questions.jsonl"
    _append(queue, _question("old", "old-id"))
    records = follow_questions(queue, poll_interval=0.001)
    queue.write_text(
        json.dumps(_question("new " + "x" * 1_000, "new-id")) + "\n",
        encoding="utf-8",
    )

    record = next(records)
    records.close()

    assert record["message_id"] == "new-id"


def test_follower_discards_oversized_complete_record(tmp_path: Path) -> None:
    """Discard an oversized complete line and emit the next valid record."""
    queue = tmp_path / "questions.jsonl"
    queue.touch()
    records = follow_questions(queue, poll_interval=0.001)
    oversized = b"x" * (MAX_QUEUE_RECORD_BYTES + 1) + b"\n"

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(next, records)
        with queue.open("ab") as stream:
            stream.write(oversized)
            stream.write(json.dumps(_question("valid")).encode() + b"\n")
            stream.flush()
        assert pending.result(timeout=2)["text"] == "valid"
    records.close()


def test_follower_discards_oversized_unterminated_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep discarding an oversized partial line until its eventual newline."""
    queue = tmp_path / "questions.jsonl"
    queue.touch()
    records = follow_questions(queue, poll_interval=0.001)
    with queue.open("ab") as stream:
        stream.write(b"x" * (MAX_QUEUE_RECORD_BYTES + 1))
        stream.flush()
    reached_end = Event()

    def mark_reached_end(_: float) -> None:
        reached_end.set()

    monkeypatch.setattr(bridge.time, "sleep", mark_reached_end)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(next, records)
        assert reached_end.wait(timeout=2)
        with queue.open("ab") as stream:
            stream.write(b"\n")
            stream.write(json.dumps(_question("valid")).encode() + b"\n")
            stream.flush()
        assert pending.result(timeout=2)["text"] == "valid"
    records.close()


def test_follower_accepts_legacy_record_without_message_id(tmp_path: Path) -> None:
    """Old queue records remain readable by the shared follower."""
    queue = tmp_path / "questions.jsonl"
    queue.touch()
    records = follow_questions(queue, poll_interval=0.001)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(next, records)
        _append(queue, _question("legacy", None))
        assert pending.result(timeout=2) == _question("legacy", None)
    records.close()


def test_question_builder_adds_stable_opaque_message_id() -> None:
    """A newly accepted browser question carries its queue identity."""
    record = build_question_record(
        {"anchor_id": "created", "parent_id": None, "text": "hello"}
    )

    assert isinstance(record["message_id"], str)
    assert len(record["message_id"]) == 32


def test_codex_first_watch_starts_at_current_eof(tmp_path: Path) -> None:
    """Persist the initial end and ignore queue history on first watch."""
    run_dir, queue_path = _make_run(tmp_path)
    _append(queue_path, _question("history", "old-id"))

    records, cursor_path, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    starting = json.loads(cursor_path.read_text(encoding="utf-8"))
    _append(queue_path, _question("new", "new-id"))
    record, cursor = next(records)
    records.close()

    assert starting["offset"] > 0
    assert record["text"] == "new"
    assert cursor.offset == queue_path.stat().st_size


def test_codex_extends_one_prefix_digest_as_records_arrive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not reread the growing queue prefix after every question."""
    run_dir, queue_path = _make_run(tmp_path)
    original = bridge._prefix_digest
    prefix_lengths: list[int] = []

    def capture_prefix(descriptor: int, length: int) -> bridge._Digest:
        prefix_lengths.append(length)
        return original(descriptor, length)

    monkeypatch.setattr(bridge, "_prefix_digest", capture_prefix)
    records, _, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    _append(queue_path, _question("first", "first-id"))
    _append(queue_path, _question("second", "second-id"))

    first = next(records)
    second = next(records)
    records.close()

    assert first[1].prefix_sha256 is not None
    assert second[1].prefix_sha256 is not None
    assert prefix_lengths == [0]


def test_codex_captures_question_appended_during_target_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open and position the queue before checking the Codex target."""
    run_dir, queue_path = _make_run(tmp_path)
    _limit_real_codex_records(monkeypatch, [1])

    calls: list[tuple[str, str | None, str | None]] = []

    def capture_helper(
        helper: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        del helper, run_id, instance_id, thread_id, endpoint
        del initial_attempts, maximum_attempts
        calls.append((command, message_id, text))
        if command == "check":
            _append(queue_path, _question("during validation", "new-id"))

    monkeypatch.setattr(bridge, "_invoke_codex_helper", capture_helper)

    assert (
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
        == 0
    )
    assert calls == [
        ("check", None, None),
        ("deliver", "new-id", "during validation"),
    ]


def test_codex_restart_replays_failed_and_later_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the durable cursor unchanged until delivery succeeds."""
    run_dir, queue_path = _make_run(tmp_path)
    _limit_real_codex_records(monkeypatch, [1, 0, 1])
    check_count = 0
    fail_delivery = True
    delivered: list[str] = []
    attempt_bounds: list[tuple[int, int]] = []

    def helper(
        helper_path: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        nonlocal check_count, fail_delivery
        del helper_path, run_id, instance_id, thread_id, endpoint, message_id
        if command == "check":
            check_count += 1
            if check_count == 1:
                _append(queue_path, _question("failed", "failed-id"))
                _append(queue_path, _question("later", "later-id"))
            return
        attempt_bounds.append((initial_attempts, maximum_attempts))
        if initial_attempts:
            assert text == "failed"
        if fail_delivery:
            fail_delivery = False
            raise CliError("simulated delivery failure")
        assert text is not None
        delivered.append(text)

    monkeypatch.setattr(bridge, "_invoke_codex_helper", helper)

    with pytest.raises(CliError, match="simulated delivery failure"):
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
    cursor_path = run_dir / bridge.CODEX_CURSOR_FILE
    failed_cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert failed_cursor["offset"] == 0
    assert failed_cursor["pending"]["ambiguous"] is True
    assert failed_cursor["pending"]["message_id"] == "failed-id"
    assert failed_cursor["pending"]["text"] == "failed"
    assert failed_cursor["pending"]["attempts"] == 1
    assert failed_cursor["pending"]["thread_id"] == "thread-1"
    assert failed_cursor["pending"]["endpoint"] == "unix:///tmp/server.sock"

    with pytest.raises(CliError, match="still pending for Codex thread thread-1"):
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-2",
            "unix:///tmp/server.sock",
        )

    assert (
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
        == 0
    )
    assert delivered == ["failed", "later"]
    assert attempt_bounds == [(0, 1), (1, 2), (0, 1)]
    resumed_cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert resumed_cursor["offset"] == queue_path.stat().st_size


def test_codex_restart_reads_replacement_from_zero(tmp_path: Path) -> None:
    """Discard an old inode offset when the queue file is replaced."""
    run_dir, queue_path = _make_run(tmp_path)
    records, cursor_path, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    _append(queue_path, _question("acknowledged", "old-id"))
    _, cursor = next(records)
    bridge._write_codex_state(cursor_path, bridge._CodexQueueState(cursor))
    records.close()

    replacement = run_dir / "replacement.jsonl"
    _append(replacement, _question("replacement", "replacement-id"))
    replacement.replace(queue_path)
    resumed, _, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    record, _ = next(resumed)
    resumed.close()

    assert record["text"] == "replacement"


def test_codex_restart_reads_truncated_queue_from_zero(tmp_path: Path) -> None:
    """Discard an offset beyond a queue shortened in place."""
    run_dir, queue_path = _make_run(tmp_path)
    records, cursor_path, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    _append(queue_path, _question("x" * 1_000, "long-id"))
    _, cursor = next(records)
    bridge._write_codex_state(cursor_path, bridge._CodexQueueState(cursor))
    records.close()

    old_inode = queue_path.stat().st_ino
    queue_path.write_text(
        json.dumps(_question("short", "short-id")) + "\n",
        encoding="utf-8",
    )
    assert queue_path.stat().st_ino == old_inode
    resumed, _, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    record, _ = next(resumed)
    resumed.close()

    assert record["text"] == "short"


def test_codex_restart_detects_rewritten_prefix_at_same_or_larger_size(
    tmp_path: Path,
) -> None:
    """Validate saved bytes when same-inode rewrite remains large enough."""
    run_dir, queue_path = _make_run(tmp_path)
    records, cursor_path, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    _append(queue_path, _question("old", "old-id"))
    _, cursor = next(records)
    bridge._write_codex_state(cursor_path, bridge._CodexQueueState(cursor))
    records.close()

    old_inode = queue_path.stat().st_ino
    replacement = _question("new prefix " + "x" * 1_000, "new-id")
    queue_path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    assert queue_path.stat().st_ino == old_inode
    assert queue_path.stat().st_size >= cursor.offset

    resumed, _, _ = bridge._follow_codex_questions(
        run_dir,
        queue_path,
        poll_interval=0.001,
    )
    record, _ = next(resumed)
    resumed.close()

    assert record["message_id"] == "new-id"


def test_codex_rejects_malformed_durable_cursor(tmp_path: Path) -> None:
    """Fail safely instead of skipping unread records on bad cursor state."""
    run_dir, queue_path = _make_run(tmp_path)
    cursor_path = run_dir / bridge.CODEX_CURSOR_FILE
    cursor_path.write_text('{"device":true,"inode":1,"offset":0}\n')

    with pytest.raises(CliError, match="malformed Codex queue cursor"):
        bridge._follow_codex_questions(run_dir, queue_path)


def test_codex_restart_preserves_the_submission_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated watcher restarts cannot reset a pending delivery's attempts."""
    run_dir, queue_path = _make_run(tmp_path)
    _limit_real_codex_records(monkeypatch, [1, 0, 0, 0, 0, 0])
    checks = 0
    bounds: list[tuple[int, int]] = []

    def helper(
        helper_path: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        nonlocal checks
        del helper_path, run_id, instance_id, thread_id, endpoint
        del message_id, text
        if command == "check":
            checks += 1
            if checks == 1:
                _append(queue_path, _question("pending", "pending-id"))
            return
        bounds.append((initial_attempts, maximum_attempts))
        raise CliError("simulated ambiguous delivery")

    monkeypatch.setattr(bridge, "_invoke_codex_helper", helper)

    for _ in range(6):
        with pytest.raises(CliError, match="simulated ambiguous delivery"):
            bridge.watch_command(
                "run",
                run_dir,
                "codex",
                "thread-1",
                "unix:///tmp/server.sock",
            )

    assert bounds == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 5)]


def test_codex_restart_reclaims_a_known_unused_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean pre-submission failure cannot strand the pending question."""
    run_dir, queue_path = _make_run(tmp_path)
    _limit_real_codex_records(monkeypatch, [1, 0, 0])
    checks = 0
    bounds: list[tuple[int, int]] = []

    def helper(
        helper_path: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        nonlocal checks
        del helper_path, run_id, instance_id, thread_id, endpoint
        del message_id, text
        if command == "check":
            checks += 1
            if checks == 1:
                _append(queue_path, _question("pending", "pending-id"))
            return
        bounds.append((initial_attempts, maximum_attempts))
        raise bridge._CodexHelperError("connection failed", initial_attempts)

    monkeypatch.setattr(bridge, "_invoke_codex_helper", helper)

    for _ in range(3):
        with pytest.raises(CliError, match="connection failed"):
            bridge.watch_command(
                "run",
                run_dir,
                "codex",
                "thread-1",
                "unix:///tmp/server.sock",
            )

    cursor = json.loads(
        (run_dir / bridge.CODEX_CURSOR_FILE).read_text(encoding="utf-8")
    )
    assert cursor["pending"]["attempts"] == 0
    assert bounds == [(0, 1), (0, 1), (0, 1)]


def test_codex_delivers_an_authored_suggested_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suggested-reply chips carry the same delivery identity as chat."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "questions.jsonl").touch()
    (run_dir / "meta.json").write_text(
        json.dumps({"instance_id": "instance-1"}),
        encoding="utf-8",
    )
    record = build_signal_record(
        {
            "anchor_id": "created",
            "label": "Looks right",
            "text": "This looks right; continue.",
        }
    )
    _stub_codex_records(monkeypatch, record)
    calls: list[tuple[str, str | None, str | None]] = []

    def capture_helper(
        helper: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        del initial_attempts, maximum_attempts
        calls.append((command, message_id, text))

    monkeypatch.setattr(bridge, "_invoke_codex_helper", capture_helper)

    assert (
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
        == 0
    )
    assert calls[1] == (
        "deliver",
        record["message_id"],
        "This looks right; continue.",
    )


def test_codex_watch_accepts_a_legacy_run_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use a legacy run's creation timestamp as its stable identity."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "questions.jsonl").touch()
    (run_dir / "meta.json").write_text(
        json.dumps({"created_at": "2026-07-29T12:00:00Z"}),
        encoding="utf-8",
    )
    _stub_codex_records(monkeypatch, _question("legacy run"))
    identities: list[str] = []

    def capture_helper(
        helper: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        del initial_attempts, maximum_attempts
        identities.append(instance_id)

    monkeypatch.setattr(bridge, "_invoke_codex_helper", capture_helper)

    assert (
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
        == 0
    )
    assert identities == ["2026-07-29T12:00:00Z"] * 2


def test_claude_and_codex_use_the_same_accepted_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both adapters consume the shared follower's record unchanged."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "questions.jsonl").touch()
    (run_dir / "meta.json").write_text(
        json.dumps({"instance_id": "instance-1"}),
        encoding="utf-8",
    )
    record = _question("  preserve me byte-for-byte  ")
    monkeypatch.setattr(bridge, "follow_questions", lambda path: iter([record]))

    assert bridge.watch_command("run", run_dir, "claude", None, None) == 0
    assert json.loads(capsys.readouterr().out) == record

    calls: list[tuple[str, str | None, str | None]] = []
    _stub_codex_records(monkeypatch, record)

    def capture_helper(
        helper: Path,
        command: str,
        run_id: str,
        instance_id: str,
        thread_id: str,
        endpoint: str,
        *,
        message_id: str | None = None,
        text: str | None = None,
        initial_attempts: int = 0,
        maximum_attempts: int = bridge.MAX_CODEX_DELIVERY_ATTEMPTS,
    ) -> None:
        del initial_attempts, maximum_attempts
        calls.append((command, message_id, text))

    monkeypatch.setattr(bridge, "_invoke_codex_helper", capture_helper)
    assert (
        bridge.watch_command(
            "run",
            run_dir,
            "codex",
            "thread-1",
            "unix:///tmp/server.sock",
        )
        == 0
    )
    assert calls == [
        ("check", None, None),
        ("deliver", "message-1", "  preserve me byte-for-byte  "),
    ]


def test_codex_target_uses_explicit_values_then_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit target values override the codex-dynamic environment."""
    monkeypatch.setenv("CODEX_THREAD_ID", "environment-thread")
    monkeypatch.setenv(
        "CCTOOLS_CODEX_CALLBACK_ENDPOINT",
        "unix:///environment.sock",
    )

    assert resolve_codex_target(None, None) == (
        "environment-thread",
        "unix:///environment.sock",
    )
    assert resolve_codex_target(
        "explicit-thread",
        "unix:///explicit.sock",
    ) == ("explicit-thread", "unix:///explicit.sock")


def test_codex_target_rejects_missing_or_nonlocal_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary or remote Codex session fails before queue following."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CCTOOLS_CODEX_CALLBACK_ENDPOINT", raising=False)
    with pytest.raises(CliError, match="codex-dynamic"):
        resolve_codex_target(None, None)
    with pytest.raises(CliError, match="unix://"):
        resolve_codex_target("thread", "ws://localhost/server")


def test_codex_watch_rejects_missing_setup_before_opening_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an ordinary Codex session without creating durable state."""
    run_dir, _ = _make_run(tmp_path)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CCTOOLS_CODEX_CALLBACK_ENDPOINT", raising=False)

    with pytest.raises(CliError, match="codex-dynamic"):
        bridge.watch_command("run", run_dir, "codex", None, None)

    assert not (run_dir / bridge.CODEX_CURSOR_FILE).exists()


def test_generated_helper_is_package_data() -> None:
    """The committed helper lives under the package's static artifacts."""
    helper = bridge._codex_helper_path()

    assert helper.name == "visual-brief-codex.mjs"
    assert helper.is_file()
