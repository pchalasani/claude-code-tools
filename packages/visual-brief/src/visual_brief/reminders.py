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
_PACKAGE_MANAGER_INFORMATION_FLAGS = {"--help", "-h", "--version", "-v"}
_PACKAGE_MANAGERS = {"cargo", "npm", "pnpm", "uv", "yarn"}


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
    """Find a receipt-eligible publish in the final shell command list."""
    command_without_here_documents = _strip_here_document_bodies(command)
    if command_without_here_documents is None:
        return False
    try:
        lexer = shlex.shlex(
            _separate_unquoted_newlines(command_without_here_documents),
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
    and_list_has_publish = False
    or_list_has_branch = False
    for index, word in enumerate(words):
        if word == "&" and _is_standard_fd_redirection(words, index):
            segment.append(word)
            continue
        if word in {"|", "|&"}:
            pipeline.append(segment)
            segment = []
        elif word in {";", "&&", "||", "&"}:
            pipeline.append(segment)
            group_has_publish = _pipeline_has_receipt_eligible_publish(pipeline)
            if word == "&&":
                if not or_list_has_branch:
                    and_list_has_publish |= group_has_publish
            else:
                and_list_has_publish = False
                or_list_has_branch = word == "||"
            segment = []
            pipeline = []
        else:
            segment.append(word)
    pipeline.append(segment)
    return (
        not or_list_has_branch
        and (
            and_list_has_publish
            or _pipeline_has_receipt_eligible_publish(pipeline)
        )
    )


def _is_standard_fd_redirection(words: list[str], index: int) -> bool:
    """Keep a ``2>&1`` file-descriptor redirection inside its command."""
    return (
        index > 0
        and index + 1 < len(words)
        and words[index - 1][:-1].isdigit()
        and words[index - 1].endswith(">")
        and words[index + 1].isdigit()
    )


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
    """Remove continuations and turn executable newlines into separators."""
    normalized: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if (
            character == "\\"
            and quote is None
            and index + 1 < len(command)
            and command[index + 1] == "\n"
            and _unquoted_backslash_run_length(command, index) % 2 == 1
        ):
            index += 2
            continue
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
        index += 1
    return "".join(normalized)


def _strip_here_document_bodies(command: str) -> str | None:
    """Remove ordinary here-document bodies before splitting command newlines."""
    lines = command.splitlines(keepends=True)
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        here_document = _here_document_delimiter(line)
        if here_document is None:
            normalized.append(line)
            index += 1
            continue
        normalized.append(line.rstrip("\r\n"))
        delimiter, strip_tabs = here_document
        index += 1
        while index < len(lines):
            body_line = lines[index].rstrip("\r\n")
            comparable = body_line.lstrip("\t") if strip_tabs else body_line
            index += 1
            if comparable == delimiter:
                break
        else:
            return None
        if index < len(lines):
            normalized.append("\n")
    return "".join(normalized)


def _here_document_delimiter(line: str) -> tuple[str, bool] | None:
    """Return one ordinary Bash here-document delimiter from a command line."""
    quote: str | None = None
    escaped = False
    index = 0
    while index + 1 < len(line):
        character = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            index += 1
            continue
        if quote is not None or character != "<" or line[index + 1] != "<":
            index += 1
            continue
        index += 2
        strip_tabs = index < len(line) and line[index] == "-"
        if strip_tabs:
            index += 1
        while index < len(line) and line[index] in " \t":
            index += 1
        if index >= len(line) or line[index] in "\r\n":
            return None
        delimiter_quote = line[index] if line[index] in {"'", '"'} else None
        if delimiter_quote is not None:
            index += 1
            end = line.find(delimiter_quote, index)
            if end == -1:
                return None
            return line[index:end], strip_tabs
        end = index
        while end < len(line) and line[end] not in " \t\r\n;|&":
            end += 1
        return line[index:end], strip_tabs
    return None


def _unquoted_backslash_run_length(command: str, index: int) -> int:
    """Count the consecutive backslashes ending at ``index``."""
    start = index
    while start > 0 and command[start - 1] == "\\":
        start -= 1
    return index - start + 1


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
    """Recognize a standalone successful build, test, format, review, or commit."""
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
    if not words or _has_compound_shell_list(words):
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
            while (
                arguments
                and arguments[0].startswith("-")
                and arguments[0] not in _PACKAGE_MANAGER_INFORMATION_FLAGS
            ):
                option = arguments[0]
                arguments = arguments[1:]
                if (
                    option in _VALUE_TAKING_PACKAGE_OPTIONS.get(executable, set())
                    and arguments
                ):
                    arguments = arguments[1:]
            if (
                executable in _PACKAGE_MANAGERS
                and len(arguments) == 1
                and arguments[0] in _PACKAGE_MANAGER_INFORMATION_FLAGS
            ):
                continue
            if (
                executable in _READ_ONLY_PACKAGE_MANAGERS
                and arguments
                and arguments[0] in _READ_ONLY_PACKAGE_VERBS
            ) or (
                executable == "npm" and arguments[:1] == ["ls"]
            ) or (
                executable == "cargo"
                and arguments[:1] == ["metadata"]
                and "--no-deps" in arguments[1:]
            ) or (
                executable == "uv"
                and arguments[:2] in (["pip", "show"], ["pip", "list"])
            ):
                continue
            return True
        if executable == "git":
            if _git_commit_follows_global_options(words[index + 1 :]):
                return True
    return False


def _has_compound_shell_list(words: list[str]) -> bool:
    """Reject shell lists whose aggregate status cannot prove a segment ran."""
    for index, word in enumerate(words):
        if word in {";", "&&", "||", "|", "|&"}:
            return True
        if word == "&" and not _is_standard_fd_redirection(words, index):
            return True
    return False


def _git_commit_follows_global_options(arguments: list[str]) -> bool:
    """Recognize a commit after Git's ordinary global options."""
    index = 0
    options_with_values = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    options_without_values = {
        "--bare",
        "--no-pager",
        "--paginate",
        "--no-optional-locks",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--no-literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
    }
    while index < len(arguments):
        argument = arguments[index]
        if argument == "commit":
            return True
        if argument in options_without_values:
            index += 1
            continue
        if argument in options_with_values:
            index += 2
            continue
        if argument.startswith("-C") or argument.startswith("-c"):
            index += 1
            continue
        if any(
            argument.startswith(f"{option}=")
            for option in options_with_values - {"-C", "-c"}
        ):
            index += 1
            continue
        return False
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
