"""How the visual brief daemon answers, separated from what it answers.

Every reply the daemon makes is one of three shapes — a JSON object, a JSON
error, or a body with a declared type — and each carries the same headers. They
live here so the handler next door is about routing and run state rather than
about writing response lines.
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any


class JsonResponder(BaseHTTPRequestHandler):
    """A request handler that answers in JSON and logs concisely."""

    protocol_version = "HTTP/1.1"

    def _send_json(
        self,
        status: HTTPStatus,
        value: dict[str, Any],
        *,
        send_body: bool = True,
    ) -> None:
        """Send a JSON response.

        Args:
            status: HTTP status to answer with.
            value: Object to serialize as the body.
            send_body: Whether to write the body, as HEAD does not.
        """
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(
            status,
            body,
            "application/json; charset=utf-8",
            send_body=send_body,
        )

    def _json_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        send_body: bool = True,
    ) -> None:
        """Send a JSON error response.

        Args:
            status: HTTP status to answer with.
            message: What went wrong, in the one shape every error uses.
            send_body: Whether to write the body, as HEAD does not.
        """
        self._send_json(status, {"error": message}, send_body=send_body)

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        send_body: bool,
    ) -> None:
        """Send response headers and an optional body.

        Args:
            status: HTTP status to answer with.
            body: The response body, whose length is always declared.
            content_type: Value of the ``Content-Type`` header.
            send_body: Whether to write the body, as HEAD does not.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        """Write a concise request log to standard error.

        Args:
            format_string: The base class's format string.
            args: Its arguments.
        """
        print(
            f"{self.address_string()} - {format_string % args}",
            file=sys.stderr,
        )
