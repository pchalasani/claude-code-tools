"""Exercise the real transport against a bounded localhost HTTP server."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from jev_prose.backend import Backend, DetectorError


@dataclass
class Reply:
    """A literal HTTP response from the local service."""

    body: Any
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    raw: bool = False


@dataclass
class Service:
    """Record requests and the peak number of overlapping handlers."""

    respond: Callable[[dict[str, Any]], Reply]
    url: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    active: int = 0
    peak: int = 0
    lock: Any = field(default_factory=threading.Lock)


@contextmanager
def running_server(
    respond: Callable[[dict[str, Any]], Reply],
) -> Iterator[Service]:
    """Run an actual HTTP endpoint and always close its socket and thread."""
    service = Service(respond)

    class Handler(BaseHTTPRequestHandler):
        """Serve a recorded request with the caller's response function."""

        def do_POST(self) -> None:
            """Record and answer one JSON POST request."""
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with service.lock:
                service.requests.append(request)
                service.headers.append(dict(self.headers))
                service.active += 1
                service.peak = max(service.peak, service.active)
            try:
                reply = service.respond(request)
                data = reply.body if reply.raw else json.dumps(reply.body).encode()
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                with service.lock:
                    service.active -= 1

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress routine HTTP request logging."""

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    service.url = f'http://127.0.0.1:{server.server_port}/v1/systemone'
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert service.active == 0


def test_direct_request_preserves_state_questions_and_auth() -> None:
    """The transport emits the documented payload and bearer authentication."""
    result = {'model': 'jev-fixed', 'answers': {}}
    state = {'text': 'A draft.'}
    questions = {'one': {'type': 'noul', 'instructions': 'A question?'}}
    with running_server(lambda request: Reply(result)) as service:
        backend = Backend(service.url, 'jev-fixed', token='local-test-token')
        assert backend.decide(state, questions) == result
        assert service.requests == [{
            'state': state, 'questions': questions, 'model': 'jev-fixed',
        }]
        assert service.headers[0]['Authorization'] == 'Bearer local-test-token'
        assert 'local-test-token' not in repr(backend)


@pytest.mark.parametrize('nested', [False, True])
def test_cloudflare_success_unwraps_result(nested: bool) -> None:
    """Both completed nested results and direct results are accepted."""
    result = {'model': 'jev-fixed', 'answers': {}}
    body = {'success': True, 'result': (
        {'state': 'Completed', 'result': result} if nested else result
    )}
    with running_server(lambda request: Reply(body)) as service:
        backend = Backend(service.url, 'jev-fixed', cloudflare=True)
        assert backend.decide({'text': 'Draft.'}, {}) == result
        assert service.requests[0] == {
            'model': 'jev-fixed',
            'input': {'state': {'text': 'Draft.'}, 'questions': {}},
        }


@pytest.mark.parametrize('reply', [
    Reply({}, status=201),
    Reply({}, status=204),
    Reply({}, status=400),
    Reply({}, status=402),
    Reply({}, status=429),
    Reply({}, status=500),
    Reply(b'not json', raw=True),
    Reply(b'\xff', raw=True),
    Reply(b'x' * 8_000_001, raw=True),
    Reply([]),
    Reply(None),
    Reply('unexpected string'),
])
def test_transport_rejects_bad_response(reply: Reply) -> None:
    """HTTP failures and malformed or oversized responses are never results."""
    with running_server(lambda request: reply) as service:
        with pytest.raises(DetectorError):
            Backend(service.url, 'jev-fixed').decide({'text': 'Draft.'}, {})
        assert len(service.requests) == 1


@pytest.mark.parametrize('body', [
    {'success': False, 'result': {}},
    {'success': 1, 'result': {}},
    {'result': {}},
    {'success': True},
    {'success': True, 'result': []},
    {'success': True, 'result': {'state': 'Running'}},
    {'success': True, 'result': {'state': 'Failed'}},
    {'success': True, 'result': {'state': 'Completed', 'result': None}},
])
def test_cloudflare_rejects_unsuccessful_or_incomplete(body: Any) -> None:
    """Cloudflare success and completion indicators must be usable."""
    with running_server(lambda request: Reply(body)) as service:
        with pytest.raises(DetectorError):
            Backend(service.url, 'jev-fixed', cloudflare=True).decide({}, {})


def test_redirect_is_not_followed() -> None:
    """A redirect must not forward input or credentials to another server."""
    with running_server(lambda request: Reply({})) as destination:
        redirect = Reply({}, status=302, headers={'Location': destination.url})
        with running_server(lambda request: redirect) as origin:
            with pytest.raises(DetectorError, match='302'):
                Backend(origin.url, 'jev-fixed', token='secret').decide({}, {})
        assert destination.requests == []


def test_connection_refused_is_failure() -> None:
    """A stopped endpoint cannot be mistaken for an empty successful result."""
    with running_server(lambda request: Reply({})) as service:
        url = service.url
    with pytest.raises(DetectorError):
        Backend(url, 'jev-fixed', timeout=1).decide({}, {})


@contextmanager
def truncated_chunked_server() -> Iterator[str]:
    """Serve a chunked body that stops mid-chunk, as a cut connection would."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    # Never block forever: a transport that fails before connecting must not
    # leave this helper waiting in accept().
    listener.settimeout(5)

    def serve() -> None:
        """Answer one request with a chunk header promising absent bytes."""
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        try:
            connection.settimeout(5)
            connection.recv(65_536)
            connection.sendall(
                b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                b'Transfer-Encoding: chunked\r\n\r\n'
                b'20\r\n{"answers": {}, "mod'
            )
            # End the write side cleanly and drain the rest of the request, so
            # the client reads a mid-chunk EOF instead of a connection reset.
            connection.shutdown(socket.SHUT_WR)
            while connection.recv(65_536):
                pass
        except OSError:
            pass
        finally:
            connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{listener.getsockname()[1]}/v1/systemone'
    finally:
        # Release the socket and the thread before asserting on either, so a
        # failed assertion cannot leave the interpreter unable to exit.
        thread.join(timeout=10)
        alive = thread.is_alive()
        listener.close()
        assert not alive


def test_truncated_response_is_a_structured_failure() -> None:
    """An interrupted body fails as a detector error, not an HTTP exception."""
    with truncated_chunked_server() as url:
        backend = Backend(url, 'jev-fixed', timeout=5)
        with pytest.raises(DetectorError) as failure:
            backend.decide({'text': 'A draft.'}, {
                'a': {'type': 'noul', 'instructions': 'A question?'},
            })
        # Pin the exact cause: a reset raises OSError, which was always
        # caught, and RemoteDisconnected is both an OSError and an
        # HTTPException, so the broader check could pass for the wrong reason.
        assert isinstance(failure.value.__cause__, IncompleteRead)
        # The request did reach the endpoint, so the report must say so.
        assert backend.attempted is True


def test_deeply_nested_response_is_a_structured_failure() -> None:
    """A body nested past the JSON parser's limit is a failure, not a crash."""
    body = b'[' * 200_000 + b']' * 200_000
    with running_server(lambda request: Reply(body, raw=True)) as service:
        backend = Backend(service.url, 'jev-fixed', timeout=10)
        with pytest.raises(DetectorError):
            backend.decide({'text': 'A draft.'}, {
                'a': {'type': 'noul', 'instructions': 'A question?'},
            })
        assert backend.attempted is True


def test_timeout_is_failure() -> None:
    """A transport timeout must not create an empty clean report."""
    def respond(request: dict[str, Any]) -> Reply:
        """Delay a real response beyond the configured socket deadline."""
        time.sleep(0.1)
        return Reply({})

    with running_server(respond) as service:
        with pytest.raises(DetectorError):
            Backend(service.url, 'jev-fixed', timeout=0.01).decide({}, {})
        assert len(service.requests) == 1
