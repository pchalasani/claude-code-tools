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

    Path-based run routes take precedence over the host. Invalid and absent
    hosts behave as a bare dashboard host.

    Args:
        host_header: Raw Host header, including an optional port.
        request_path: URL path without query parameters.

    Returns:
        The parsed route.
    """
    if request_path == "/health":
        return Route("health")

    path_route = _path_run_route(request_path)
    if path_route is not None:
        return path_route

    run_id = run_id_from_host(host_header)
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
    if not host_header:
        return None
    host = _strip_port(host_header.strip())
    suffix = ".localhost"
    if not host.endswith(suffix):
        return None
    run_id = host[: -len(suffix)]
    if "." in run_id or not is_valid_run_id(run_id):
        return None
    return run_id


def _strip_port(host: str) -> str:
    """Remove the port portion from an HTTP Host value."""
    if host.startswith("["):
        closing = host.find("]")
        return host[1:closing] if closing >= 0 else ""
    name, separator, port = host.rpartition(":")
    if separator and port.isdecimal():
        return name
    return host


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
        "/ask": "ask",
        "/signal": "signal",
    }
    return endpoints.get(path)
