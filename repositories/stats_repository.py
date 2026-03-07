"""
repositories/stats_repository.py

Responsibility: Provides low-level read/write access to the RecordStats table
in SQLite via SQLModel.
Does NOT: contain business logic, IP fetching, or log parsing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from db.models import RecordStats

logger = logging.getLogger(__name__)


class StatsRepository:
    """
    Manages persistence of per-record DNS update statistics.

    One RecordStats row per tracked FQDN. Rows are created on first access
    and updated after every check cycle by StatsService.

    Collaborators:
        - Session: SQLModel DB session injected at construction time
    """

    def __init__(self, session: Session) -> None:
        """
        Initialises the repository with an active DB session.

        Args:
            session: An open SQLModel Session for the current request.
        """
        self._session = session

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def get_or_create(self, record_name: str) -> RecordStats:
        """
        Returns the RecordStats row for the given FQDN, creating it if absent.

        Args:
            record_name: The fully-qualified DNS name, e.g. "home.example.com".

        Returns:
            The RecordStats ORM instance for the given record name.
        """
        statement = select(RecordStats).where(RecordStats.record_name == record_name)
        stats = self._session.exec(statement).first()

        if stats is None:
            logger.debug("Creating RecordStats row for %s.", record_name)
            stats = RecordStats(record_name=record_name)
            self._session.add(stats)
            self._session.commit()
            self._session.refresh(stats)

        return stats

    def get_all(self) -> list[RecordStats]:
        """
        Returns all RecordStats rows ordered by record name.

        Returns:
            A list of RecordStats instances, possibly empty.
        """
        statement = select(RecordStats).order_by(RecordStats.record_name)
        return list(self._session.exec(statement).all())

    def get_bulk(self, names: list[str]) -> dict[str, RecordStats]:
        """
        Returns a dict of RecordStats keyed by FQDN for all listed names.

        Issues a single SELECT … WHERE record_name IN (…) query instead of
        N individual lookups, reducing DB round-trips for dashboard renders.
        Names that have no row in the DB are absent from the returned dict
        (not a KeyError — callers should use .get()).

        Args:
            names: List of FQDNs to look up.

        Returns:
            dict[str, RecordStats] keyed by record_name.  Only names that
            have an existing row appear as keys.
        """
        if not names:
            return {}
        statement = select(RecordStats).where(RecordStats.record_name.in_(names))
        rows = self._session.exec(statement).all()
        return {row.record_name: row for row in rows}

    def get_by_name(self, record_name: str) -> RecordStats | None:
        """
        Returns the RecordStats row for the given FQDN, or None if absent.

        Args:
            record_name: The fully-qualified DNS name to look up.

        Returns:
            The RecordStats instance, or None if the record is not tracked.
        """
        statement = select(RecordStats).where(RecordStats.record_name == record_name)
        return self._session.exec(statement).first()

    def save(self, stats: RecordStats) -> RecordStats:
        """
        Persists a RecordStats instance to the database.

        Args:
            stats: The RecordStats instance to save.

        Returns:
            The refreshed RecordStats instance after commit.
        """
        self._session.add(stats)
        self._session.commit()
        self._session.refresh(stats)
        return stats

    def record_check(self, record_name: str) -> RecordStats:
        """
        Updates the last_checked timestamp for the given record.

        Args:
            record_name: The fully-qualified DNS name that was just checked.

        Returns:
            The updated RecordStats instance.
        """
        stats = self.get_or_create(record_name)
        stats.last_checked = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.save(stats)

    def record_update(self, record_name: str) -> RecordStats:
        """
        Increments the update counter and sets last_updated for the given record.

        Args:
            record_name: The fully-qualified DNS name that was just updated.

        Returns:
            The updated RecordStats instance.
        """
        stats = self.get_or_create(record_name)
        stats.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
        stats.updates += 1
        return self.save(stats)

    def record_failure(self, record_name: str) -> RecordStats:
        """
        Increments the failure counter for the given record.

        Args:
            record_name: The fully-qualified DNS name whose update failed.

        Returns:
            The updated RecordStats instance.
        """
        stats = self.get_or_create(record_name)
        stats.failures += 1
        return self.save(stats)

    def reset_failures(self, record_name: str) -> RecordStats:
        """
        Resets the failure counter to zero for the given record.

        Args:
            record_name: The fully-qualified DNS name whose failures to clear.

        Returns:
            The updated RecordStats instance.
        """
        stats = self.get_or_create(record_name)
        stats.failures = 0
        return self.save(stats)

    def reset_updates(self, record_name: str) -> RecordStats:
        """
        Resets the updates counter to zero for the given record.

        Args:
            record_name: The fully-qualified DNS name whose updates counter to clear.

        Returns:
            The updated RecordStats instance.
        """
        stats = self.get_or_create(record_name)
        stats.updates = 0
        return self.save(stats)

    def delete_by_name(self, record_name: str) -> bool:
        """
        Deletes the RecordStats row for the given FQDN if it exists.

        Args:
            record_name: The fully-qualified DNS name to remove.

        Returns:
            True if a row was deleted, False if no row existed.
        """
        stats = self.get_by_name(record_name)
        if stats is None:
            return False
        self._session.delete(stats)
        self._session.commit()
        logger.debug("Deleted RecordStats for %s.", record_name)
        return True
