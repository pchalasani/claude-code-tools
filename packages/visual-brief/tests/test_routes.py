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
        ("anything.invalid", "/r/demo-run/"),
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
        "127.0.0.1",
        "Demo-Run.localhost",
        None,
        "garbage.invalid",
    ],
)
def test_bare_or_garbage_host_reaches_dashboard(host: str | None) -> None:
    """A request without a valid run subdomain reaches the dashboard."""
    assert route_request(host, "/") == Route("dashboard")


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
    """Health is available regardless of the Host header."""
    assert route_request("demo-run.localhost", "/health") == Route("health")
