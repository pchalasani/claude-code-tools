"""Command-line interface for visual brief runs.

This module is wiring: it parses arguments, resolves the runs root, and hands
each command to the module that owns it. The verbs that change a run live in
``visual_brief.writes``.
"""

from __future__ import annotations

import argparse
import errno
import http.client
import json
import os
import sys
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from visual_brief.bridge import watch_command
from visual_brief.reminders import (
    activate_session,
    is_meaningful_completion,
    is_successful_publish_completion,
    record_tool_completion,
)
from visual_brief.server.daemon import DEFAULT_PORT, create_server
from visual_brief.server.registry import discover_runs
from visual_brief.writes import (
    CliError,
    add_update_command,
    answer_command,
    fold_command,
    lint_command,
    new_command,
    publish_command,
    publish_render,
    read_content,
    read_json_payload,
    read_text_payload,
    report_lint,
    resolve_run,
)
from visual_brief.writes.runfiles import write_transaction

DEFAULT_RUNS_ROOT = Path("~/.claude/visual-brief/runs/")

__all__ = [
    "CliError",
    "build_parser",
    "get_runs_root",
    "list_command",
    "main",
    "new_command",
    "render_command",
    "serve_command",
]


def get_runs_root() -> Path:
    """Return the configured visual brief runs directory."""
    configured = os.environ.get("VISUAL_BRIEF_HOME")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_RUNS_ROOT.expanduser()


def build_parser() -> argparse.ArgumentParser:
    """Build the visual-brief argument parser."""
    parser = argparse.ArgumentParser(description="Create and serve visual briefs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="start the shared daemon")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    new_parser = subparsers.add_parser("new", help="create a run")
    new_parser.add_argument("--label", required=True)
    new_parser.add_argument("--run-id")
    new_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    render_parser = subparsers.add_parser("render", help="render a run")
    render_parser.add_argument("run_id")

    subparsers.add_parser("list", help="list runs")

    watch_parser = subparsers.add_parser(
        "watch",
        help="watch questions and wake the owning agent",
    )
    watch_parser.add_argument(
        "--agent",
        choices=["claude", "codex"],
        required=True,
    )
    _add_run_option(watch_parser)
    watch_parser.add_argument("--thread-id")
    watch_parser.add_argument("--endpoint")

    fold_parser = subparsers.add_parser(
        "fold",
        help="fold queued questions into the page",
    )
    _add_run_option(fold_parser)

    answer_parser = subparsers.add_parser(
        "answer",
        help="answer one conversation on the page",
    )
    answer_parser.add_argument("thread_id")
    _add_run_option(answer_parser)
    answer_source = answer_parser.add_mutually_exclusive_group()
    answer_source.add_argument("--text", help="the answer itself")
    answer_source.add_argument("--file", help="read the answer from a file")
    _add_stdin_argument(answer_parser)

    update_parser = subparsers.add_parser(
        "add-update",
        help="append one briefing for compatibility imports",
    )
    _add_run_option(update_parser)
    update_parser.add_argument("--file", help="read the update from a file")
    _add_stdin_argument(update_parser)

    publish_parser = subparsers.add_parser(
        "publish",
        help="append one direct briefing",
    )
    _add_run_option(publish_parser)
    publish_parser.add_argument(
        "--file",
        help="read one briefing object from a file",
    )
    _add_stdin_argument(publish_parser)

    lint_parser = subparsers.add_parser("lint", help="check one run's content")
    _add_run_option(lint_parser)
    lint_parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 when anything is reported",
    )
    reminder_parser = subparsers.add_parser(
        "reminder-hook",
        help="handle a provider PostToolUse event",
    )
    reminder_parser.add_argument(
        "--provider",
        choices=["auto", "claude", "codex"],
        required=True,
    )
    return parser


def _add_run_option(parser: argparse.ArgumentParser) -> None:
    """Add the run selector shared by every verb."""
    parser.add_argument(
        "--run",
        dest="run",
        default=None,
        help="run id; optional when exactly one run exists",
    )


def _add_stdin_argument(parser: argparse.ArgumentParser) -> None:
    """Add the bare ``-`` that reads the payload from standard input."""
    parser.add_argument(
        "stdin",
        nargs="?",
        choices=["-"],
        default=None,
        help="read the payload from standard input",
    )


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface.

    Args:
        argv: Optional arguments excluding the executable name.

    Returns:
        The process exit status.
    """
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (CliError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    """Run the selected command."""
    runs_root = get_runs_root()
    if args.command == "serve":
        return serve_command(runs_root, args.port)
    if args.command == "new":
        return new_command(runs_root, args.label, args.run_id, args.port)
    if args.command == "render":
        return render_command(runs_root, args.run_id)
    if args.command == "watch":
        run_id, run_dir = resolve_run(runs_root, args.run)
        return watch_command(
            run_id,
            run_dir,
            args.agent,
            args.thread_id,
            args.endpoint,
        )
    if args.command == "fold":
        return fold_command(runs_root, args.run)
    if args.command == "answer":
        return answer_command(
            runs_root,
            args.run,
            args.thread_id,
            read_text_payload(args.text, args.file, args.stdin == "-"),
        )
    if args.command == "add-update":
        return add_update_command(
            runs_root,
            args.run,
            read_json_payload(args.file, args.stdin == "-"),
        )
    if args.command == "publish":
        return publish_command(
            runs_root,
            args.run,
            read_json_payload(args.file, args.stdin == "-"),
        )
    if args.command == "lint":
        return lint_command(runs_root, args.run, args.strict)
    if args.command == "reminder-hook":
        return reminder_hook_command(runs_root, args.provider)
    return list_command(runs_root)


def reminder_hook_command(runs_root: Path, provider: str) -> int:
    """Normalize PostToolUse JSON and emit quiet provider context."""
    response: dict[str, object] = {}
    try:
        provider = _resolve_reminder_provider(provider)
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        session_id = payload.get("session_id")
        tool_name = payload.get("tool_name")
        tool_input = payload.get("tool_input", {})
        tool_result = payload.get(
            "tool_response",
            payload.get("tool_result", {}),
        )
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("missing session identity")
        if not isinstance(tool_name, str):
            raise ValueError("missing tool name")
        if not isinstance(tool_input, dict):
            raise ValueError("invalid tool fields")
        tool_result = _normalize_reminder_result(provider, tool_result)
        if is_successful_publish_completion(tool_name, tool_input, tool_result):
            activate_session(runs_root, provider, session_id.strip())
            print("{}")
            return 0
        meaningful = is_meaningful_completion(
            tool_name,
            tool_input,
            tool_result,
        )
        reminder = record_tool_completion(
            runs_root,
            provider,
            session_id.strip(),
            meaningful=meaningful,
        )
        if reminder is not None:
            response = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": reminder,
                }
            }
    except Exception:
        pass
    print(json.dumps(response, separators=(",", ":")))
    return 0


def _normalize_reminder_result(
    provider: str,
    tool_result: object,
) -> dict[str, object]:
    """Normalize provider output without weakening Claude's object contract."""
    if isinstance(tool_result, dict):
        return tool_result
    if provider != "codex" or not isinstance(tool_result, str):
        raise ValueError("invalid tool result")
    output = tool_result.strip()
    lowered = output.lower()
    error_markers = ("error:", "failed", "failure", "process exited")
    if not output or any(marker in lowered for marker in error_markers):
        return {"success": False, "_codex_string_output": tool_result}
    return {"success": True, "_codex_string_output": tool_result}


def _resolve_reminder_provider(provider: str) -> str:
    """Resolve auto mode from provider-guaranteed plugin-root variables."""
    if provider in {"claude", "codex"}:
        return provider
    if provider != "auto":
        raise ValueError("unsupported reminder provider")
    if os.environ.get("PLUGIN_ROOT", "").strip():
        return "codex"
    if os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip():
        return "claude"
    raise ValueError("auto provider requires a plugin root")


def serve_command(runs_root: Path, port: int) -> int:
    """Start the daemon, or reuse a visual-brief daemon on the port.

    Args:
        runs_root: Directory holding every run.
        port: Loopback port to bind.

    Returns:
        The process exit status.

    Raises:
        CliError: If the port is unusable or occupied by another service.
    """
    if not 0 <= port <= 65_535:
        raise CliError("--port must be between 0 and 65535")
    try:
        server = create_server(runs_root, port)
    except ValueError as error:
        raise CliError(str(error)) from error
    except OSError as error:
        if error.errno != errno.EADDRINUSE or port == 0:
            raise CliError(f"cannot bind to 127.0.0.1:{port}: {error}") from error
        health = _visual_brief_health(port)
        if health is None:
            raise CliError(
                f"port {port} is already used by a different service"
            ) from error
        print(f"http://localhost:{port}/")
        return 0

    chosen_port = server.server_address[1]
    print(f"http://localhost:{chosen_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def render_command(runs_root: Path, run_id: str) -> int:
    """Render a run's content JSON into its index page.

    Args:
        runs_root: Directory holding every run.
        run_id: The run to render.

    Returns:
        The process exit status.

    Raises:
        CliError: If the run is unknown, unreadable, or invalid.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    with write_transaction(run_dir):
        data = read_content(run_dir)
        index_path = publish_render(run_dir, data)
    print(index_path)
    report_lint(run_dir, data)
    return 0


def list_command(runs_root: Path) -> int:
    """Print known runs and unanswered-question counts.

    Args:
        runs_root: Directory holding every run.

    Returns:
        The process exit status.
    """
    runs = discover_runs(runs_root)
    if not runs:
        print("No visual brief runs.")
        return 0
    for run in runs:
        details = [run.run_id, run.label]
        if run.repo:
            details.append(run.repo)
        if run.branch:
            details.append(run.branch)
        details.append(f"unanswered: {run.unanswered_count}")
        print(" | ".join(details))
    return 0


def _visual_brief_health(port: int) -> dict[str, object] | None:
    """Return visual-brief health data, or none for any other listener."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as response:
            data = json.load(response)
    except (
        OSError,
        HTTPError,
        URLError,
        UnicodeDecodeError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ):
        return None
    if not isinstance(data, dict) or data.get("service") != "visual-brief":
        return None
    return data


def fail(message: str) -> NoReturn:
    """Raise a concise command-line error.

    Args:
        message: The message to show the user.

    Raises:
        CliError: Always.
    """
    raise CliError(message)


if __name__ == "__main__":
    raise SystemExit(main())
