"""
tests/unit/test_broadcast_service.py

Unit tests for services/broadcast_service.py.
Verifies subscribe/publish/unsubscribe lifecycle and fan-out correctness.
"""

from __future__ import annotations

import asyncio

import pytest

from services.broadcast_service import BroadcastService


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


def test_subscribe_returns_queue():
    """subscribe() must return a new asyncio.Queue."""
    svc = BroadcastService()
    q = svc.subscribe()
    assert isinstance(q, asyncio.Queue)


def test_subscribe_registers_queue_in_internal_set():
    """subscribe() must add the returned queue to _queues."""
    svc = BroadcastService()
    q = svc.subscribe()
    assert q in svc._queues


def test_subscribe_increments_queue_count():
    """Each subscribe() call adds exactly one queue."""
    svc = BroadcastService()
    svc.subscribe()
    svc.subscribe()
    assert len(svc._queues) == 2


# ---------------------------------------------------------------------------
# unsubscribe
# ---------------------------------------------------------------------------


def test_unsubscribe_removes_queue():
    """unsubscribe() must remove the queue from _queues."""
    svc = BroadcastService()
    q = svc.subscribe()
    svc.unsubscribe(q)
    assert q not in svc._queues


def test_unsubscribe_unknown_queue_is_no_op():
    """unsubscribe() with an unregistered queue must not raise."""
    svc = BroadcastService()
    q: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    # Should not raise even though q was never subscribed
    svc.unsubscribe(q)


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_puts_event_on_subscriber_queue():
    """publish() must place the event dict on every registered queue."""
    svc = BroadcastService()
    q = svc.subscribe()
    svc.publish("records_updated", "<div>payload</div>")
    msg = q.get_nowait()
    assert msg == {"event": "records_updated", "data": "<div>payload</div>"}


@pytest.mark.asyncio
async def test_publish_reaches_all_subscribers():
    """publish() must fan-out to every open subscriber queue."""
    svc = BroadcastService()
    q1 = svc.subscribe()
    q2 = svc.subscribe()
    q3 = svc.subscribe()
    svc.publish("ip_updated", "5.6.7.8")
    assert q1.get_nowait()["event"] == "ip_updated"
    assert q2.get_nowait()["event"] == "ip_updated"
    assert q3.get_nowait()["event"] == "ip_updated"


@pytest.mark.asyncio
async def test_publish_does_not_reach_unsubscribed_queue():
    """Events published after unsubscribe() must not reach the removed queue."""
    svc = BroadcastService()
    q = svc.subscribe()
    svc.unsubscribe(q)
    svc.publish("records_updated", "")
    # Queue must still be empty because it was removed before publish
    assert q.empty()


def test_publish_with_no_subscribers_is_no_op():
    """publish() with no subscribers must not raise."""
    svc = BroadcastService()
    # Should not raise
    svc.publish("ping", "")


@pytest.mark.asyncio
async def test_publish_multiple_events_arrive_in_order():
    """Events must be consumable in the order they were published."""
    svc = BroadcastService()
    q = svc.subscribe()
    svc.publish("ev1", "a")
    svc.publish("ev2", "b")
    svc.publish("ev3", "c")
    events = [q.get_nowait()["event"] for _ in range(3)]
    assert events == ["ev1", "ev2", "ev3"]
