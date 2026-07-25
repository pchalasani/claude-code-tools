"""Command-line interface for visual brief runs."""

from __future__ import annotations

import argparse
import errno
import http.client
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from visual_brief.render import render_content
from visual_brief.server.daemon import DEFAULT_PORT, create_server
from visual_brief.server.registry import (
    discover_runs,
    resolve_run_path,
    validate_run_id,
)

DEFAULT_RUNS_ROOT = Path("~/.claude/visual-brief/runs/")


class CliError(Exception):
    """A concise user-facing command error."""


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

    render_parser = subparsers.add_parser("render", help="render a run")
    render_parser.add_argument("run_id")

    subparsers.add_parser("list", help="list runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface.

    Args:
        argv: Optional arguments excluding the executable name.

    Returns:
        The process exit status.
    """
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            return serve_command(get_runs_root(), args.port)
        if args.command == "new":
            return new_command(get_runs_root(), args.label, args.run_id)
        if args.command == "render":
            return render_command(get_runs_root(), args.run_id)
        return list_command(get_runs_root())
    except (CliError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def serve_command(runs_root: Path, port: int) -> int:
    """Start the daemon, or reuse a visual-brief daemon on the port."""
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


def new_command(runs_root: Path, label: str, run_id: str | None) -> int:
    """Create a run directory and print port-neutral access URLs."""
    label = label.strip()
    if not label:
        raise CliError("--label must not be empty")
    try:
        runs_root = runs_root.expanduser().resolve()
        runs_root.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError) as error:
        raise CliError(f"could not prepare runs root {runs_root}: {error}") from error

    selected_id = validate_run_id(run_id) if run_id is not None else None
    try:
        if selected_id is None:
            selected_id = _create_generated_run(runs_root, label)
        else:
            _initialize_run(runs_root, selected_id, label)
    except FileExistsError as error:
        raise CliError(f"run already exists: {selected_id}") from error
    except (OSError, UnicodeError) as error:
        identity = selected_id or "generated run"
        raise CliError(f"could not initialize run {identity}: {error}") from error

    print(f"http://{selected_id}.localhost/")
    print(f"http://localhost/r/{selected_id}/")
    return 0


def render_command(runs_root: Path, run_id: str) -> int:
    """Render a run's content JSON into its index page."""
    selected_id = validate_run_id(run_id)
    run_dir = resolve_run_path(runs_root, selected_id)
    if not run_dir.is_dir():
        raise CliError(f"unknown run: {selected_id}")
    content_path = _run_file(run_dir, "content.json")
    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CliError(f"cannot read {content_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliError(
            f"malformed JSON in {content_path} at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    try:
        html = render_content(data)
    except ValueError as error:
        raise CliError(f"{content_path}: {error}") from error
    try:
        output_path = _run_output_file(run_dir, "index.html")
        meta_path = _run_output_file(run_dir, "meta.json")
        _touch_updated_at(meta_path)
        _write_text_atomic(output_path, html + "\n")
    except OSError as error:
        raise CliError(f"cannot write rendered run {selected_id}: {error}") from error
    print(run_dir / "index.html")
    return 0


def list_command(runs_root: Path) -> int:
    """Print known runs and unanswered-question counts."""
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


def _create_generated_run(runs_root: Path, label: str) -> str:
    """Initialize a non-colliding generated run."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "run"
    slug = slug[:31].rstrip("-") or "run"
    for _ in range(100):
        candidate = validate_run_id(f"{slug}-{secrets.token_hex(3)}")
        try:
            _initialize_run(runs_root, candidate, label)
        except FileExistsError:
            continue
        return candidate
    raise CliError("could not generate a unique run id")


def _initialize_run(runs_root: Path, run_id: str, label: str) -> None:
    """Build a complete sibling directory, then publish it atomically."""
    run_dir = resolve_run_path(runs_root, run_id)
    if run_dir.exists():
        raise FileExistsError(run_dir)
    timestamp = _timestamp()
    cwd = Path.cwd()
    meta = {
        "run_id": run_id,
        "label": label,
        "cwd": str(cwd),
        "repo": _git_value(cwd, ["config", "--get", "remote.origin.url"]),
        "branch": _git_value(cwd, ["branch", "--show-current"]),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    content = _initial_content(label)
    temporary = Path(
        tempfile.mkdtemp(dir=runs_root, prefix=f".{run_id}.", suffix=".tmp")
    )
    try:
        _run_file(temporary, "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _run_file(temporary, "content.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _run_file(temporary, "index.html").write_text(
            render_content(content) + "\n",
            encoding="utf-8",
        )
        _run_file(temporary, "questions.jsonl").touch(mode=0o600)
        temporary.rename(run_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _initial_content(label: str) -> dict[str, object]:
    """Build a small valid document for a newly created run."""
    return {
        "title": label,
        "summary": "This visual brief is ready for content.",
        "updates": [
            {
                "id": "created",
                "timestamp": "Created",
                "headline": "The visual brief run is ready",
                "summary": "Replace content.json, then run visual-brief render.",
                "lanes": [],
            }
        ],
    }


def _git_value(cwd: Path, arguments: list[str]) -> str:
    """Read optional repository metadata without invoking a shell."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _touch_updated_at(meta_path: Path) -> None:
    """Update the activity timestamp in readable run metadata."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(meta, dict):
        return
    meta["updated_at"] = _timestamp()
    _write_text_atomic(
        meta_path,
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )


def _run_file(run_dir: Path, name: str) -> Path:
    """Resolve a run file and reject any final path outside the run."""
    try:
        root = run_dir.resolve()
        path = (root / name).resolve()
    except (OSError, RuntimeError) as error:
        raise CliError(f"cannot resolve {name} in {run_dir}: {error}") from error
    if path == root or not path.is_relative_to(root):
        raise CliError(f"run file escapes run directory: {name}")
    return path


def _run_output_file(run_dir: Path, name: str) -> Path:
    """Return a lexical output path whose parent is inside the run."""
    try:
        root = run_dir.resolve()
        path = root / name
        parent = path.parent.resolve()
    except (OSError, RuntimeError) as error:
        raise CliError(f"cannot resolve {name} in {run_dir}: {error}") from error
    if parent != root and not parent.is_relative_to(root):
        raise CliError(f"run file escapes run directory: {name}")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode):
            raise CliError(f"refusing to replace symlink: {path}")
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    """Replace a text file atomically."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _timestamp() -> str:
    """Return an RFC 3339 UTC timestamp."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def fail(message: str) -> NoReturn:
    """Raise a concise command-line error."""
    raise CliError(message)


if __name__ == "__main__":
    raise SystemExit(main())
