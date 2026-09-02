"""Session-scoped milestone reminder state and policy."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REMINDER = (
    "Visual Brief is active for this session. Remember to publish an update "
    "when you reach the next meaningful milestone."
)
SCHEMA_VERSION = 1
DEFAULT_SECONDS = 20 * 60
DEFAULT_COMPLETIONS = 3

_WRITE_TOOLS = {
    "applypatch",
    "edit",
    "multiedit",
    "notebookedit",
    "patch",
    "strreplace",
    "write",
}
_READ_TOOLS = {
    "find",
    "glob",
    "grep",
    "list",
    "ls",
    "read",
    "search",
    "status",
    "view",
}
_PROGRESS_COMMANDS = {
    "build",
    "cargo",
    "commit",
    "eslint",
    "fmt",
    "format",
    "jest",
    "lint",
    "make",
    "mypy",
    "npm",
    "pnpm",
    "prettier",
    "pytest",
    "review",
    "ruff",
    "test",
    "tox",
    "tsc",
    "uv",
    "vitest",
    "yarn",
}
_READ_ONLY_PACKAGE_MANAGERS = {"cargo", "npm", "yarn"}
_READ_ONLY_PACKAGE_VERBS = {"info", "search", "view"}
_VALUE_TAKING_PACKAGE_OPTIONS = {
    "cargo": {"--color"},
    "npm": {"--prefix"},
}


def activate_session(
    home: Path,
    provider: str,
    session_id: str,
    *,
    now: float | None = None,
) -> None:
    """Activate or reset reminders for one provider session."""
    if not provider or not session_id:
        return
    current = time.time() if now is None else now
    state = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "activation_time": current,
        "last_gate_time": current,
        "meaningful_work_count": 0,
    }
    with _locked_paths(home, provider, session_id) as (state_path, _):
        _write_state(state_path, state)


def record_tool_completion(
    home: Path,
    provider: str,
    session_id: str,
    *,
    meaningful: bool,
    now: float | None = None,
) -> str | None:
    """Record one completion and return advisory context when gates open."""
    if not provider or not session_id:
        return None
    state_path = _state_path(home, provider, session_id)
    if not state_path.exists():
        return None
    current = time.time() if now is None else now
    try:
        with _locked_paths(home, provider, session_id) as (locked_path, _):
            state = _read_state(locked_path, provider)
            if state is None:
                return None
            if not meaningful:
                return None
            state["meaningful_work_count"] += 1
            elapsed = current - state["last_gate_time"]
            if (
                elapsed >= _threshold("VISUAL_BRIEF_REMINDER_SECONDS", DEFAULT_SECONDS)
                and state["meaningful_work_count"]
                >= _threshold(
                    "VISUAL_BRIEF_REMINDER_COMPLETIONS",
                    DEFAULT_COMPLETIONS,
                )
            ):
                state["last_gate_time"] = current
                state["meaningful_work_count"] = 0
                _write_state(locked_path, state)
                return REMINDER
            _write_state(locked_path, state)
    except OSError:
        return None
    return None


def is_meaningful_completion(
    tool_name: str,
    tool_input: dict[str, object],
    tool_result: dict[str, object],
) -> bool:
    """Classify a normalized successful tool completion."""
    normalized = tool_name.strip().lower().replace("_", "")
    if normalized in _READ_TOOLS:
        return False
    if normalized in _WRITE_TOOLS:
        return _reported_success(tool_result) or _is_canonical_write_response(
            tool_result
        )
    if not _reported_success(tool_result):
        return False
    if normalized not in {"bash", "shell", "exec", "execcommand"}:
        return False
    command = tool_input.get("command", tool_input.get("cmd", ""))
    return isinstance(command, str) and _meaningful_command(command)


def _is_canonical_write_response(result: dict[str, object]) -> bool:
    """Recognize Claude's successful file-write response without a status code."""
    if "interrupted" in result and result["interrupted"] is not False:
        return False
    stderr = result.get("stderr", "")
    if not isinstance(stderr, str) or stderr:
        return False
    if any(
        key in result and result[key] is not False
        for key in ("error", "is_error", "isError")
    ):
        return False
    return (
        isinstance(result.get("filePath"), str)
        and bool(result["filePath"])
        and result.get("type") in {"create", "update"}
    )


def is_successful_publish_completion(
    tool_name: str,
    tool_input: dict[str, object],
    tool_result: dict[str, object],
) -> bool:
    """Recognize a successfully executed Visual Brief publish segment."""
    normalized = tool_name.strip().lower().replace("_", "")
    if normalized not in {"bash", "shell", "exec", "execcommand"}:
        return False
    if not _reported_success(tool_result):
        return False
    codex_output = tool_result.get("_codex_string_output")
    if isinstance(codex_output, str):
        output = codex_output
    else:
        output = tool_result.get("stdout")
    if not isinstance(output, str) or not _has_publish_receipt(output):
        return False
    command = tool_input.get("command", tool_input.get("cmd", ""))
    return isinstance(command, str) and _contains_publish_segment(command)


def _has_publish_receipt(output: str) -> bool:
    """Require the literal CLI receipt at the start of an output line."""
    return any(line.startswith("publish: appended ") for line in output.splitlines())


def _reported_success(result: dict[str, object]) -> bool:
    """Require an explicit, well-formed successful shell result."""
    if "interrupted" in result and result["interrupted"] is not False:
        return False
    stderr = result.get("stderr", "")
    if not isinstance(stderr, str) or stderr:
        return False
    if any(
        key in result and result[key] is not False
        for key in ("error", "is_error", "isError")
    ):
        return False
    success = result.get("success")
    if isinstance(success, bool):
        return success
    exit_code = result.get("exit_code", result.get("exitCode"))
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return exit_code == 0
    return (
        isinstance(result.get("stdout"), str)
        and result.get("interrupted") is False
        and isinstance(result.get("isImage"), bool)
        and isinstance(result.get("noOutputExpected"), bool)
    )


def _contains_publish_segment(command: str) -> bool:
    """Find a receipt-eligible publish in the final executable command group."""
    try:
        lexer = shlex.shlex(
            _separate_unquoted_newlines(command),
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False

    segment: list[str] = []
    pipeline: list[list[str]] = []
    final_group_has_publish = False
    for word in [*words, ";"]:
        if word == "|":
            pipeline.append(segment)
            segment = []
        elif word in {";", "&&", "||", "&", "|&"}:
            pipeline.append(segment)
            if any(pipeline):
                final_group_has_publish = _pipeline_has_receipt_eligible_publish(
                    pipeline
                )
            segment = []
            pipeline = []
        else:
            segment.append(word)
    return final_group_has_publish


def _pipeline_has_receipt_eligible_publish(pipeline: list[list[str]]) -> bool:
    """Require downstream pipeline stages to preserve a publish receipt."""
    for index, stage in enumerate(pipeline):
        if _is_publish_segment(stage) and all(
            _is_receipt_preserving_stage(downstream)
            for downstream in pipeline[index + 1 :]
        ):
            return True
    return False


def _is_receipt_preserving_stage(words: list[str]) -> bool:
    """Return whether a pipeline stage copies standard input to standard output."""
    index = 0
    while index < len(words) and _is_assignment(words[index]):
        index += 1
    return index < len(words) and Path(words[index]).name == "tee"


def _separate_unquoted_newlines(command: str) -> str:
    """Turn executable newlines into standalone semicolon separators."""
    normalized: list[str] = []
    quote: str | None = None
    escaped = False
    for character in command:
        if escaped:
            normalized.append(character)
            escaped = False
        elif character == "\\" and quote != "'":
            normalized.append(character)
            escaped = True
        elif character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            normalized.append(character)
        elif character == "\n" and quote is None:
            normalized.append(" ; ")
        else:
            normalized.append(character)
    return "".join(normalized)


def _is_publish_segment(words: list[str]) -> bool:
    """Check one tokenized command segment without matching its arguments."""
    index = 0
    while index < len(words) and _is_assignment(words[index]):
        index += 1
    if index + 1 >= len(words):
        return False
    return (
        Path(words[index]).name == "visual-brief"
        and words[index + 1] == "publish"
    )


def _is_assignment(word: str) -> bool:
    """Return whether a shell word is a leading environment assignment."""
    name, separator, _ = word.partition("=")
    return bool(separator and name and name.replace("_", "a").isalnum())


def _meaningful_command(command: str) -> bool:
    """Recognize successful build, test, format, review, and commit commands."""
    try:
        lexer = shlex.shlex(
            _separate_unquoted_newlines(command.lower()),
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False
    if not words:
        return False
    command_start = True
    for index, word in enumerate(words):
        if word in {";", "&&", "||", "|", "&", "|&"}:
            command_start = True
            continue
        if not command_start:
            continue
        if _is_assignment(word):
            continue
        command_start = False
        executable = Path(word).name
        if (
            executable in {"python", "python3"}
            and words[index + 1 : index + 3] == ["-m", "pytest"]
        ):
            return True
        if executable in _PROGRESS_COMMANDS:
            arguments = words[index + 1 :]
            while arguments and arguments[0].startswith("-"):
                option = arguments[0]
                arguments = arguments[1:]
                if (
                    option in _VALUE_TAKING_PACKAGE_OPTIONS.get(executable, set())
                    and arguments
                ):
                    arguments = arguments[1:]
            if (
                executable in _READ_ONLY_PACKAGE_MANAGERS
                and arguments
                and arguments[0] in _READ_ONLY_PACKAGE_VERBS
            ) or (
                executable == "npm" and arguments[:1] == ["ls"]
            ) or (
                executable == "cargo"
                and arguments[:2] == ["metadata", "--no-deps"]
            ) or (
                executable == "uv"
                and arguments[:2] in (["pip", "show"], ["pip", "list"])
            ):
                continue
            return True
        if executable == "git" and index + 1 < len(words):
            if words[index + 1] == "commit":
                return True
    return False


def _threshold(name: str, default: int) -> int:
    """Read a non-negative integer policy override."""
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _state_path(home: Path, provider: str, session_id: str) -> Path:
    """Return the opaque state path for one provider session."""
    digest = hashlib.sha256(f"{provider}|{session_id}".encode()).hexdigest()
    return home / ".reminders" / f"{digest}.json"


@contextmanager
def _locked_paths(
    home: Path,
    provider: str,
    session_id: str,
) -> Iterator[tuple[Path, Path]]:
    """Hold the per-session lock and yield its state and lock paths."""
    directory = home / ".reminders"
    directory.mkdir(parents=True, exist_ok=True)
    state_path = _state_path(home, provider, session_id)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield state_path, lock_path
            return

        import msvcrt

        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield state_path, lock_path
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _read_state(path: Path, provider: str) -> dict[str, Any] | None:
    """Read and validate one supported durable state record."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != SCHEMA_VERSION:
        return None
    if value.get("provider") != provider:
        return None
    activation_time = value.get("activation_time")
    if not _is_finite_timestamp(activation_time):
        return None
    last_gate_time = value.get("last_gate_time")
    if not _is_finite_timestamp(last_gate_time):
        return None
    count = value.get("meaningful_work_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    return value


def _is_finite_timestamp(value: Any) -> bool:
    """Return whether a value is numeric and representable as a finite float."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace one reminder state file."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
