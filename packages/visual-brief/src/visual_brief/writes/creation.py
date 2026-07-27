"""Create a run directory and publish it in one atomic move."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from visual_brief.render import render_content
from visual_brief.server.daemon import DEFAULT_PORT
from visual_brief.server.registry import resolve_run_path, validate_run_id
from visual_brief.writes.runfiles import CliError, run_file, utc_timestamp


def new_command(
    runs_root: Path,
    label: str,
    run_id: str | None,
    port: int = DEFAULT_PORT,
) -> int:
    """Create a run directory and print copyable URLs for its daemon port.

    Args:
        runs_root: Directory holding every run.
        label: Human-facing name of the run.
        run_id: Explicit run identifier, or ``None`` to generate one.
        port: Port the daemon is expected to serve on.

    Returns:
        The process exit status.

    Raises:
        CliError: If the port, label or run id is unusable, or the run
            directory cannot be created.
    """
    if not 1 <= port <= 65_535:
        raise CliError("--port must be between 1 and 65535")
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

    print(f"http://{selected_id}.localhost:{port}/")
    print(f"http://localhost:{port}/r/{selected_id}/")
    return 0


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
    timestamp = utc_timestamp()
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
        run_file(temporary, "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_file(temporary, "content.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_file(temporary, "index.html").write_text(
            render_content(content) + "\n",
            encoding="utf-8",
        )
        run_file(temporary, "questions.jsonl").touch(mode=0o600)
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
                "summary": (
                    "Publish the Now panel with visual-brief publish-now."
                ),
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
