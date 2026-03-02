"""
tests/unit/test_stats_repository.py

Unit tests for repositories/stats_repository.py.
Uses the in-memory SQLite db_session fixture from conftest.py.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from repositories.stats_repository import StatsRepository


_RECORD = "home.example.com"


def test_get_or_create_creates_new_row(db_session):
    """get_or_create must create a new row when none exists."""
    repo = StatsRepository(db_session)
    stats = repo.get_or_create(_RECORD)

    assert stats.record_name == _RECORD
    assert stats.updates == 0
    assert stats.failures == 0


def test_get_or_create_returns_existing_row(db_session):
    """get_or_create must not duplicate a row on repeated calls."""
    repo = StatsRepository(db_session)
    first = repo.get_or_create(_RECORD)
    second = repo.get_or_create(_RECORD)

    assert first.id == second.id


def test_record_check_updates_timestamp(db_session):
    """record_check must set last_checked to a recent datetime."""
    repo = StatsRepository(db_session)
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    stats = repo.record_check(_RECORD)

    assert stats.last_checked is not None
    assert stats.last_checked >= before


def test_record_update_increments_counter(db_session):
    """record_update must increment the updates counter."""
    repo = StatsRepository(db_session)
    repo.get_or_create(_RECORD)
    stats = repo.record_update(_RECORD)

    assert stats.updates == 1
    assert stats.last_updated is not None


def test_record_failure_increments_counter(db_session):
    """record_failure must increment the failures counter."""
    repo = StatsRepository(db_session)
    repo.get_or_create(_RECORD)
    stats = repo.record_failure(_RECORD)

    assert stats.failures == 1


def test_reset_updates_zeroes_counter(db_session):
    """reset_updates must set the updates counter back to zero."""
    repo = StatsRepository(db_session)
    repo.record_update(_RECORD)
    repo.record_update(_RECORD)
    assert repo.get_by_name(_RECORD).updates == 2

    stats = repo.reset_updates(_RECORD)
    assert stats.updates == 0


def test_delete_by_name_removes_row(db_session):
    """delete_by_name must remove the row and return True."""
    repo = StatsRepository(db_session)
    repo.get_or_create(_RECORD)
    deleted = repo.delete_by_name(_RECORD)

    assert deleted is True
    assert repo.get_by_name(_RECORD) is None


def test_delete_by_name_returns_false_when_absent(db_session):
    """delete_by_name must return False when no row exists."""
    repo = StatsRepository(db_session)
    assert repo.delete_by_name("nonexistent.example.com") is False


def test_get_all_returns_all_rows(db_session):
    """get_all must return all rows ordered by record_name."""
    repo = StatsRepository(db_session)
    repo.get_or_create("b.example.com")
    repo.get_or_create("a.example.com")

    all_stats = repo.get_all()
    assert len(all_stats) == 2
    assert all_stats[0].record_name == "a.example.com"
