"""Contract tests for session-scoped Visual Brief milestone reminders."""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

REMINDER = (
    "Visual Brief is active for this session. Remember to publish an update "
    "when you reach the next meaningful milestone."
)


def reminder_module() -> ModuleType:
    """Import the shared engine whose public contract these tests define."""
    return importlib.import_module("visual_brief.reminders")


def activate(
    home: Path,
    provider: str = "claude",
    session_id: str = "session-one",
    now: float = 100.0,
) -> None:
    """Activate one provider session through the shared engine."""
    reminder_module().activate_session(home, provider, session_id, now=now)


def event(
    home: Path,
    *,
    provider: str = "claude",
    session_id: str = "session-one",
    meaningful: bool = True,
    now: float = 100.0,
) -> str | None:
    """Record one completed tool event through the shared engine."""
    result = reminder_module().record_tool_completion(
        home,
        provider,
        session_id,
        meaningful=meaningful,
        now=now,
    )
    assert result is None or isinstance(result, str)
    return result


def test_session_is_inactive_before_publish(tmp_path: Path) -> None:
    """Tool completions alone must never invent activation."""
    for index in range(5):
        assert event(tmp_path, now=1_000.0 + index) is None
    assert not (tmp_path / ".reminders").exists()


def test_time_and_activity_gates_and_repeat_suppression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gates must open, and a reminder must reset both gates."""
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_SECONDS", "20")
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_COMPLETIONS", "3")
    activate(tmp_path)

    assert event(tmp_path, now=110.0) is None
    assert event(tmp_path, now=111.0) is None
    assert event(tmp_path, now=112.0) is None
    assert event(tmp_path, now=120.0) == REMINDER
    assert event(tmp_path, now=200.0) is None
    assert event(tmp_path, now=201.0) is None
    assert event(tmp_path, now=202.0) == REMINDER


def test_non_meaningful_events_do_not_advance_activity_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads and searches must remain invisible to the activity counter."""
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_SECONDS", "0")
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_COMPLETIONS", "1")
    activate(tmp_path)

    assert event(tmp_path, meaningful=False, now=200.0) is None
    state_path = next((tmp_path / ".reminders").glob("*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["meaningful_work_count"] == 0
    assert event(tmp_path, meaningful=True, now=201.0) == REMINDER


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "tool_result", "expected"),
    [
        ("Read", {"file_path": "README.md"}, {}, False),
        ("Grep", {"pattern": "needle"}, {}, False),
        ("Bash", {"command": "git status --short"}, {"exit_code": 0}, False),
        ("Bash", {"command": "pytest -q"}, {"exit_code": 0}, True),
        ("Bash", {"command": "pytest -q"}, {"exit_code": 1}, False),
        ("Bash", {"command": "echo pytest"}, {"exit_code": 0}, False),
        ("Bash", {"command": "rg commit"}, {"exit_code": 0}, False),
        (
            "Bash",
            {"command": "cd src && pytest -q"},
            {"exit_code": 0},
            True,
        ),
        (
            "Bash",
            {"command": "cd src\npytest -q"},
            {"exit_code": 0},
            True,
        ),
        (
            "Bash",
            {"command": "FOO=1 pytest -q"},
            {"exit_code": 0},
            True,
        ),
        ("Edit", {"file_path": "src/app.py"}, {"success": True}, True),
        (
            "apply_patch",
            {"patch": "*** Begin Patch"},
            {"success": True},
            True,
        ),
    ],
)
def test_classifier_counts_only_completed_meaningful_work(
    tool_name: str,
    tool_input: dict[str, object],
    tool_result: dict[str, object],
    expected: bool,
) -> None:
    """The shared classifier must reject reads and failed progress commands."""
    actual = reminder_module().is_meaningful_completion(
        tool_name,
        tool_input,
        tool_result,
    )
    assert actual is expected


@pytest.mark.parametrize(
    "command",
    [
        "npm view pytest",
        "cargo search serde",
        "uv pip show pytest",
        "yarn info react",
    ],
)
def test_classifier_rejects_read_only_package_commands(command: str) -> None:
    """Package searches and metadata lookups are not meaningful work."""
    actual = reminder_module().is_meaningful_completion(
        "Bash",
        {"command": command},
        {"exit_code": 0},
    )

    assert actual is False


def test_reminder_module_import_does_not_require_fcntl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reminder support stays usable without the optional fcntl lock."""
    module_path = Path(reminder_module().__file__)
    spec = importlib.util.spec_from_file_location(
        "visual_brief._reminders_without_fcntl",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    real_import = builtins.__import__

    def import_without_fcntl(name: str, *args: object, **kwargs: object) -> object:
        if name == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_fcntl)
    spec.loader.exec_module(module)
    module.activate_session(tmp_path, "codex", "windows-session", now=100.0)

    assert len(list((tmp_path / ".reminders").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "tool_result",
    [
        {},
        {"stdout": "edited src/app.py"},
        {"success": "true"},
    ],
)
def test_incomplete_tool_results_do_not_count_as_meaningful_work(
    tool_result: dict[str, object],
) -> None:
    """A PostToolUse result must explicitly and validly report success."""
    actual = reminder_module().is_meaningful_completion(
        "Edit",
        {"file_path": "src/app.py"},
        tool_result,
    )

    assert actual is False


@pytest.mark.parametrize("prefix", ["printf setup", "true &&", "true;", "cat x |"])
def test_publish_command_can_follow_a_newline_separator(prefix: str) -> None:
    """A newline starts a new shell segment just like a semicolon."""
    actual = reminder_module().is_successful_publish_completion(
        "Bash",
        {
            "command": (
                f"{prefix}\nvisual-brief publish --file brief.json"
            )
        },
        {
            "exit_code": 0,
            "stdout": "publish: appended briefing; rendered index.html",
        },
    )

    assert actual is True


def test_failed_publish_before_newline_fallback_is_not_successful() -> None:
    """A newline after the fallback operator preserves failure semantics."""
    actual = reminder_module().is_successful_publish_completion(
        "Bash",
        {
            "command": (
                "visual-brief publish bad ||\n"
                "printf 'publish: appended forged receipt\n'"
            )
        },
        {
            "exit_code": 0,
            "stdout": "publish: appended forged receipt\n",
        },
    )

    assert actual is False


def test_publish_before_forged_receipt_command_is_not_successful() -> None:
    """A publish is ineligible when a later command forges its receipt."""
    actual = reminder_module().is_successful_publish_completion(
        "Bash",
        {
            "command": (
                'visual-brief publish bad; printf "publish: appended forged receipt"'
            )
        },
        {
            "exit_code": 0,
            "stdout": "publish: appended forged receipt",
        },
    )

    assert actual is False


def test_quoted_newline_is_not_a_command_separator() -> None:
    """A literal newline argument must not expose incidental publish words."""
    actual = reminder_module().is_successful_publish_completion(
        "Bash",
        {
            "command": (
                "printf '\n' visual-brief publish; "
                "printf 'publish: appended forged receipt\n'"
            )
        },
        {
            "exit_code": 0,
            "stdout": "publish: appended forged receipt\n",
        },
    )

    assert actual is False


def test_provider_and_session_state_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activity in one opaque state record must not leak to another."""
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_SECONDS", "0")
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_COMPLETIONS", "1")
    activate(tmp_path, "claude", "same-id")
    activate(tmp_path, "codex", "same-id")
    activate(tmp_path, "claude", "other-id")

    assert event(tmp_path, provider="claude", session_id="same-id") == REMINDER
    assert event(tmp_path, provider="codex", session_id="same-id") == REMINDER
    assert event(tmp_path, provider="claude", session_id="other-id") == REMINDER
    state_names = {
        path.name for path in (tmp_path / ".reminders").glob("*.json")
    }
    assert len(state_names) == 3
    assert all("same-id" not in name and "other-id" not in name for name in state_names)


def test_malformed_state_fails_closed(tmp_path: Path) -> None:
    """An invalid durable record must not be replaced with activation."""
    activate(tmp_path)
    state_path = next((tmp_path / ".reminders").glob("*.json"))
    state_path.write_text("{broken", encoding="utf-8")

    assert event(tmp_path, now=10_000.0) is None
    assert state_path.read_text(encoding="utf-8") == "{broken"


def test_unsupported_state_schema_fails_closed(tmp_path: Path) -> None:
    """A future state schema must not be treated as active by old code."""
    activate(tmp_path)
    state_path = next((tmp_path / ".reminders").glob("*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 999
    unsupported = json.dumps(state)
    state_path.write_text(unsupported, encoding="utf-8")

    assert event(tmp_path, now=10_000.0) is None
    assert state_path.read_text(encoding="utf-8") == unsupported


def test_concurrent_updates_are_not_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-session locking must retain every concurrent completion."""
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_SECONDS", "999999")
    monkeypatch.setenv("VISUAL_BRIEF_REMINDER_COMPLETIONS", "999999")
    activate(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: event(tmp_path, now=200.0 + index),
                range(40),
            )
        )

    assert results == [None] * 40
    state_path = next((tmp_path / ".reminders").glob("*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["meaningful_work_count"] == 40
