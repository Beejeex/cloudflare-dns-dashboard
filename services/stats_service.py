"""
services/stats_service.py

Responsibility: Provides a business-level API for recording and retrieving
per-DNS-record update and failure statistics. Delegates all persistence
to StatsRepository.
Does NOT: make HTTP calls, read configuration, or manage log entries.
"""

from __future__ import annotations

import logging

from db.models import RecordStats
from repositories.stats_repository import StatsRepository

logger = logging.getLogger(__name__)


class StatsService:
    """
    Records and retrieves per-DNS-record DDNS update statistics.

    Wraps StatsRepository with intent-named methods called by DnsService
    after each check/update/failure event.

    Collaborators:
        - StatsRepository: handles all database access for RecordStats rows
    """

    def __init__(self, stats_repo: StatsRepository) -> None:
        """
        Initialises the service with a stats repository.

        Args:
            stats_repo: An initialised StatsRepository for the current session.
        """
        self._repo = stats_repo

    async def record_checked(self, record_name: str) -> RecordStats:
        """
        Records that a DNS record was checked (updates last_checked timestamp).

        Args:
            record_name: The fully-qualified DNS name that was checked.

        Returns:
            The updated RecordStats instance.
        """
        return self._repo.record_check(record_name)

    async def record_updated(self, record_name: str) -> RecordStats:
        """
        Records a successful IP update for the given DNS record.

        Increments the updates counter and sets last_updated.

        Args:
            record_name: The fully-qualified DNS name that was updated.

        Returns:
            The updated RecordStats instance.
        """
        logger.info("Stats: update recorded for %s.", record_name)
        return self._repo.record_update(record_name)

    async def record_failed(self, record_name: str) -> RecordStats:
        """
        Records a failed update attempt for the given DNS record.

        Increments the failures counter.

        Args:
            record_name: The fully-qualified DNS name whose update failed.

        Returns:
            The updated RecordStats instance.
        """
        logger.warning("Stats: failure recorded for %s.", record_name)
        return self._repo.record_failure(record_name)

    async def get_all(self) -> list[RecordStats]:
        """
        Returns all RecordStats rows ordered by record name.

        Returns:
            A list of RecordStats instances.
        """
        return self._repo.get_all()

    async def get_for_record(self, record_name: str) -> RecordStats | None:
        """
        Returns stats for a specific DNS record, or None if not tracked.

        Args:
            record_name: The fully-qualified DNS name to look up.

        Returns:
            The RecordStats instance, or None.
        """
        return self._repo.get_by_name(record_name)

    async def reset_failures(self, record_name: str) -> RecordStats:
        """
        Resets the failure counter to zero for the given DNS record.

        Args:
            record_name: The fully-qualified DNS name whose failures to clear.

        Returns:
            The updated RecordStats instance.
        """
        logger.info("Stats: failures reset for %s.", record_name)
        return self._repo.reset_failures(record_name)

    async def reset_updates(self, record_name: str) -> RecordStats:
        """
        Resets the updates counter to zero for the given DNS record.

        Args:
            record_name: The fully-qualified DNS name whose updates counter to clear.

        Returns:
            The updated RecordStats instance.
        """
        logger.info("Stats: updates reset for %s.", record_name)
        return self._repo.reset_updates(record_name)

    async def delete_for_record(self, record_name: str) -> bool:
        """
        Deletes the stats row for the given DNS record.

        Called when a record is removed from the managed list so stale
        stats are not shown in the UI.

        Args:
            record_name: The fully-qualified DNS name to remove stats for.

        Returns:
            True if a row was deleted, False if none existed.
        """
        return self._repo.delete_by_name(record_name)
