"""Integration tests for visual-brief CLI lifecycle behavior."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from visual_brief.cli import (
    CliError,
    _visual_brief_health,
    main,
    new_command,
    render_command,
    serve_command,
)
from visual_brief.server.daemon import HOST, VisualBriefServer, create_server


@contextmanager
def running_server(
    runs_root: Path,
) -> Iterator[VisualBriefServer]:
    """Run a visual-brief daemon on an ephemeral port."""
    server = create_server(runs_root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def raw_listener(response: bytes) -> Iterator[int]:
    """Run a one-request loopback listener with a fixed raw response."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def respond() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(4096)
            connection.sendall(response)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        listener.close()
        thread.join(timeout=2)


def test_serve_reuses_any_visual_brief_daemon(tmp_path: Path) -> None:
    """An occupied visual-brief port is reusable regardless of runs root."""
    first_root = tmp_path / "first"
    with running_server(first_root) as server:
        port = server.server_address[1]
        assert serve_command(first_root, port) == 0
        assert serve_command(tmp_path / "second", port) == 0


def test_serve_rejects_non_http_listener_without_traceback(
    tmp_path: Path,
) -> None:
    """A foreign protocol on the port becomes a concise CLI error."""
    with raw_listener(b"this is not HTTP\r\n") as port:
        with pytest.raises(CliError, match="different service"):
            serve_command(tmp_path / "runs", port)


def test_serve_reports_unusable_runs_root_without_claiming_bind_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A runs-root setup failure is not misdiagnosed as a bind failure."""
    runs_root = tmp_path / "loop"
    runs_root.symlink_to(runs_root, target_is_directory=True)
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(runs_root))

    assert main(["serve", "--port", "8803"]) == 2

    error = capsys.readouterr().err
    assert f"could not prepare runs root {runs_root}" in error
    assert "cannot bind" not in error


def test_invalid_utf8_health_is_a_foreign_service() -> None:
    """Invalid health response encoding is classified cleanly."""
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Content-Length: 1\r\nConnection: close\r\n\r\n\xff"
    )
    with raw_listener(response) as port:
        assert _visual_brief_health(port) is None


def test_new_succeeds_when_git_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional repository metadata stays empty when Git cannot execute."""
    runs_root = tmp_path / "runs"
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(tmp_path)

    assert new_command(runs_root, "No Git", "no-git") == 0

    metadata = json.loads(
        (runs_root / "no-git" / "meta.json").read_text(encoding="utf-8")
    )
    assert metadata["repo"] == ""
    assert metadata["branch"] == ""
    assert set(path.name for path in (runs_root / "no-git").iterdir()) == {
        "content.json",
        "index.html",
        "meta.json",
        "questions.jsonl",
    }


def test_new_cleans_temporary_run_after_initialization_failure(
    tmp_path: Path,
) -> None:
    """A late file-write failure leaves neither a run nor temp directory."""
    runs_root = tmp_path / "runs"

    with pytest.raises(CliError, match="could not initialize"):
        new_command(runs_root, "\ud800", "broken-run")

    assert list(runs_root.iterdir()) == []


def test_new_reports_runs_root_filesystem_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad runs-root parent produces a concise CLI error."""
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(parent_file / "runs"))

    assert main(["new", "--label", "Blocked", "--run-id", "blocked"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: could not prepare runs root ")
    assert "Traceback" not in captured.err


def test_render_rejects_index_symlink_without_corrupting_content(
    tmp_path: Path,
) -> None:
    """Rendering never follows an index symlink into the source JSON."""
    runs_root = tmp_path / "runs"
    assert new_command(runs_root, "Safe", "safe-run") == 0
    run = runs_root / "safe-run"
    content = run / "content.json"
    original = content.read_bytes()
    (run / "index.html").unlink()
    (run / "index.html").symlink_to("content.json")

    with pytest.raises(CliError, match="refusing to replace symlink"):
        render_command(runs_root, "safe-run")

    assert content.read_bytes() == original
    assert (run / "index.html").is_symlink()


def test_render_rejects_meta_symlink_before_publishing(
    tmp_path: Path,
) -> None:
    """Rendering never follows a metadata symlink into the source JSON."""
    runs_root = tmp_path / "runs"
    assert new_command(runs_root, "Safe", "safe-run") == 0
    run = runs_root / "safe-run"
    content = run / "content.json"
    index = run / "index.html"
    original_content = content.read_bytes()
    original_index = index.read_bytes()
    (run / "meta.json").unlink()
    (run / "meta.json").symlink_to("content.json")

    with pytest.raises(CliError, match="refusing to replace symlink"):
        render_command(runs_root, "safe-run")

    assert content.read_bytes() == original_content
    assert index.read_bytes() == original_index
    assert (run / "meta.json").is_symlink()


def test_render_ignores_invalid_utf8_metadata_before_publishing(
    tmp_path: Path,
) -> None:
    """Unreadable metadata cannot turn a completed publish into a failure."""
    runs_root = tmp_path / "runs"
    assert new_command(runs_root, "Safe", "safe-run") == 0
    run = runs_root / "safe-run"
    metadata = run / "meta.json"
    index = run / "index.html"
    metadata.write_bytes(b"\xff")
    index.write_text("old render", encoding="utf-8")

    assert render_command(runs_root, "safe-run") == 0

    assert metadata.read_bytes() == b"\xff"
    assert index.read_text(encoding="utf-8").startswith("<!doctype html>")
