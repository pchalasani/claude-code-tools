"""A very small Chrome DevTools Protocol client for the browser suites.

The browser CLI these tests drive can open pages and read them back, but it
cannot turn a Chrome media preference on: ``agent-browser set media
reduced-motion`` reports success and changes nothing, and the page still
matches ``(prefers-reduced-motion: no-preference)`` afterwards. The preference
is reachable one level down, over the DevTools protocol the same browser
already exposes, so this module speaks just enough of that protocol to ask for
one emulation override — a WebSocket handshake, masked text frames, and a call
that waits for its own reply.

Chrome drops every emulation override the moment the client that asked for it
disconnects, so the entry point is a context manager: the preference is real
for exactly as long as the block that reads the page.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator

_TEXT_FRAME = 0x1
_CLOSE_FRAME = 0x8
_PING_FRAME = 0x9
_PONG_FRAME = 0xA
_FINAL = 0x80
_MASKED = 0x80
_TIMEOUT = 15.0


def page_socket_url(cdp_url: str, page_url: str) -> str:
    """Return the DevTools socket URL of the tab showing one page.

    Args:
        cdp_url: Browser-level socket URL, as ``agent-browser get cdp-url``
            prints it.
        page_url: Address the wanted tab is showing.

    Returns:
        The tab's DevTools WebSocket URL.

    Raises:
        AssertionError: If no open tab is showing that address.
    """
    parts = urllib.parse.urlsplit(cdp_url)
    endpoint = f"http://{parts.hostname}:{parts.port}/json/list"
    with urllib.request.urlopen(endpoint, timeout=_TIMEOUT) as response:
        targets: list[dict[str, Any]] = json.loads(response.read())
    pages = [target for target in targets if target.get("type") == "page"]
    for target in pages:
        if str(target.get("url", "")).rstrip("/") == page_url.rstrip("/"):
            return str(target["webSocketDebuggerUrl"])
    raise AssertionError(
        f"no open tab is showing {page_url!r}; "
        f"the browser has {[target.get('url') for target in pages]}"
    )


class DevToolsPage:
    """One open DevTools connection to a single page target."""

    def __init__(self, socket_url: str) -> None:
        """Open the connection.

        Args:
            socket_url: The tab's DevTools WebSocket URL.
        """
        self._socket = _handshake(socket_url)
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one command and return its result.

        Args:
            method: Protocol method name.
            params: Method arguments.

        Returns:
            The command's result object.

        Raises:
            AssertionError: If the browser reports an error for the command.
        """
        self._next_id += 1
        wanted = self._next_id
        _send_text(
            self._socket,
            json.dumps({"id": wanted, "method": method, "params": params}),
        )
        while True:
            message: dict[str, Any] = json.loads(_receive_text(self._socket))
            if message.get("id") != wanted:
                # Everything else on this socket is an unsolicited event.
                continue
            assert "error" not in message, message["error"]
            return dict(message.get("result", {}))

    def close(self) -> None:
        """Close the connection, dropping every override it asked for."""
        self._socket.close()


@contextmanager
def emulated_media(
    cdp_url: str,
    page_url: str,
    features: dict[str, str],
) -> Iterator[DevToolsPage]:
    """Hold Chrome media features at chosen values for one block.

    Args:
        cdp_url: Browser-level socket URL from ``agent-browser get cdp-url``.
        page_url: Address of the tab to emulate the features in.
        features: Media feature names mapped to the values to report.

    Yields:
        The open connection, in case the block wants more of the protocol.
    """
    page = DevToolsPage(page_socket_url(cdp_url, page_url))
    try:
        page.call(
            "Emulation.setEmulatedMedia",
            {
                "features": [
                    {"name": name, "value": value}
                    for name, value in features.items()
                ]
            },
        )
        yield page
    finally:
        page.close()


def press_enter(cdp_url: str, page_url: str) -> None:
    """Send Chrome's complete native Enter sequence to an attached tab.

    The attached-browser driver omits the character event, so Chrome delivers
    ``keydown`` but performs neither button activation nor textarea insertion.
    Sending the complete protocol sequence preserves those browser defaults.

    Args:
        cdp_url: Browser-level socket URL.
        page_url: Address of the tab receiving Enter.
    """
    page = DevToolsPage(page_socket_url(cdp_url, page_url))
    event = {
        "key": "Enter",
        "code": "Enter",
        "windowsVirtualKeyCode": 13,
        "nativeVirtualKeyCode": 13,
        "modifiers": 0,
    }
    try:
        page.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **event})
        page.call(
            "Input.dispatchKeyEvent",
            {
                "type": "char",
                "text": "\r",
                "unmodifiedText": "\r",
                **event,
            },
        )
        page.call("Input.dispatchKeyEvent", {"type": "keyUp", **event})
    finally:
        page.close()


def _handshake(socket_url: str) -> socket.socket:
    """Open a WebSocket connection to one DevTools target.

    Args:
        socket_url: A ``ws://`` DevTools URL.

    Returns:
        The connected socket, ready for frames.

    Raises:
        AssertionError: If the server declines to upgrade the connection.
    """
    parts = urllib.parse.urlsplit(socket_url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    connection = socket.create_connection((host, port), timeout=_TIMEOUT)
    key = base64.b64encode(os.urandom(16)).decode()
    connection.sendall(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode()
    )
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = connection.recv(4096)
        assert chunk, "the browser closed the connection during the handshake"
        reply += chunk
    status = reply.split(b"\r\n", 1)[0]
    assert b" 101 " in status, status.decode(errors="replace")
    return connection


def _send_text(connection: socket.socket, text: str) -> None:
    """Write one masked text frame.

    Args:
        connection: The open socket.
        text: The frame's payload.
    """
    _send_frame(connection, _TEXT_FRAME, text.encode())


def _send_frame(connection: socket.socket, opcode: int, payload: bytes) -> None:
    """Write one masked frame, as every client frame has to be.

    Args:
        connection: The open socket.
        opcode: The frame's opcode.
        payload: The frame's payload.
    """
    header = bytearray([_FINAL | opcode])
    length = len(payload)
    if length < 126:
        header.append(_MASKED | length)
    elif length < 1 << 16:
        header.append(_MASKED | 126)
        header += struct.pack(">H", length)
    else:
        header.append(_MASKED | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    header += mask
    header += bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    connection.sendall(bytes(header))


def _receive_text(connection: socket.socket) -> str:
    """Read frames until one carries text.

    Args:
        connection: The open socket.

    Returns:
        The payload of the next text frame.

    Raises:
        AssertionError: If the browser closes the connection first.
    """
    while True:
        first, second = _receive_exactly(connection, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", _receive_exactly(connection, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _receive_exactly(connection, 8))[0]
        payload = _receive_exactly(connection, length) if length else b""
        if opcode == _TEXT_FRAME:
            return payload.decode()
        if opcode == _PING_FRAME:
            _send_frame(connection, _PONG_FRAME, payload)
        elif opcode == _CLOSE_FRAME:
            raise AssertionError("the browser closed the DevTools connection")


def _receive_exactly(connection: socket.socket, count: int) -> bytes:
    """Read a fixed number of bytes.

    Args:
        connection: The open socket.
        count: How many bytes to read.

    Returns:
        Exactly ``count`` bytes.

    Raises:
        AssertionError: If the connection ends first.
    """
    chunks = bytearray()
    while len(chunks) < count:
        chunk = connection.recv(count - len(chunks))
        assert chunk, "the DevTools connection ended mid-frame"
        chunks += chunk
    return bytes(chunks)
