"""Opt-in native Codex transfer check without credentials or model requests."""

from __future__ import annotations

import json
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, Self

import pytest

from claude_code_tools.transfer_codex import export_session, import_databases

pytestmark = pytest.mark.skipif(
    os.environ.get("CCT_CODEX_NATIVE_TEST") != "1" or shutil.which("codex") is None,
    reason="Set CCT_CODEX_NATIVE_TEST=1 with Codex 0.153.4 installed",
)


class NativeServer:
    """Bounded stdio client that always reaps its isolated native server."""

    def __init__(self, home: Path, account: Path, stderr_path: Path) -> None:
        self.home = home
        self.account = account
        self.stderr_path = stderr_path
        self.responses: queue.Queue[str | None] = queue.Queue()
        self.request_id = 0

    def __enter__(self) -> Self:
        self.stderr = self.stderr_path.open("w")
        try:
            self.process = subprocess.Popen(
                ["codex", "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                text=True,
                bufsize=1,
                cwd=self.home,
                env={
                    "PATH": os.environ["PATH"],
                    "HOME": str(self.home),
                    "CODEX_HOME": str(self.account),
                },
            )
        except BaseException:
            self.stderr.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {"name": "transfer-native-test", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self._send({"method": "initialized"})
        except BaseException:
            self.close()
            raise
        return self

    def _read(self) -> None:
        """Drain stdout independently so RPC deadlines also cover partial lines."""
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.responses.put(line)
        finally:
            self.responses.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Read an explicit successful response within a bounded deadline."""
        import time

        self.request_id += 1
        self._send({"id": self.request_id, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Native RPC timed out: {method}")
            try:
                line = self.responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"Native RPC timed out: {method}") from exc
            if line is None:
                raise RuntimeError(f"Native server exited during {method}")
            message = json.loads(line)
            if message.get("id") != self.request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"Native {method} failed: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise TypeError(f"Native {method} returned no object result")
            return result

    def close(self) -> None:
        """Terminate and reap the server even when a test assertion fails."""
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.stderr):
            if stream is not None:
                stream.close()
        assert not self.reader.is_alive()

    def __exit__(self, *args: object) -> None:
        self.close()


def start_fixture(server: NativeServer, cwd: Path) -> dict[str, Any]:
    """Persist native history by injection, without asking a model to execute."""
    thread = server.request(
        "thread/start", {"cwd": str(cwd), "experimentalRawEvents": False}
    )["thread"]
    server.request(
        "thread/inject_items",
        {
            "threadId": thread["id"],
            "items": [{
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Native fixture"}],
            }],
        },
    )
    return thread


def add_paginated_fixture(account: Path, thread: dict[str, Any]) -> None:
    """Populate protocol-valid synthetic UI history in native-created tables."""
    with sqlite3.connect(account / "thread_history_1.sqlite") as connection:
        connection.execute(
            "INSERT INTO thread_turns("
            "thread_id,turn_id,rollout_ordinal,status,started_at,completed_at,"
            "duration_ms,first_user_item_id,final_agent_item_id,"
            "rollout_byte_offset,rollout_end_byte_offset) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                thread["id"], "fixture-turn", 1, "completed", 1788707000,
                1788707001, 1000, "fixture-user", "fixture-agent", 0,
                Path(thread["path"]).stat().st_size,
            ),
        )
        items = [
            {
                "type": "userMessage", "id": "fixture-user", "clientId": None,
                "content": [{
                    "type": "text", "text": "PORTABLE USER MARKER",
                    "text_elements": [],
                }],
            },
            {
                "type": "agentMessage", "id": "fixture-agent",
                "text": "PORTABLE AGENT MARKER", "phase": "final_answer",
                "memoryCitation": None,
            },
        ]
        for ordinal, item in enumerate(items, 1):
            connection.execute(
                "INSERT INTO thread_items(thread_id,turn_id,item_id,"
                "rollout_ordinal,created_at_ms,item_json,item_type,"
                "updated_at_ordinal) VALUES(?,?,?,?,?,?,?,?)",
                (
                    thread["id"], "fixture-turn", item["id"], ordinal,
                    1788707000000 + ordinal, json.dumps(item), item["type"], ordinal,
                ),
            )


def test_native_codex_reads_transferred_paginated_history(tmp_path: Path) -> None:
    """Native resume must hydrate imported markers and honor destination cwd."""
    version = subprocess.run(
        ["codex", "--version"], check=True, capture_output=True, text=True,
        timeout=10,
    ).stdout.strip()
    if version != "codex-cli 0.153.4":
        pytest.skip(f"Native schema fixture requires 0.153.4; installed {version}")
    source, destination = tmp_path / "source", tmp_path / "destination"
    old, new = tmp_path / "old", tmp_path / "new"
    for directory in (source, destination, old, new):
        directory.mkdir()
    with NativeServer(tmp_path, source, tmp_path / "source.stderr") as server:
        thread = start_fixture(server, old)
    add_paginated_fixture(source, thread)
    with NativeServer(tmp_path, destination, tmp_path / "init.stderr") as server:
        start_fixture(server, new)
    staging = tmp_path / "bundle"
    manifest = export_session(source, thread["id"], destination, new, staging)
    assert manifest["ok"] is True
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staging / "files" / relative, target)
    assert import_databases(manifest, destination)["ok"] is True
    with NativeServer(tmp_path, destination, tmp_path / "resume.stderr") as server:
        resumed = server.request(
            "thread/resume", {"threadId": thread["id"], "cwd": str(new)}
        )
        assert Path(resumed["cwd"]).resolve() == new.resolve()
        assert Path(resumed["thread"]["cwd"]).resolve() == new.resolve()
        turns = server.request("thread/turns/list", {"threadId": thread["id"]})
        items = server.request(
            "thread/items/list",
            {"threadId": thread["id"], "turnId": "fixture-turn"},
        )
        assert [turn["id"] for turn in turns["data"]] == ["fixture-turn"]
        assert items["nextCursor"] is None
        assert len(items["data"]) == 2
        user, agent = [row["item"] for row in items["data"]]
        assert user["content"][0]["text"] == "PORTABLE USER MARKER"
        assert agent["text"] == "PORTABLE AGENT MARKER"
        assert "PORTABLE USER MARKER" in json.dumps(resumed["thread"]["turns"])
