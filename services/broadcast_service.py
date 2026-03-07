"""
services/broadcast_service.py

Responsibility: Manages SSE subscriber queues and dispatches named events to
all currently connected SSE clients.
Does NOT: fetch IP addresses, render HTML, interact with the DB, or know about
DNS records or configuration.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class BroadcastService:
    """
    Fan-out SSE event broadcaster.

    Each connected SSE client registers a private asyncio.Queue via
    subscribe().  When publish() is called every open queue receives a copy
    of the event so all clients update simultaneously.  Queues are removed
    on disconnect via unsubscribe() so no leak occurs over time.

    Collaborators:
        None — pure in-process fan-out; no external I/O.
    """

    def __init__(self) -> None:
        # NOTE: Using a plain set — subscribers are added/removed on the same
        # event loop so no locking is needed in a single-process async app.
        self._queues: set[asyncio.Queue[dict[str, str]]] = set()

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, str]]:
        """
        Creates a new subscriber queue and registers it for broadcasts.

        Returns:
            A new asyncio.Queue that receives all subsequent published events
            as dicts with ``event`` and ``data`` keys.
        """
        q: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self._queues.add(q)
        logger.debug("SSE subscriber added. Active: %d", len(self._queues))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, str]]) -> None:
        """
        Removes a subscriber queue.  No-op if the queue is not registered.

        Args:
            q: The queue returned by a previous subscribe() call.
        """
        self._queues.discard(q)
        logger.debug("SSE subscriber removed. Active: %d", len(self._queues))

    def publish(self, event_type: str, data: str) -> None:
        """
        Puts an event onto every registered subscriber queue.

        Uses put_nowait() so this method is safe to call from both sync and
        async contexts without suspending the caller.  Queues are unbounded
        so put_nowait() never raises QueueFull under normal operation.

        Args:
            event_type: The SSE event name, e.g. ``"records_updated"``.
            data: The payload string.  For HTML fragment events this is raw
                  HTML; for signal-only events (log_appended, ping) this may
                  be an empty string or JSON.
        """
        if not self._queues:
            return
        msg: dict[str, str] = {"event": event_type, "data": data}
        for q in list(self._queues):
            q.put_nowait(msg)
        logger.debug(
            "Published SSE event '%s' to %d subscriber(s).", event_type, len(self._queues)
        )
