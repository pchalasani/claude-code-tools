"""Pure request routing for the visual brief daemon."""

from __future__ import annotations

from dataclasses import dataclass

from visual_brief.server.registry import is_valid_run_id


@dataclass(frozen=True, slots=True)
class Route:
    """A parsed visual brief request route."""

    endpoint: str
    run_id: str | None = None


def route_request(host_header: str | None, request_path: str) -> Route:
    """Route a request using its host and URL path.

    Only loopback authorities reach the router. On those authorities,
    path-based run routes take precedence over a subdomain.

    Args:
        host_header: Raw Host header, including an optional port.
        request_path: URL path without query parameters.

    Returns:
        The parsed route.
    """
    host = _host_name(host_header)
    run_id = _run_subdomain(host)
    if host not in {"localhost", "127.0.0.1"} and run_id is None:
        return Route("invalid_host")

    if request_path == "/health":
        return Route("health")

    path_route = _path_run_route(request_path)
    if path_route is not None:
        return path_route

    if run_id is not None:
        endpoint = _run_endpoint(request_path)
        if endpoint is not None:
            return Route(endpoint, run_id)
        return Route("not_found", run_id)

    if request_path == "/":
        return Route("dashboard")
    return Route("not_found")


def run_id_from_host(host_header: str | None) -> str | None:
    """Extract a valid run id from a ``<run-id>.localhost`` Host value.

    Args:
        host_header: Raw Host header.

    Returns:
        A valid run id, or ``None`` for bare and malformed hosts.
    """
    return _run_subdomain(_host_name(host_header))


def _run_subdomain(host: str) -> str | None:
    """Recognize only a single valid run id under localhost."""
    run_id, dot, domain = host.partition(".")
    if dot and domain == "localhost" and is_valid_run_id(run_id):
        return run_id
    return None


def _host_name(authority: str | None) -> str:
    """Read an ASCII hostname with an optional numeric TCP port."""
    if not authority or not authority.isascii():
        return ""
    name, colon, port = authority.strip(" \t").partition(":")
    if colon and not (port.isdigit() and len(port) <= 5 and int(port) <= 65535):
        return ""
    return name.lower()


def _path_run_route(path: str) -> Route | None:
    """Parse the fallback ``/r/<run-id>/`` route form."""
    if not path.startswith("/r/"):
        return None
    parts = path.split("/")
    if len(parts) < 4:
        return Route("not_found")
    run_id = parts[2]
    if not is_valid_run_id(run_id):
        return Route("not_found")
    remainder = "/" + "/".join(parts[3:])
    endpoint = _run_endpoint(remainder)
    if endpoint is None:
        return Route("not_found", run_id)
    return Route(endpoint, run_id)


def _run_endpoint(path: str) -> str | None:
    """Map a run-relative path to an endpoint name."""
    endpoints = {
        "": "run",
        "/": "run",
        "/version": "version",
        "/render-version": "render_version",
        "/document": "document",
        "/ask": "ask",
        "/signal": "signal",
    }
    return endpoints.get(path)
