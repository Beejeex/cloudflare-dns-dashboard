"""
tests/unit/test_ip_service.py

Unit tests for services/ip_service.py.
Verifies happy-path IP fetching and typed error raising on failure.
"""

from __future__ import annotations

import pytest
import httpx
import respx
from unittest.mock import patch

from services.ip_service import IpService
from exceptions import IpFetchError


@pytest.mark.asyncio
async def test_get_public_ip_returns_ip(mock_http):
    """IpService must return the plain-text IP from the upstream provider."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="1.2.3.4")
    )
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        ip = await service.get_public_ip()
    assert ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_public_ip_strips_whitespace(mock_http):
    """IpService must strip leading/trailing whitespace from the response."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="  1.2.3.4\n")
    )
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        ip = await service.get_public_ip()
    assert ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_public_ip_raises_on_network_error(mock_http):
    """IpService must raise IpFetchError when the upstream is unreachable."""
    mock_http.get("https://api.ipify.org").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        with pytest.raises(IpFetchError):
            await service.get_public_ip()


@pytest.mark.asyncio
async def test_get_public_ip_raises_on_http_error(mock_http):
    """IpService must raise IpFetchError when the upstream returns a non-200 status."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        with pytest.raises(IpFetchError):
            await service.get_public_ip()


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class _FakeState:
    """Minimal stand-in for FastAPI app.state that holds ip_cache."""
    def __init__(self):
        self.ip_cache: dict = {}


@pytest.mark.asyncio
async def test_cache_hit_skips_network_call(mock_http):
    """When the cached value is still within TTL, ipify must not be called."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="9.9.9.9")
    )
    state = _FakeState()
    # Pre-populate cache with a fresh entry
    state.ip_cache = {"ip": "1.2.3.4", "fetched_at": 0.0}

    # Patch monotonic so the cache appears fresh (fetched_at=0 + now=5 < TTL=30)
    with patch("services.ip_service.time.monotonic", return_value=5.0):
        async with httpx.AsyncClient() as client:
            service = IpService(http_client=client, app_state=state)
            ip = await service.get_public_ip()

    assert ip == "1.2.3.4"
    # Network mock should not have been hit
    assert not mock_http.calls


@pytest.mark.asyncio
async def test_expired_cache_triggers_fresh_fetch(mock_http):
    """When the cached entry is older than TTL, the service must fetch upstream."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="5.5.5.5")
    )
    state = _FakeState()
    # Cache entry exists but is 60 seconds old — beyond 30 s TTL
    state.ip_cache = {"ip": "1.2.3.4", "fetched_at": 0.0}

    # Patch monotonic so now=60 → age=60 > TTL=30 → cache miss
    with patch("services.ip_service.time.monotonic", return_value=60.0):
        async with httpx.AsyncClient() as client:
            service = IpService(http_client=client, app_state=state)
            ip = await service.get_public_ip()

    assert ip == "5.5.5.5"
    # Cache should now hold the freshly fetched IP
    assert state.ip_cache["ip"] == "5.5.5.5"


@pytest.mark.asyncio
async def test_no_app_state_disables_cache(mock_http):
    """When app_state=None the service must fetch fresh on every call."""
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="2.2.2.2")
    )
    async with httpx.AsyncClient() as client:
        # No app_state → caching disabled
        service = IpService(http_client=client, app_state=None)
        ip = await service.get_public_ip()

    assert ip == "2.2.2.2"
    assert len(mock_http.calls) == 1
