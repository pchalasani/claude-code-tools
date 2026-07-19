"""Shared fixtures and helpers for the ``aichat resolve`` tests.

Not collected by pytest (no ``test_`` filename prefix); the split
``test_resolve_session*`` and port-resolver test modules import
these helpers and fixtures by name.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from claude_code_tools.aichat import main
from claude_code_tools.session_utils import encode_claude_project_path


CLAUDE_IDS = (
    "aaaa1111-1111-4111-8111-111111111111",
    "aaaa2222-2222-4222-8222-222222222222",
    "bbbb3333-3333-4333-8333-333333333333",
)
CODEX_IDS = (
    "cccc1111-1111-4111-8111-111111111111",
    "cccc2222-2222-4222-8222-222222222222",
    "dddd3333-3333-4333-8333-333333333333",
)


@dataclass(frozen=True)
class FakeHome:
    """Paths and metadata for one fake agent home."""

    path: Path
    ids: tuple[str, str, str]
    files: tuple[Path, Path, Path]
    directories: tuple[str, str, str]


def _write_claude_session(
    home: Path,
    session_id: str,
    cwd: str,
    title: str,
    mtime: float,
) -> Path:
    """Create one realistic minimal Claude transcript."""
    project = home / "projects" / encode_claude_project_path(cwd)
    project.mkdir(parents=True, exist_ok=True)
    session_file = project / f"{session_id}.jsonl"
    lines = [
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "message": {"content": "hello"},
        },
        {
            "type": "ai-title",
            "sessionId": session_id,
            "aiTitle": f"Auto {title}",
        },
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": title,
        },
    ]
    session_file.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


def _write_raw_claude_session(
    home: Path,
    session_id: str,
    project_cwd: str,
    lines: list[object],
    mtime: float = 1_720_000_000.0,
) -> Path:
    """Create a Claude transcript from exact JSON values."""
    project = home / "projects" / encode_claude_project_path(project_cwd)
    project.mkdir(parents=True, exist_ok=True)
    session_file = project / f"{session_id}.jsonl"
    session_file.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


@pytest.fixture
def claude_home(tmp_path: Path) -> FakeHome:
    """Create three Claude sessions with name and prefix collisions."""
    home = tmp_path / "claude"
    directories = (
        str(tmp_path / "work" / "alpha"),
        str(tmp_path / "work" / "beta"),
        str(tmp_path / "work" / "gamma"),
    )
    titles = ("Shared Plan", "Shared Plan", "Unique Deployment Review")
    files = tuple(
        _write_claude_session(
            home,
            session_id,
            cwd,
            title,
            1_700_000_000.0 + index,
        )
        for index, (session_id, cwd, title) in enumerate(
            zip(CLAUDE_IDS, directories, titles, strict=True)
        )
    )
    return FakeHome(home, CLAUDE_IDS, files, directories)


def _create_threads_database(database: Path) -> None:
    """Create the Codex threads schema used by the resolver."""
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT,
                rollout_path TEXT,
                cwd TEXT,
                title TEXT,
                archived INTEGER,
                git_branch TEXT,
                updated_at INTEGER
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_codex_thread(
    database: Path,
    session_id: object,
    rollout_path: object,
    cwd: object,
    title: object,
    updated_at: int,
    archived: object = False,
) -> None:
    """Insert one row into a fake Codex threads database."""
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, cwd, title, archived,
                git_branch, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                str(rollout_path) if isinstance(rollout_path, Path) else rollout_path,
                cwd,
                title,
                archived,
                "feat/session-finder",
                updated_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _write_codex_session(
    home: Path,
    session_id: str,
    cwd: str,
    mtime: float,
    lines: list[object] | None = None,
) -> Path:
    """Create one realistic minimal Codex rollout file."""
    session_dir = home / "sessions" / "2026" / "07" / "14"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / (
        f"rollout-2026-07-14T10-00-00-{session_id}.jsonl"
    )
    metadata = {
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "cwd": cwd,
            "git": {"branch": "feat/session-finder"},
        },
    }
    entries = [metadata] if lines is None else lines
    session_file.write_text(
        "".join(f"{json.dumps(entry)}\n" for entry in entries),
        encoding="utf-8",
    )
    os.utime(session_file, (mtime, mtime))
    return session_file


@pytest.fixture
def codex_home(tmp_path: Path) -> FakeHome:
    """Create three Codex threads with name and prefix collisions."""
    home = tmp_path / "codex"
    home.mkdir()
    directories = (
        str(tmp_path / "code" / "alpha"),
        str(tmp_path / "code" / "beta"),
        str(tmp_path / "code" / "gamma"),
    )
    titles = ("Shared Codex", "Shared Codex", "Unique Codex Migration")
    files = tuple(
        _write_codex_session(
            home,
            session_id,
            cwd,
            1_710_000_000.0 + index,
        )
        for index, (session_id, cwd) in enumerate(
            zip(CODEX_IDS, directories, strict=True)
        )
    )
    old_database = home / "state_4.sqlite"
    _create_threads_database(old_database)
    database = home / "state_5.sqlite"
    _create_threads_database(database)
    for index, (session_id, session_file, cwd, title) in enumerate(
        zip(CODEX_IDS, files, directories, titles, strict=True)
    ):
        _insert_codex_thread(
            database,
            session_id,
            session_file,
            cwd,
            title,
            1_710_000_000 + index,
            archived=index == 1,
        )
    return FakeHome(home, CODEX_IDS, files, directories)


@pytest.fixture
def runner() -> CliRunner:
    """Return an isolated in-process Click runner."""
    return CliRunner()


def _invoke(
    runner: CliRunner,
    query: str | None,
    home: Path | None = None,
    agent: str | None = None,
    extra: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    """Invoke ``aichat resolve`` with concise test arguments."""
    args = ["resolve"]
    if query is not None:
        args.append(query)
    if agent is not None:
        args.extend(["--agent", agent])
    if home is not None:
        args.extend(["--home", str(home)])
    if extra:
        args.extend(extra)
    return runner.invoke(main, args, env=env, catch_exceptions=False)


def _json(result: Result) -> dict[str, object]:
    """Decode a command's single JSON object."""
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _run_in_tty(arguments: list[str]) -> tuple[int, str]:
    """Run the CLI with a pseudo-terminal attached to stdout."""
    master, slave = os.openpty()
    command = [sys.executable, "-m", "claude_code_tools.aichat", *arguments]
    process = subprocess.Popen(command, stdout=slave, stderr=slave)
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4_096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(master)
    return process.wait(timeout=5), b"".join(chunks).decode(errors="replace")


def _run_cli(
    arguments: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the real module entry point with an isolated environment."""
    return subprocess.run(
        [sys.executable, "-m", "claude_code_tools.aichat", *arguments],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=20,
    )
