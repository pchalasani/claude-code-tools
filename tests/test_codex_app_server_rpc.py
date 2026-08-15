"""Protocol tests for read-only Codex App Server queries."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import socket
import struct
import tempfile
import threading
from pathlib import Path
from typing import BinaryIO, cast

from claude_code_tools.codex_app_server_rpc import (
    deliver_thread_message,
    loaded_thread_ids,
    verify_thread_loaded,
)

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    assert data is not None and len(data) == size
    return data


def _read_client_json(stream: BinaryIO) -> dict[str, object]:
    first, second = _read_exact(stream, 2)
    assert first & 0x0F == 1
    assert second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(stream, 8))[0]
    mask = _read_exact(stream, 4)
    payload = _read_exact(stream, length)
    decoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    value = json.loads(decoded)
    assert isinstance(value, dict)
    return value


def _send_server_json(connection: socket.socket, value: object) -> None:
    payload = json.dumps(value).encode("utf-8")
    if len(payload) < 126:
        header = bytes([0x81, len(payload)])
    else:
        assert len(payload) <= 0xFFFF
        header = bytes([0x81, 126]) + struct.pack("!H", len(payload))
    connection.sendall(header + payload)


def _serve_loaded_threads(
    listener: socket.socket,
    requests: list[dict[str, object]],
) -> None:
    connection, _address = listener.accept()
    with connection, connection.makefile("rb") as stream:
        headers: dict[str, str] = {}
        assert stream.readline().startswith(b"GET /rpc ")
        while True:
            line = stream.readline()
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            headers[name.lower()] = value.strip()
        accept = base64.b64encode(
            hashlib.sha1(
                (headers["sec-websocket-key"] + _GUID).encode("ascii")
            ).digest()
        ).decode("ascii")
        connection.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        )
        initialize = _read_client_json(stream)
        requests.append(initialize)
        _send_server_json(connection, {"id": initialize["id"], "result": {}})
        requests.append(_read_client_json(stream))
        loaded = _read_client_json(stream)
        requests.append(loaded)
        _send_server_json(
            connection,
            {
                "id": loaded["id"],
                "result": {"data": ["thread-one", "thread-two"]},
            },
        )


def test_loaded_thread_ids_uses_read_only_app_server_request(
) -> None:
    """The query initializes, lists loaded IDs, and closes without resuming."""
    root = Path(tempfile.mkdtemp(prefix="cct-rpc-", dir="/tmp"))
    socket_path = root / "app-server.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    requests: list[dict[str, object]] = []
    server = threading.Thread(
        target=_serve_loaded_threads,
        args=(listener, requests),
    )
    server.start()
    try:
        result = loaded_thread_ids(socket_path)
    finally:
        server.join(timeout=2)
        listener.close()
        shutil.rmtree(root)

    assert not server.is_alive()
    assert result == frozenset({"thread-one", "thread-two"})
    assert [request["method"] for request in requests] == [
        "initialize",
        "initialized",
        "thread/loaded/list",
    ]
    assert requests[-1]["params"] == {}


def _serve_thread_delivery(
    listener: socket.socket,
    requests: list[dict[str, object]],
    status: str,
) -> None:
    """Serve initialization, a bounded status read, and delivery."""
    connection, _address = listener.accept()
    with connection, connection.makefile("rb") as stream:
        headers: dict[str, str] = {}
        assert stream.readline().startswith(b"GET /rpc ")
        while True:
            line = stream.readline()
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            headers[name.lower()] = value.strip()
        accept = base64.b64encode(
            hashlib.sha1(
                (headers["sec-websocket-key"] + _GUID).encode("ascii")
            ).digest()
        ).decode("ascii")
        connection.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        )
        initialize = _read_client_json(stream)
        requests.append(initialize)
        _send_server_json(connection, {"id": initialize["id"], "result": {}})
        requests.append(_read_client_json(stream))
        read = _read_client_json(stream)
        requests.append(read)
        _send_server_json(
            connection,
            {
                "id": read["id"],
                "result": {
                    "thread": {
                        "id": "thread-one",
                        "status": {"type": status},
                    }
                },
            },
        )
        if status == "active":
            turns = _read_client_json(stream)
            requests.append(turns)
            _send_server_json(
                connection,
                {
                    "id": turns["id"],
                    "result": {
                        "data": [
                            {
                                "id": "active-turn",
                                "status": "inProgress",
                                "items": [],
                            }
                        ],
                        "nextCursor": None,
                    },
                },
            )
        delivery = _read_client_json(stream)
        requests.append(delivery)
        _send_server_json(
            connection,
            {"id": delivery["id"], "result": {"turnId": "new-turn"}},
        )


def test_deliver_thread_message_starts_idle_turn() -> None:
    """An idle origin receives a new turn with an idempotency key."""
    root = Path(tempfile.mkdtemp(prefix="cct-rpc-", dir="/tmp"))
    socket_path = root / "app-server.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    requests: list[dict[str, object]] = []
    server = threading.Thread(
        target=_serve_thread_delivery,
        args=(listener, requests, "idle"),
    )
    server.start()
    try:
        turn_id = deliver_thread_message(
            socket_path,
            "thread-one",
            "github-wake:watch:comment",
            "reply arrived",
        )
    finally:
        server.join(timeout=2)
        listener.close()
        shutil.rmtree(root)

    assert turn_id == "new-turn"
    assert requests[-1]["method"] == "turn/start"
    params = cast(dict[str, object], requests[-1]["params"])
    assert params["clientUserMessageId"] == (
        "github-wake:watch:comment"
    )


def test_deliver_thread_message_steers_active_turn() -> None:
    """A busy origin receives a steer targeting its in-progress turn."""
    root = Path(tempfile.mkdtemp(prefix="cct-rpc-", dir="/tmp"))
    socket_path = root / "app-server.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    requests: list[dict[str, object]] = []
    server = threading.Thread(
        target=_serve_thread_delivery,
        args=(listener, requests, "active"),
    )
    server.start()
    try:
        deliver_thread_message(
            socket_path,
            "thread-one",
            "github-wake:watch:comment",
            "reply arrived",
        )
    finally:
        server.join(timeout=2)
        listener.close()
        shutil.rmtree(root)

    assert requests[-1]["method"] == "turn/steer"
    params = cast(dict[str, object], requests[-1]["params"])
    assert params["expectedTurnId"] == "active-turn"


def _serve_thread_read(listener: socket.socket) -> None:
    """Serve one initialized thread/read preflight."""
    connection, _address = listener.accept()
    with connection, connection.makefile("rb") as stream:
        headers: dict[str, str] = {}
        assert stream.readline().startswith(b"GET /rpc ")
        while True:
            line = stream.readline()
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            headers[name.lower()] = value.strip()
        accept = base64.b64encode(
            hashlib.sha1(
                (headers["sec-websocket-key"] + _GUID).encode("ascii")
            ).digest()
        ).decode("ascii")
        connection.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        )
        initialize = _read_client_json(stream)
        _send_server_json(connection, {"id": initialize["id"], "result": {}})
        _read_client_json(stream)
        read = _read_client_json(stream)
        _send_server_json(
            connection,
            {
                "id": read["id"],
                "result": {
                    "thread": {
                        "id": "thread-one",
                        "status": {"type": "idle"},
                    }
                },
            },
        )


def test_verify_thread_loaded_accepts_idle_thread() -> None:
    """Wakeup preflight succeeds only through a responding App Server."""
    root = Path(tempfile.mkdtemp(prefix="cct-rpc-", dir="/tmp"))
    socket_path = root / "app-server.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    server = threading.Thread(target=_serve_thread_read, args=(listener,))
    server.start()
    try:
        verify_thread_loaded(socket_path, "thread-one")
    finally:
        server.join(timeout=2)
        listener.close()
        shutil.rmtree(root)

    assert not server.is_alive()
