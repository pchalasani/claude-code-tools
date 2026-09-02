"""Publish activation and provider PostToolUse adapter contracts."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest

from test_publish import briefing
from visual_brief.writes import CliError, publish_command
from write_support import make_run

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "visual-brief"
REMINDER = (
    "Visual Brief is active for this session. Remember to publish an update "
    "when you reach the next meaningful milestone."
)


def reminder_states(home: Path) -> list[Path]:
    """Return durable reminder records below one configured home."""
    return list((home / ".reminders").glob("*.json"))


def reminder_module() -> ModuleType:
    """Import the shared engine used to prepare existing active state."""
    return importlib.import_module("visual_brief.reminders")


def test_publish_command_never_activates_reminders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing storage must remain independent of hook session state."""
    home = tmp_path / "visual-brief"
    make_run(home)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "ignored-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "also-ignored")

    with pytest.raises(CliError):
        publish_command(home, None, {"not": "a briefing"})
    assert reminder_states(home) == []

    assert publish_command(home, None, briefing()) == 0
    assert reminder_states(home) == []


def test_publish_command_does_not_reset_existing_state(
    tmp_path: Path,
) -> None:
    """Only a successful PostToolUse event may reset reminder state."""
    home = tmp_path / "visual-brief"
    make_run(home)
    engine = reminder_module()
    engine.activate_session(home, "claude", "claude-session", now=100.0)
    engine.record_tool_completion(
        home,
        "claude",
        "claude-session",
        meaningful=True,
        now=101.0,
    )
    state_path = reminder_states(home)[0]
    before_failure = state_path.read_bytes()

    with pytest.raises(CliError):
        publish_command(home, None, {"not": "a briefing"})
    assert state_path.read_bytes() == before_failure

    assert publish_command(home, None, briefing()) == 0
    assert state_path.read_bytes() == before_failure


def test_publish_ignores_unwritable_reminder_state(
    tmp_path: Path,
) -> None:
    """A successful briefing remains successful when activation cannot save."""
    home = tmp_path / "visual-brief"
    make_run(home)
    (home / ".reminders").write_text("not a directory", encoding="utf-8")
    assert publish_command(home, None, briefing()) == 0


def run_adapter(
    provider: str,
    payload: object,
    home: Path,
    *,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one installed-hook source with PostToolUse JSON on stdin."""
    environment = os.environ.copy()
    environment["VISUAL_BRIEF_HOME"] = str(home)
    environment["VISUAL_BRIEF_REMINDER_SECONDS"] = "1"
    environment["VISUAL_BRIEF_REMINDER_COMPLETIONS"] = "1"
    environment.pop("CLAUDE_SESSION_ID", None)
    environment.pop("CODEX_THREAD_ID", None)
    environment.pop("PLUGIN_ROOT", None)
    environment.pop("CLAUDE_PLUGIN_ROOT", None)
    environment.update(environment_overrides or {})
    return subprocess.run(
        [
            "uv",
            "run",
            "--package",
            "visual-brief",
            "visual-brief",
            "reminder-hook",
            "--provider",
            provider,
        ],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


@pytest.mark.parametrize(
    ("provider", "tool_name", "result_key"),
    [
        ("claude", "Edit", "tool_response"),
        ("codex", "apply_patch", "tool_response"),
    ],
)
def test_adapters_normalize_input_and_emit_quiet_context(
    tmp_path: Path,
    provider: str,
    tool_name: str,
    result_key: str,
) -> None:
    """Both provider shapes must produce the exact advisory context."""
    home = tmp_path / provider
    make_run(home)
    assert publish_command(home, None, briefing()) == 0
    reminder_module().activate_session(
        home,
        provider,
        "adapter-session",
        now=time.time() - 2.0,
    )

    payload = {
        "session_id": "adapter-session",
        "tool_name": tool_name,
        "tool_input": {"file_path": "src/app.py"},
        result_key: {"success": True},
    }
    completed = run_adapter(provider, payload, home)

    assert completed.returncode == 0
    assert completed.stderr == ""
    response = json.loads(completed.stdout)
    assert response == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": REMINDER,
        }
    }

    suppressed = run_adapter(provider, payload, home)
    assert suppressed.returncode == 0
    assert json.loads(suppressed.stdout) == {}

    reminder_module().activate_session(
        home,
        provider,
        "adapter-session",
        now=time.time() - 2.0,
    )
    automatic = run_adapter(
        "auto",
        payload,
        home,
        environment_overrides={
            (
                "PLUGIN_ROOT"
                if provider == "codex"
                else "CLAUDE_PLUGIN_ROOT"
            ): str(PLUGIN)
        },
    )
    assert automatic.returncode == 0
    assert json.loads(automatic.stdout) == response


def test_codex_string_test_output_triggers_shared_reminder_gate(
    tmp_path: Path,
) -> None:
    """Real nonempty Codex command output is an explicit success result."""
    reminder_module().activate_session(
        tmp_path,
        "codex",
        "codex-session",
        now=time.time() - 2.0,
    )
    completed = run_adapter(
        "codex",
        {
            "session_id": "codex-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "pytest --version"},
            "tool_response": "pytest 9.0.1\n",
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": REMINDER,
        }
    }


@pytest.mark.parametrize(
    "tool_response",
    [
        "test session starts\nFAILED tests/test_app.py::test_publish\n",
        "Build output\nError: compilation failed\n",
        "test session starts\nERROR collecting tests/test_app.py\n",
    ],
)
def test_codex_error_marker_anywhere_does_not_advance_gate(
    tmp_path: Path,
    tool_response: str,
) -> None:
    """A normal header must not hide a later Codex failure marker."""
    reminder_module().activate_session(
        tmp_path,
        "codex",
        "codex-session",
        now=time.time() - 2.0,
    )
    completed = run_adapter(
        "codex",
        {
            "session_id": "codex-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "pytest -q"},
            "tool_response": tool_response,
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}


def test_codex_publish_receipt_string_activates_session(
    tmp_path: Path,
) -> None:
    """Codex publish activation requires the concrete CLI success receipt."""
    rendered_path = tmp_path / "runs" / "brief" / "index.html"
    completed = run_adapter(
        "codex",
        {
            "session_id": "codex-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "visual-brief publish -"},
            "tool_response": (
                "publish: appended update; rendered "
                f"{rendered_path}\n"
            ),
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    state = json.loads(reminder_states(tmp_path)[0].read_text(encoding="utf-8"))
    assert state["provider"] == "codex"


@pytest.mark.parametrize(
    "tool_response",
    [
        "",
        "Error: command execution failed",
        "visual-brief publish - would run here",
    ],
)
def test_codex_string_without_publish_receipt_does_not_activate(
    tmp_path: Path,
    tool_response: str,
) -> None:
    """Empty, erroneous, or incidental Codex text must fail closed."""
    completed = run_adapter(
        "codex",
        {
            "session_id": "codex-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "visual-brief publish -"},
            "tool_response": tool_response,
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


@pytest.mark.parametrize(
    ("provider", "command", "tool_response"),
    [
        (
            "claude",
            "visual-brief publish bad; true",
            {"success": True, "stdout": "", "stderr": ""},
        ),
        (
            "claude",
            "visual-brief publish bad | true",
            {"exit_code": 0, "stdout": "", "stderr": ""},
        ),
        (
            "codex",
            "visual-brief publish bad; true",
            "command completed successfully\n",
        ),
        (
            "codex",
            "visual-brief publish bad | true",
            "command completed successfully\n",
        ),
    ],
)
def test_masked_publish_failure_without_receipt_does_not_activate(
    tmp_path: Path,
    provider: str,
    command: str,
    tool_response: dict[str, object] | str,
) -> None:
    """Aggregate shell success cannot substitute for the publish receipt."""
    completed = run_adapter(
        provider,
        {
            "session_id": "masked-session",
            "tool_name": "Bash" if provider == "claude" else "exec_command",
            "tool_input": {"command": command},
            "tool_response": tool_response,
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


def test_incidental_inline_publish_receipt_does_not_activate(
    tmp_path: Path,
) -> None:
    """Only a CLI receipt at the start of an output line is authoritative."""
    completed = run_adapter(
        "codex",
        {
            "session_id": "masked-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "visual-brief publish bad; true"},
            "tool_response": "echoed: publish: appended fake receipt\n",
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


def test_forged_receipt_after_publish_does_not_activate(tmp_path: Path) -> None:
    """A later executable command makes an earlier publish ineligible."""
    completed = run_adapter(
        "codex",
        {
            "session_id": "masked-session",
            "tool_name": "exec_command",
            "tool_input": {
                "cmd": (
                    'visual-brief publish bad; '
                    'printf "publish: appended forged receipt"'
                )
            },
            "tool_response": "publish: appended forged receipt",
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


@pytest.mark.parametrize("operator", ["&", "|&"])
def test_forged_receipt_after_background_publish_does_not_activate(
    tmp_path: Path,
    operator: str,
) -> None:
    """Background boundaries isolate a failed publish from forged output."""
    completed = run_adapter(
        "codex",
        {
            "session_id": "background-session",
            "tool_name": "exec_command",
            "tool_input": {
                "cmd": (
                    f"visual-brief publish bad {operator} "
                    'printf "publish: appended forged receipt"'
                )
            },
            "tool_response": "publish: appended forged receipt",
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


def test_successful_publish_pipeline_with_receipt_activates(
    tmp_path: Path,
) -> None:
    """A pipeline remains eligible when it emits the literal CLI receipt."""
    completed = run_adapter(
        "codex",
        {
            "session_id": "pipeline-session",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "visual-brief publish - | tee publish.log"},
            "tool_response": (
                "pipeline setup\n"
                "publish: appended update; rendered index.html\n"
            ),
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert len(reminder_states(tmp_path)) == 1


def test_claude_string_tool_response_remains_invalid(tmp_path: Path) -> None:
    """Claude keeps requiring its canonical object response shape."""
    completed = run_adapter(
        "claude",
        {
            "session_id": "claude-session",
            "tool_name": "Bash",
            "tool_input": {"command": "visual-brief publish -"},
            "tool_response": "publish: appended update; rendered index.html\n",
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_adapters_fail_closed_with_valid_empty_output(
    tmp_path: Path,
    provider: str,
) -> None:
    """Malformed or identity-free events must neither block nor mutate state."""
    malformed = run_adapter(provider, "not-an-object", tmp_path)
    missing_identity = run_adapter(
        provider,
        {"session_id": "", "tool_name": "Edit", "tool_input": {}},
        tmp_path,
    )

    for completed in (malformed, missing_identity):
        assert completed.returncode == 0
        assert json.loads(completed.stdout) == {}
    auto_missing = run_adapter(
        "auto",
        {"session_id": "adapter-session", "tool_name": "Edit"},
        tmp_path,
    )
    for completed in (auto_missing,):
        assert completed.returncode == 0
        assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


@pytest.mark.parametrize(
    ("environment_overrides", "provider"),
    [
        ({"PLUGIN_ROOT": str(PLUGIN)}, "codex"),
        ({"CLAUDE_PLUGIN_ROOT": str(PLUGIN)}, "claude"),
        (
            {
                "PLUGIN_ROOT": str(PLUGIN),
                "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
                "CLAUDE_SESSION_ID": "wrong-signal",
                "CODEX_THREAD_ID": "wrong-signal",
            },
            "codex",
        ),
    ],
)
def test_auto_provider_uses_plugin_root_contract(
    tmp_path: Path,
    environment_overrides: dict[str, str],
    provider: str,
) -> None:
    """Codex PLUGIN_ROOT takes precedence over Claude compatibility root."""
    payload = {
        "session_id": "payload-session",
        "tool_name": "Bash",
        "tool_input": {"command": "visual-brief publish --file brief.json"},
        "tool_response": {
            "exit_code": 0,
            "stdout": "publish: appended briefing; rendered index.html",
        },
    }
    completed = run_adapter(
        "auto",
        payload,
        tmp_path,
        environment_overrides=environment_overrides,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    state = json.loads(reminder_states(tmp_path)[0].read_text(encoding="utf-8"))
    assert state["provider"] == provider


@pytest.mark.parametrize(
    ("command", "result_key", "result"),
    [
        (
            "visual-brief publish --file brief.json",
            "tool_response",
            {
                "exit_code": 0,
                "stdout": "publish: appended briefing; rendered index.html",
            },
        ),
        (
            "printf '%s' data | visual-brief publish --stdin -",
            "tool_result",
            {
                "success": True,
                "stdout": "publish: appended briefing; rendered index.html",
            },
        ),
        (
            "visual-brief publish --file brief.json",
            "tool_response",
            {
                "stdout": "publish: appended briefing; rendered index.html",
                "stderr": "",
                "interrupted": False,
                "isImage": False,
                "noOutputExpected": False,
            },
        ),
    ],
)
def test_successful_publish_post_tool_use_activates_and_is_not_work(
    tmp_path: Path,
    command: str,
    result_key: str,
    result: dict[str, object],
) -> None:
    """The completed publish event activates from its payload session id."""
    reminder_module().activate_session(
        tmp_path,
        "claude",
        "payload-session",
        now=100.0,
    )
    reminder_module().record_tool_completion(
        tmp_path,
        "claude",
        "payload-session",
        meaningful=True,
        now=101.0,
    )
    payload = {
        "session_id": "payload-session",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        result_key: result,
    }
    completed = run_adapter("claude", payload, tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    state_path = reminder_states(tmp_path)[0]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["provider"] == "claude"
    assert state["meaningful_work_count"] == 0
    assert state["last_gate_time"] > 101.0


@pytest.mark.parametrize(
    ("tool_name", "command", "result"),
    [
        ("Bash", "visual-brief publish x", {"exit_code": 1}),
        ("Bash", "echo 'visual-brief publish x'", {"exit_code": 0}),
        ("Bash", "visual-brief view", {"exit_code": 0}),
        ("Bash", "visual-brief publish x", {}),
        (
            "Bash",
            "visual-brief publish x",
            {
                "stdout": "publish: appended briefing; rendered index.html",
                "stderr": "publish failed",
                "interrupted": False,
                "isImage": False,
                "noOutputExpected": False,
            },
        ),
        (
            "Bash",
            "visual-brief publish x",
            {
                "stdout": "publish: appended briefing; rendered index.html",
                "stderr": "",
                "interrupted": True,
                "isImage": False,
                "noOutputExpected": False,
            },
        ),
        (
            "Bash",
            "visual-brief publish x",
            {
                "stdout": "publish: appended briefing; rendered index.html",
                "stderr": "",
                "interrupted": "false",
                "isImage": False,
                "noOutputExpected": False,
            },
        ),
        ("Bash", "visual-brief publish x", {"success": True, "isError": True}),
        ("Edit", "visual-brief publish x", {"exit_code": 0}),
    ],
)
def test_non_publish_completion_does_not_activate(
    tmp_path: Path,
    tool_name: str,
    command: str,
    result: dict[str, object],
) -> None:
    """Only an executed successful publish shell segment may activate."""
    completed = run_adapter(
        "claude",
        {
            "session_id": "payload-session",
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "tool_response": result,
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {}
    assert reminder_states(tmp_path) == []


def test_publish_activation_failure_is_quiet_and_non_blocking(
    tmp_path: Path,
) -> None:
    """Reminder storage failures must not corrupt the hook protocol."""
    (tmp_path / ".reminders").write_text("not a directory", encoding="utf-8")
    completed = run_adapter(
        "claude",
        {
            "session_id": "payload-session",
            "tool_name": "Bash",
            "tool_input": {"command": "visual-brief publish --file brief.json"},
            "tool_response": {
                "exit_code": 0,
                "stdout": "publish: appended briefing; rendered index.html",
            },
        },
        tmp_path,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {}
