#!/usr/bin/env python3
"""Ask another Claude Code or Codex session a question.

Resolve sessions through ``aichat``, then resume them headlessly with their
original conversation context. Live or possibly-live sessions must be forked so
two processes never write the same transcript.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from claude_code_tools.helper_sessions import run_and_mark_helper_fork
except ModuleNotFoundError:
    tool_python = (
        Path.home()
        / ".local/share/uv/tools/claude-code-tools/bin/python3"
    )
    try:
        already_using_tool = Path(sys.executable).samefile(tool_python)
    except OSError:
        already_using_tool = False
    if already_using_tool or not tool_python.is_file():
        raise
    os.execv(
        str(tool_python),
        [str(tool_python), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

HOME = Path.home()
AGENTS = ("claude", "codex")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ResolutionError(RuntimeError):
    """Raised when a session target is missing or ambiguous."""


def _unique_existing(paths: list[Path]) -> list[Path]:
    """Return unique existing paths, resolving aliases and symlinks.

    Args:
        paths: Candidate filesystem paths.

    Returns:
        Existing paths with duplicate real paths removed.
    """
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        expanded = path.expanduser()
        if not expanded.is_dir():
            continue
        real = expanded.resolve()
        if real in seen:
            continue
        seen.add(real)
        result.append(expanded)
    return result


def _config_roots(agent: str, home: str | None = None) -> list[Path]:
    """Return configured homes for one agent.

    Args:
        agent: ``claude`` or ``codex``.
        home: Optional explicit configuration home.

    Returns:
        Candidate configuration homes in precedence order.
    """
    if home:
        roots = _unique_existing([Path(home)])
        if not roots:
            raise ResolutionError(f"Agent home does not exist: {home}")
        return roots
    if agent == "claude":
        env_name = "CLAUDE_CONFIG_DIR"
        defaults = [HOME / ".claude"]
    else:
        env_name = "CODEX_HOME"
        defaults = [HOME / ".codex"]

    candidates: list[Path] = []
    for value in re.split(r"[:,]", os.environ.get(env_name, "")):
        if value.strip():
            candidates.append(Path(value.strip()))
    candidates.extend(defaults)
    return _unique_existing(candidates)


def _run_aichat_resolve(
    target: str,
    agent: str,
    home: Path,
) -> dict[str, Any] | None:
    """Resolve one target in one agent home through ``aichat``.

    Args:
        target: Session name, UUID, fragment, or transcript path.
        agent: ``claude`` or ``codex``.
        home: Agent configuration home.

    Returns:
        The resolver record, or ``None`` when that home has no match.
    """
    command = [
        "aichat",
        "resolve",
        target,
        "--agent",
        agent,
        "--home",
        str(home),
        "--json",
    ]
    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, _stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise ResolutionError(f"aichat timed out while searching {home}.") from exc
    except FileNotFoundError as exc:
        raise ResolutionError("aichat is required but was not found on PATH.") from exc

    if process.returncode != 0:
        return None
    try:
        record = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ResolutionError(
            f"aichat returned invalid JSON for {agent} home {home}."
        ) from exc
    if not isinstance(record, dict) or not record.get("session_id"):
        return None
    return record


def _claude_live_record(
    session_id: str,
    home: Path,
) -> dict[str, Any] | None:
    """Find a Claude live-session record by UUID.

    Args:
        session_id: Resolved session UUID.
        home: Claude configuration home.

    Returns:
        The live record, or ``None`` when the session is not registered live.
    """
    for path in (home / "sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("sessionId") == session_id:
            return record
    return None


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize an ``aichat`` record for this helper.

    Args:
        record: Raw JSON record returned by ``aichat resolve``.

    Returns:
        A normalized session information dictionary.
    """
    agent = str(record["agent"])
    home = Path(str(record["home"])).expanduser()
    session_id = str(record["session_id"])
    live_record = _claude_live_record(session_id, home) if agent == "claude" else None
    if agent == "claude" and live_record:
        status = str(live_record.get("status") or "running")
        source = "running"
    elif agent == "claude":
        status = "stopped"
        source = "disk"
    else:
        # Codex persists names, but exposes no reliable cross-client live flag.
        status = "unknown"
        source = "disk"

    return {
        "agent": agent,
        "sessionId": session_id,
        "cwd": record.get("directory"),
        "status": status,
        "name": record.get("name"),
        "source": source,
        "config_root": str(home),
        "session_file": record.get("session_file"),
        "matched_by": record.get("matched_by"),
    }


def resolve(
    target: str,
    agent: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    """Resolve a target across configured Claude and Codex homes.

    Args:
        target: Session name, UUID, fragment, or transcript path.
        agent: Optional agent restriction.
        home: Optional explicit configuration home.

    Returns:
        One normalized session record.

    Raises:
        ResolutionError: If no session matches or multiple distinct sessions
            match.
    """
    agents = (agent,) if agent else AGENTS
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate_agent in agents:
        for candidate_home in _config_roots(candidate_agent, home):
            record = _run_aichat_resolve(
                target,
                candidate_agent,
                candidate_home,
            )
            if record is None:
                continue
            key = (
                str(record["agent"]),
                str(record["home"]),
                str(record["session_id"]),
            )
            if key in seen:
                continue
            seen.add(key)
            matches.append(_normalize_record(record))

    if not matches:
        qualifier = f" for agent {agent}" if agent else ""
        raise ResolutionError(f"Could not resolve session {target!r}{qualifier}.")
    if len(matches) > 1:
        choices = ", ".join(f"{item['agent']}:{item['sessionId']}" for item in matches)
        raise ResolutionError(
            f"Session target {target!r} is ambiguous ({choices}). "
            "Pass --agent and --home to select one account."
        )
    return matches[0]


def _codex_names(home: Path) -> list[dict[str, Any]]:
    """Read persistent Codex names from a session index.

    Args:
        home: Codex configuration home.

    Returns:
        The newest record for each named session UUID.
    """
    index = home / "session_index.jsonl"
    latest: dict[str, dict[str, Any]] = {}
    try:
        lines = index.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = record.get("id")
        name = record.get("thread_name")
        if session_id and name:
            latest[str(session_id)] = record
    return list(latest.values())


def cmd_list(_args: argparse.Namespace) -> int:
    """List live Claude names and persistent Codex names."""
    rows: list[dict[str, str]] = []
    for home in _config_roots("claude"):
        for path in (home / "sessions").glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not record.get("name"):
                continue
            rows.append(
                {
                    "agent": "claude",
                    "name": str(record["name"]),
                    "status": str(record.get("status") or "running"),
                    "id": str(record.get("sessionId") or "-"),
                    "root": home.name,
                }
            )
    for home in _config_roots("codex"):
        for record in _codex_names(home):
            rows.append(
                {
                    "agent": "codex",
                    "name": str(record["thread_name"]),
                    "status": "stored",
                    "id": str(record["id"]),
                    "root": home.name,
                }
            )

    unique: dict[tuple[str, str], dict[str, str]] = {
        (row["agent"], row["id"]): row for row in rows
    }
    if not unique:
        print("No named Claude or Codex sessions found.", file=sys.stderr)
        return 0
    print(f"{'AGENT':<8} {'NAME':<32} {'STATUS':<9} {'SESSION-ID':<38} ROOT")
    for row in sorted(unique.values(), key=lambda item: (item["agent"], item["name"])):
        print(
            f"{row['agent']:<8} {row['name']:<32} {row['status']:<9} "
            f"{row['id']:<38} {row['root']}"
        )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve and print one session."""
    try:
        info = resolve(args.target, args.agent, args.home)
    except ResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(info, indent=2))
    return 0


def _send_to_claude(
    info: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    """Send a message through Claude's headless resume mode."""
    command = [
        "claude",
        "-p",
        "--resume",
        str(info["sessionId"]),
        "--output-format",
        "json",
    ]
    if args.fork:
        command.append("--fork-session")
    if args.mode:
        command.extend(["--permission-mode", args.mode])
    if args.model:
        command.extend(["--model", args.model])
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR"] = str(info["config_root"])

    session_ids: list[str] = []

    def run() -> tuple[int, str | None]:
        result = _run_and_print(
            command,
            info,
            args,
            environment,
            result_format="claude-json",
            result_session_ids=session_ids,
        )
        session_id = session_ids[-1] if session_ids else None
        return result, session_id

    if args.fork:
        source = Path(str(info["session_file"]))
        return run_and_mark_helper_fork(source, run)
    return run()[0]


def _send_to_codex(
    info: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    """Send a message through Codex's native headless fork or resume mode."""
    if args.mode not in (None, "default", "bypassPermissions"):
        print(
            f"--mode {args.mode} is Claude-specific. For Codex, use "
            "default or bypassPermissions.",
            file=sys.stderr,
        )
        return 2

    session_id = str(info["sessionId"])
    output_fd, output_name = tempfile.mkstemp(
        prefix="ask-session-codex-",
        suffix=".txt",
    )
    os.close(output_fd)
    output_path = Path(output_name)
    command = ["codex", "exec", "-s", "read-only"]
    if args.mode == "bypassPermissions":
        command = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
    if args.model:
        command.extend(["--model", args.model])
    command.extend([
        "fork" if args.fork else "resume",
        "--json", "-o", str(output_path), session_id, "-",
    ])
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(info["config_root"])
    try:
        return _run_and_print(
            command,
            info,
            args,
            environment,
            result_format="codex-json",
            output_path=output_path,
        )
    finally:
        output_path.unlink(missing_ok=True)


def _run_and_print(
    command: list[str],
    info: dict[str, Any],
    args: argparse.Namespace,
    environment: dict[str, str],
    result_format: str,
    output_path: Path | None = None,
    derived_session_id: str | None = None,
    result_session_ids: list[str] | None = None,
) -> int:
    """Run one headless agent command and print its final response."""
    print(
        f"[ask-session] -> {info.get('name') or info['sessionId']} "
        f"(agent={info['agent']}, cwd={info['cwd']}, "
        f"root={Path(str(info['config_root'])).name}, fork={args.fork}, "
        f"mode={args.mode or 'default'})",
        file=sys.stderr,
    )
    try:
        process = subprocess.run(
            command,
            cwd=str(info["cwd"]),
            input=args.message,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"Timed out after {args.timeout}s.", file=sys.stderr)
        return 4
    claude_data: dict[str, Any] | None = None
    if result_format == "claude-json":
        try:
            decoded = json.loads(process.stdout)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            claude_data = decoded
            reported_id = decoded.get("session_id")
            if isinstance(reported_id, str) and reported_id:
                derived_session_id = reported_id
                if result_session_ids is not None:
                    result_session_ids.append(reported_id)
    if process.returncode != 0:
        print(
            f"{info['agent']} exited {process.returncode}:\n{process.stderr.strip()}",
            file=sys.stderr,
        )
        return process.returncode
    answer: str | None = None
    if result_format == "codex-json":
        try:
            answer = output_path.read_text(encoding="utf-8") if output_path else ""
        except OSError:
            answer = ""
        if not answer.strip():
            print("Codex returned no final response.", file=sys.stderr)
            return 5
        for line in process.stdout.split("\n"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    derived_session_id = thread_id
                    break
    if args.raw:
        sys.stdout.write(process.stdout)
        return 0
    if result_format == "claude-json":
        if claude_data is None:
            sys.stdout.write(process.stdout)
            return 0
        print(claude_data.get("result", ""))
    elif answer is not None:
        print(answer.rstrip())
    else:
        sys.stdout.write(process.stdout)
    if derived_session_id:
        print(
            f"[ask-session] done: session_id={derived_session_id}",
            file=sys.stderr,
        )
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    """Resolve a session and send it one message."""
    try:
        info = resolve(args.target, args.agent, args.home)
    except ResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not info.get("cwd"):
        print("Resolved session has no working directory.", file=sys.stderr)
        return 2

    possibly_live = info["source"] == "running" or info["agent"] == "codex"
    if possibly_live and not (args.fork or args.force):
        print(
            f"Refusing to resume {args.target!r}: the session is live or "
            "its live state cannot be established safely. Use --fork "
            "(recommended) or --force (risky).",
            file=sys.stderr,
        )
        return 3
    if info["agent"] == "claude":
        return _send_to_claude(info, args)
    return _send_to_codex(info, args)


def _add_agent_option(parser: argparse.ArgumentParser) -> None:
    """Add common optional agent and home restrictions."""
    parser.add_argument(
        "--agent",
        choices=AGENTS,
        help="Restrict resolution to Claude or Codex.",
    )
    parser.add_argument(
        "--home",
        help="Restrict resolution to one Claude or Codex configuration home.",
    )


def main() -> int:
    """Parse command-line arguments and run the selected subcommand."""
    parser = argparse.ArgumentParser(
        prog="ask-session.py",
        description=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list",
        help="List live Claude names and stored Codex names.",
    )
    list_parser.set_defaults(func=cmd_list)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve a session name, ID, fragment, or path.",
    )
    resolve_parser.add_argument("target")
    _add_agent_option(resolve_parser)
    resolve_parser.set_defaults(func=cmd_resolve)

    send_parser = subparsers.add_parser(
        "send",
        help="Resume a session with one message.",
    )
    send_parser.add_argument("target")
    send_parser.add_argument(
        "-m",
        "--message",
        required=True,
        help="Message to send.",
    )
    send_parser.add_argument(
        "--fork",
        action="store_true",
        help="Fork first; required for live or possibly-live sessions.",
    )
    send_parser.add_argument(
        "--force",
        action="store_true",
        help="Resume the original live session anyway; risks corruption.",
    )
    send_parser.add_argument(
        "--mode",
        choices=[
            "acceptEdits",
            "auto",
            "bypassPermissions",
            "default",
            "dontAsk",
            "plan",
        ],
        help="Permission mode; Codex supports default or bypassPermissions.",
    )
    send_parser.add_argument("--model", help="Optional model override.")
    send_parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Timeout in seconds (default: 900).",
    )
    send_parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw agent output.",
    )
    _add_agent_option(send_parser)
    send_parser.set_defaults(func=cmd_send)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
