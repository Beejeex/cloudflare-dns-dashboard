"""
tests/integration/test_action_routes.py

Integration tests for routes/action_routes.py.
Uses FastAPI's TestClient as a context manager so the lifespan starts and stops
cleanly for each test. Depends() providers are overridden with test doubles
backed by the in-memory SQLite fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import app
from dependencies import (
    get_config_service,
    get_log_service,
    get_stats_service,
)
from repositories.config_repository import ConfigRepository
from repositories.stats_repository import StatsRepository
from services.config_service import ConfigService
from services.log_service import LogService
from services.stats_service import StatsService


def _apply_overrides(db_session: Session) -> None:
    """Install dependency overrides backed by the test DB session."""
    config_repo = ConfigRepository(db_session)
    stats_repo = StatsRepository(db_session)
    app.dependency_overrides[get_config_service] = lambda: ConfigService(config_repo)
    app.dependency_overrides[get_stats_service] = lambda: StatsService(stats_repo)
    app.dependency_overrides[get_log_service] = lambda: LogService(db_session)


# ---------------------------------------------------------------------------
# POST /add-to-managed
# ---------------------------------------------------------------------------


def test_add_to_managed_returns_html_fragment(db_session):
    """POST /add-to-managed must return HTML (not a redirect)."""
    _apply_overrides(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/add-to-managed", data={"record_name": "home.example.com"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "home.example.com" in response.text


def test_add_to_managed_persists_record(db_session):
    """POST /add-to-managed must persist the new record to the DB."""
    _apply_overrides(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/add-to-managed", data={"record_name": "vpn.example.com"})
    app.dependency_overrides.clear()

    repo = ConfigRepository(db_session)
    config = repo.load()
    records = repo.get_records(config)
    assert "vpn.example.com" in records


# ---------------------------------------------------------------------------
# POST /remove-from-managed
# ---------------------------------------------------------------------------


def test_remove_from_managed_removes_record(db_session):
    """POST /remove-from-managed must remove the record from the DB."""
    # Seed a record first
    repo = ConfigRepository(db_session)
    config = repo.load()
    repo.set_records(config, ["home.example.com"])
    repo.save(config)

    _apply_overrides(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/remove-from-managed", data={"record_name": "home.example.com"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    # After removal, the table fragment should not contain the record
    updated_records = repo.get_records(repo.load())
    assert "home.example.com" not in updated_records


# ---------------------------------------------------------------------------
# POST /clear-logs
# ---------------------------------------------------------------------------


def test_clear_logs_returns_html_fragment(db_session):
    """POST /clear-logs must return an HTML fragment, not a redirect."""
    _apply_overrides(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/clear-logs")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# POST /reset-updates
# ---------------------------------------------------------------------------


def test_reset_updates_zeroes_counter(db_session):
    """POST /reset-updates must reset the updates counter to zero and return HTML."""
    repo = StatsRepository(db_session)
    repo.record_update("home.example.com")
    repo.record_update("home.example.com")
    config_repo = ConfigRepository(db_session)
    config = config_repo.load()
    config_repo.set_records(config, ["home.example.com"])
    config_repo.save(config)

    _apply_overrides(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/reset-updates", data={"record_name": "home.example.com"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    stats = repo.get_by_name("home.example.com")
    assert stats.updates == 0
