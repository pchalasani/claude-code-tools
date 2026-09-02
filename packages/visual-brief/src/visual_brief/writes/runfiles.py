"""Shared run-file access for validated, atomic visual brief writes.

Every verb that changes a run goes through this module. It resolves the run,
reads ``content.json``, hands the resulting document to the renderer's own
validator before anything touches disk, and publishes both ``content.json``
and the re-rendered ``index.html`` by temp-file-and-rename inside the run
directory. A crash therefore leaves the previous valid pair in place.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from visual_brief.render import render_content
from visual_brief.server.registry import (
    discover_runs,
    resolve_run_path,
    validate_run_id,
)
from visual_brief.writes.queue_view import question_lists


class CliError(Exception):
    """A concise user-facing command error."""


@contextmanager
def write_transaction(run_dir: Path) -> Iterator[None]:
    """Serialize one run's complete read-modify-write transaction.

    Args:
        run_dir: The run directory whose content will be changed.

    Yields:
        Control while this process holds the run's exclusive write lock.

    Raises:
        CliError: If the per-run lock cannot be opened or acquired.
    """
    lock_path = run_output_file(run_dir, ".write.lock")
    try:
        lock_file = lock_path.open("a", encoding="utf-8")
    except OSError as error:
        raise CliError(f"cannot open run write lock: {error}") from error
    with lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError as error:
            raise CliError(f"cannot acquire run write lock: {error}") from error
        yield


def resolve_run(runs_root: Path, run_id: str | None) -> tuple[str, Path]:
    """Resolve the run a verb acts on.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` to use the only run.

    Returns:
        The validated run id and its resolved directory.

    Raises:
        CliError: If the run is unknown, or if the run is ambiguous because
            more than one run exists and none was named.
    """
    if run_id is None:
        runs = discover_runs(runs_root)
        if not runs:
            raise CliError(f"no visual brief runs under {runs_root}")
        if len(runs) > 1:
            names = ", ".join(run.run_id for run in runs)
            raise CliError(f"--run is required; runs are: {names}")
        run_id = runs[0].run_id
    try:
        selected = validate_run_id(run_id)
        run_dir = resolve_run_path(runs_root, selected)
    except ValueError as error:
        raise CliError(str(error)) from error
    if not run_dir.is_dir():
        raise CliError(f"unknown run: {selected}")
    return selected, run_dir


def read_content(run_dir: Path) -> dict[str, Any]:
    """Read one run's content document.

    Args:
        run_dir: The run directory.

    Returns:
        The parsed document, unvalidated and unnormalized.

    Raises:
        CliError: If the file cannot be read, is not JSON, or is not an
            object. A verb that changes nothing must still say so, rather
            than treating damaged content as an empty page.
    """
    content_path = run_file(run_dir, "content.json")
    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CliError(f"cannot read {content_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliError(
            f"malformed JSON in {content_path} at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(data, dict):
        raise CliError(
            f"{content_path}: top-level JSON value must be an object"
        )
    return data


def render_html(run_dir: Path, data: Any) -> str:
    """Validate a document and render it, without touching disk.

    Args:
        run_dir: The run directory, named in validation errors.
        data: The candidate document.

    Returns:
        The rendered page.

    Raises:
        CliError: If the document does not satisfy the schema.
    """
    content_path = run_dir / "content.json"
    collision = _repeated_thread(data)
    if collision is not None:
        thread_id, first, second = collision
        raise CliError(
            f"{content_path}: two conversations carry the id {thread_id!r}, "
            f"one at {first} and one at {second}; an id names one "
            "conversation, and the queue line that generated it can only "
            "belong to one of them — keep that one and drop the other"
        )
    try:
        return render_content(data)
    except ValueError as error:
        raise CliError(f"{content_path}: {error}") from error


def _repeated_thread(data: Any) -> tuple[str, str, str] | None:
    """Return the first repeated conversation id and both anchor paths."""
    seen: dict[str, str] = {}
    for path, questions in question_lists(data):
        for thread in questions:
            if not isinstance(thread, dict):
                continue
            thread_id = thread.get("id")
            if not isinstance(thread_id, str):
                continue
            if thread_id in seen:
                return thread_id, seen[thread_id], path
            seen[thread_id] = path
    return None


def publish_render(run_dir: Path, data: Any) -> Path:
    """Publish a run's rendered page for an unchanged content document.

    Args:
        run_dir: The run directory.
        data: The document to render.

    Returns:
        The path of the written page.

    Raises:
        CliError: If the document is invalid or the run cannot be written.
    """
    html = render_html(run_dir, data)
    index_path = run_output_file(run_dir, "index.html")
    meta_path = run_output_file(run_dir, "meta.json")
    try:
        touch_updated_at(meta_path)
        write_text_atomic(index_path, html + "\n")
    except OSError as error:
        raise CliError(f"cannot write rendered run: {error}") from error
    return index_path


def save_document(run_dir: Path, data: Any) -> Path:
    """Validate a document, then publish content and page together.

    Args:
        run_dir: The run directory.
        data: The document to save.

    Returns:
        The path of the written page.

    Raises:
        CliError: If the document is invalid or the run cannot be written.
    """
    html = render_html(run_dir, data)
    content_path = run_output_file(run_dir, "content.json")
    index_path = run_output_file(run_dir, "index.html")
    meta_path = run_output_file(run_dir, "meta.json")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        with rollback_replaced_files(content_path, index_path, meta_path):
            write_text_atomic(content_path, payload)
            write_text_atomic(index_path, html + "\n")
            touch_updated_at(meta_path)
    except OSError as error:
        raise CliError(f"cannot write run: {error}") from error
    return index_path


@contextmanager
def rollback_replaced_files(*paths: Path) -> Iterator[None]:
    """Restore files byte-for-byte if a group of replacements fails.

    Args:
        *paths: Files that the guarded operation may replace.

    Yields:
        Control while replacements are attempted.
    """
    originals: list[tuple[Path, bytes | None]] = []
    for path in paths:
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            original = None
        originals.append((path, original))
    try:
        yield
    except BaseException:
        for path, original in originals:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _write_bytes_atomic(path, original)
        raise


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    """Replace one file atomically with exact bytes."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_file(run_dir: Path, name: str) -> Path:
    """Resolve a run file and reject any final path outside the run.

    Args:
        run_dir: The run directory.
        name: Name of a file inside the run.

    Returns:
        The resolved path.

    Raises:
        CliError: If the path escapes the run directory.
    """
    try:
        root = run_dir.resolve()
        path = (root / name).resolve()
    except (OSError, RuntimeError) as error:
        raise CliError(f"cannot resolve {name} in {run_dir}: {error}") from error
    if path == root or not path.is_relative_to(root):
        raise CliError(f"run file escapes run directory: {name}")
    return path


def run_output_file(run_dir: Path, name: str) -> Path:
    """Return a lexical output path whose parent is inside the run.

    Args:
        run_dir: The run directory.
        name: Name of a file the command will replace.

    Returns:
        The output path.

    Raises:
        CliError: If the path escapes the run or is a symlink.
    """
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


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a text file atomically.

    Args:
        path: File to replace.
        content: Complete new contents.
    """
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


def touch_updated_at(meta_path: Path) -> None:
    """Update the activity timestamp in readable run metadata.

    Args:
        meta_path: Path of the run's metadata file.
    """
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(meta, dict):
        return
    meta["updated_at"] = utc_timestamp()
    write_text_atomic(
        meta_path,
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )


def utc_timestamp(milliseconds: bool = False) -> str:
    """Return an RFC 3339 UTC timestamp from the real clock.

    Args:
        milliseconds: Whether to keep millisecond precision.

    Returns:
        The formatted instant.
    """
    precision = "milliseconds" if milliseconds else "seconds"
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=precision)
        .replace("+00:00", "Z")
    )
