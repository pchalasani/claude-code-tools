"""Small synchronous client for bounded Codex App Server operations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
from pathlib import Path
from typing import BinaryIO

_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_HEADER_BYTES = 32 * 1024
_MAX_MESSAGE_BYTES = 1024 * 1024


class AppServerQueryError(RuntimeError):
    """A failed or malformed App Server operation."""


class _UnixWebSocket:
    """Minimal blocking WebSocket connection over a Unix socket."""

    def __init__(self, path: Path, timeout: float) -> None:
        """Connect and complete the WebSocket HTTP upgrade."""
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._reader: BinaryIO | None = None
        self._request_id = 0
        try:
            self._socket.settimeout(timeout)
            self._socket.connect(str(path))
            self._reader = self._socket.makefile("rb")
            self._upgrade()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Close the connection without affecting the App Server."""
        try:
            if self._reader is not None:
                self._reader.close()
        finally:
            self._socket.close()

    def request(self, method: str, params: object | None = None) -> object:
        """Send one request and return its matching JSON-RPC result."""
        self._request_id += 1
        request_id = self._request_id
        message: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send_json(message)
        while True:
            response = self._receive_json()
            if response.get("id") != request_id:
                continue
            error = response.get("error")
            if error is not None:
                raise AppServerQueryError(
                    f"App Server request {method} failed: {error}"
                )
            if "result" not in response:
                raise AppServerQueryError(
                    f"App Server request {method} returned no result"
                )
            return response["result"]

    def notify(self, method: str, params: object) -> None:
        """Send one JSON-RPC notification."""
        self._send_json({"method": method, "params": params})

    def _upgrade(self) -> None:
        assert self._reader is not None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /rpc HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: websocket\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        status = self._reader.readline(_MAX_HEADER_BYTES + 1)
        if len(status) > _MAX_HEADER_BYTES or b" 101 " not in status:
            raise AppServerQueryError("App Server rejected the WebSocket upgrade")
        headers: dict[bytes, bytes] = {}
        consumed = len(status)
        while True:
            line = self._reader.readline(_MAX_HEADER_BYTES - consumed + 1)
            consumed += len(line)
            if consumed > _MAX_HEADER_BYTES or not line:
                raise AppServerQueryError("App Server returned invalid HTTP headers")
            if line in {b"\r\n", b"\n"}:
                break
            name, separator, value = line.partition(b":")
            if not separator:
                raise AppServerQueryError("App Server returned invalid HTTP headers")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()
        )
        if headers.get(b"sec-websocket-accept") != expected:
            raise AppServerQueryError("App Server returned an invalid handshake")

    def _send_json(self, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_MESSAGE_BYTES:
            raise AppServerQueryError("App Server request is too large")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + masked)

    def _receive_json(self) -> dict[str, object]:
        payload = self._receive_text_frame()
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppServerQueryError("App Server returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise AppServerQueryError("App Server returned a non-object message")
        return value

    def _receive_text_frame(self) -> bytes:
        fragments = bytearray()
        started = False
        while True:
            first, second = self._read_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            if first & 0x70 or second & 0x80:
                raise AppServerQueryError("App Server returned an invalid frame")
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if length > _MAX_MESSAGE_BYTES - len(fragments):
                raise AppServerQueryError("App Server message is too large")
            payload = self._read_exact(length)
            if opcode == 0x8:
                raise AppServerQueryError("App Server closed the connection")
            if opcode == 0x9:
                self._send_control_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1 and not started:
                started = True
            elif opcode != 0x0 or not started:
                raise AppServerQueryError("App Server returned an invalid frame")
            fragments.extend(payload)
            if final:
                return bytes(fragments)

    def _send_control_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
        self._socket.sendall(header + mask + masked)

    def _read_exact(self, size: int) -> bytes:
        assert self._reader is not None
        data = self._reader.read(size)
        if data is None or len(data) != size:
            raise AppServerQueryError("App Server closed the connection")
        return data


def loaded_thread_ids(socket_path: Path, timeout: float = 1.0) -> frozenset[str]:
    """Return the thread IDs currently held in one App Server's memory."""
    client = _UnixWebSocket(socket_path, timeout)
    try:
        client.request(
            "initialize",
            {
                "capabilities": {"optOutNotificationMethods": ["*"]},
                "clientInfo": {
                    "name": "cctools_resume_router",
                    "title": "Codex Resume Router",
                    "version": "1.0.0",
                },
            },
        )
        client.notify("initialized", {})
        result = client.request("thread/loaded/list", {})
    except (OSError, TimeoutError) as exc:
        raise AppServerQueryError(f"cannot query App Server: {exc}") from exc
    finally:
        client.close()
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise AppServerQueryError("App Server returned invalid loaded-thread data")
    data = result["data"]
    if any(not isinstance(item, str) or not item for item in data):
        raise AppServerQueryError("App Server returned invalid loaded-thread data")
    return frozenset(data)


def verify_thread_loaded(
    socket_path: Path,
    thread_id: str,
    timeout: float = 2.0,
) -> None:
    """Require one thread to be loaded by the selected App Server."""
    client = _initialized_client(socket_path, timeout, "cctools_github_wake")
    try:
        result = client.request(
            "thread/read",
            {"includeTurns": False, "threadId": thread_id},
        )
    finally:
        client.close()
    thread = _response_thread(result)
    status = thread.get("status")
    if not isinstance(status, dict) or status.get("type") in {
        "notLoaded",
        "systemError",
    }:
        raise AppServerQueryError(
            f"thread {thread_id} is not loaded by the selected App Server"
        )


def deliver_thread_message(
    socket_path: Path,
    thread_id: str,
    client_id: str,
    text: str,
    timeout: float = 10.0,
) -> str | None:
    """Start or steer a user message into a loaded Codex thread."""
    client = _initialized_client(socket_path, timeout, "cctools_github_wake")
    try:
        read = client.request(
            "thread/read",
            {"includeTurns": False, "threadId": thread_id},
        )
        thread = _response_thread(read)
        status = thread.get("status")
        if not isinstance(status, dict):
            raise AppServerQueryError("App Server returned invalid thread status")
        inputs = [{"text": text, "type": "text"}]
        if status.get("type") == "active":
            turn_id = _active_turn_id(
                client.request(
                    "thread/turns/list",
                    {
                        "itemsView": "summary",
                        "limit": 1,
                        "sortDirection": "desc",
                        "threadId": thread_id,
                    },
                )
            )
            result = client.request(
                "turn/steer",
                {
                    "clientUserMessageId": client_id,
                    "expectedTurnId": turn_id,
                    "input": inputs,
                    "threadId": thread_id,
                },
            )
        elif status.get("type") == "idle":
            result = client.request(
                "turn/start",
                {
                    "clientUserMessageId": client_id,
                    "input": inputs,
                    "threadId": thread_id,
                },
            )
        else:
            raise AppServerQueryError(
                f"thread {thread_id} is not currently deliverable"
            )
    finally:
        client.close()
    if not isinstance(result, dict):
        raise AppServerQueryError("App Server returned invalid delivery data")
    turn_id = result.get("turnId")
    if not isinstance(turn_id, str):
        turn = result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
    return turn_id if isinstance(turn_id, str) else None


def socket_path_from_endpoint(endpoint: str) -> Path:
    """Resolve a local App Server endpoint to its Unix socket path."""
    if not endpoint.startswith("unix://"):
        raise AppServerQueryError("App Server endpoint must use unix://")
    configured = endpoint.removeprefix("unix://")
    if not configured:
        raise AppServerQueryError("App Server endpoint must name a socket")
    return Path(configured).expanduser().resolve()


def _initialized_client(
    socket_path: Path,
    timeout: float,
    name: str,
) -> _UnixWebSocket:
    """Connect and initialize a bounded helper client."""
    client: _UnixWebSocket | None = None
    try:
        client = _UnixWebSocket(socket_path, timeout)
        client.request(
            "initialize",
            {
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "item/agentMessage/delta",
                        "item/commandExecution/outputDelta",
                        "item/reasoning/summaryTextDelta",
                        "thread/tokenUsage/updated",
                    ]
                },
                "clientInfo": {
                    "name": name,
                    "title": "Claude Code Tools Notification",
                    "version": "1.0.0",
                },
            },
        )
        client.notify("initialized", {})
        return client
    except (OSError, TimeoutError) as exc:
        if client is not None:
            client.close()
        raise AppServerQueryError(f"cannot query App Server: {exc}") from exc
    except Exception:
        if client is not None:
            client.close()
        raise


def _response_thread(value: object) -> dict[str, object]:
    """Extract a thread object from one App Server response."""
    if not isinstance(value, dict) or not isinstance(value.get("thread"), dict):
        raise AppServerQueryError("App Server returned invalid thread data")
    return value["thread"]


def _active_turn_id(value: object) -> str:
    """Return the newest in-progress turn from a bounded turns page."""
    if not isinstance(value, dict):
        raise AppServerQueryError("App Server returned invalid turn data")
    turns = value.get("data")
    if not isinstance(turns, list):
        raise AppServerQueryError("active thread returned no turns")
    for turn in reversed(turns):
        if (
            isinstance(turn, dict)
            and turn.get("status") == "inProgress"
            and isinstance(turn.get("id"), str)
        ):
            return turn["id"]
    raise AppServerQueryError("active thread returned no in-progress turn")
