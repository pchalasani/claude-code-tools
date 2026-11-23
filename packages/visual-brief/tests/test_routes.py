"""Tests for Host-based and path-based request routing."""

from __future__ import annotations

import pytest

from visual_brief.server.routes import Route, route_request, run_id_from_host


@pytest.mark.parametrize(
    ("host", "path"),
    [
        ("demo-run.localhost", "/"),
        ("demo-run.localhost:8765", "/"),
        ("localhost", "/r/demo-run/"),
        ("127.0.0.1:8765", "/r/demo-run/"),
    ],
)
def test_both_url_forms_reach_same_run(host: str, path: str) -> None:
    """Every supported URL form routes to the requested run."""
    assert route_request(host, path) == Route("run", "demo-run")


@pytest.mark.parametrize(
    ("host", "path", "endpoint"),
    [
        ("demo-run.localhost", "/version", "version"),
        ("localhost", "/r/demo-run/version", "version"),
        ("demo-run.localhost", "/render-version", "render_version"),
        ("localhost", "/r/demo-run/render-version", "render_version"),
        ("demo-run.localhost", "/document", "document"),
        ("localhost", "/r/demo-run/document", "document"),
        ("demo-run.localhost", "/ask", "ask"),
        ("localhost", "/r/demo-run/ask", "ask"),
        ("demo-run.localhost", "/signal", "signal"),
        ("localhost", "/r/demo-run/signal", "signal"),
    ],
)
def test_run_endpoints_work_in_both_forms(
    host: str,
    path: str,
    endpoint: str,
) -> None:
    """Every run-scoped endpoint shares routing behavior."""
    assert route_request(host, path) == Route(endpoint, "demo-run")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8765",
        "localhost:0008765",
        "127.0.0.1",
        "LOCALHOST",
    ],
)
def test_bare_loopback_host_reaches_dashboard(host: str) -> None:
    """Bare loopback names serve the dashboard."""
    assert route_request(host, "/") == Route("dashboard")


@pytest.mark.parametrize(
    "authority",
    [
        None,
        "",
        "example.invalid",
        "Demo-Run.localhost",
        "nested.demo.localhost",
        "localhost:bad",
        "localhost:99999",
        "localhost:80:80",
        "[localhost]",
        "localhost, localhost",
    ],
)
@pytest.mark.parametrize("path", ["/health", "/", "/r/demo-run/"])
def test_unrecognized_authority_is_rejected_before_routing(
    authority: str | None, path: str
) -> None:
    """The authority check takes precedence over every kind of path."""
    assert route_request(authority, path) == Route("invalid_host")


def test_query_only_target_is_not_a_dashboard_path() -> None:
    """A relative target needs a path to select the dashboard."""
    assert route_request("localhost", "?view=all") == Route("not_found")


def test_absolute_target_requires_a_valid_port() -> None:
    """An absolute URL contains one numeric port at most."""
    route = route_request("localhost:8000", "http://localhost:8000:80/health")
    assert route == Route("invalid_host")


def test_host_port_is_removed_before_run_id_parsing() -> None:
    """The Host header's port is not treated as part of a run id."""
    assert run_id_from_host("my-run.localhost:43210") == "my-run"


@pytest.mark.parametrize(
    "path",
    [
        "/r/unknown/path",
        "/r/../../etc",
        "/r/A/",
        "/r/x-/",
    ],
)
def test_bad_path_route_is_not_found(path: str) -> None:
    """Malformed and unsupported run paths become ordinary 404 routes."""
    assert route_request("localhost", path).endpoint == "not_found"


def test_health_is_not_run_scoped() -> None:
    """Health is available on each allowed local hostname."""
    assert route_request("demo-run.localhost", "/health") == Route("health")
