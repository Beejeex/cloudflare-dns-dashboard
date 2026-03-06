"""
tests/integration/test_api_routes.py

Integration tests for routes/api_routes.py.
Uses FastAPI's TestClient as a context manager so the lifespan starts and stops
cleanly for each test. Depends() providers are overridden with test doubles
backed by the in-memory SQLite fixture.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from dependencies import get_config_service, get_log_service, get_stats_service
from repositories.config_repository import ConfigRepository
from repositories.stats_repository import StatsRepository
from services.config_service import ConfigService
from services.log_service import LogService
from services.stats_service import StatsService


def test_health_endpoint_returns_ok():
    """GET /health must return {"status": "ok"} with HTTP 200."""
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_recent_logs_returns_html(db_session):
    """GET /api/logs/recent must return an HTML fragment for HTMX polling."""
    config_repo = ConfigRepository(db_session)
    stats_repo = StatsRepository(db_session)
    app.dependency_overrides[get_config_service] = lambda: ConfigService(config_repo)
    app.dependency_overrides[get_stats_service] = lambda: StatsService(stats_repo)
    app.dependency_overrides[get_log_service] = lambda: LogService(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/logs/recent")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_health_json_returns_ok():
    """GET /api/health/json must return {"status": "ok"}."""
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/health/json")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# SSE /api/events
# ---------------------------------------------------------------------------


def test_sse_events_route_is_registered():
    """GET /api/events must be registered as a route in the FastAPI app.

    Full end-to-end SSE streaming is tested implicitly by the BroadcastService
    unit tests; live HTTP streaming tests require a real server and are out of
    scope for the synchronous TestClient.
    """
    route_paths = {
        getattr(route, "path", None) for route in app.routes
    }
    assert "/api/events" in route_paths


def test_sse_broadcaster_publishes_event_to_subscriber():
    """Events published after SSE subscribe must be retrievable from the queue.

    Verifies the integration point between the broadcaster (injected into the
    SSE endpoint via Depends) and the per-client subscriber queue.
    """
    import asyncio
    from services.broadcast_service import BroadcastService

    svc = BroadcastService()
    q = svc.subscribe()
    svc.publish("ip_updated", "1.2.3.4")
    msg = q.get_nowait()

    assert msg["event"] == "ip_updated"
    assert msg["data"] == "1.2.3.4"
    assert len(svc._queues) == 1  # Queue still registered until unsubscribe

    svc.unsubscribe(q)
    assert len(svc._queues) == 0
