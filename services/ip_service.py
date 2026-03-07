"""
services/ip_service.py

Responsibility: Fetches the current public IP address of the host machine,
with a short-lived in-memory cache to avoid redundant upstream calls.
Does NOT: parse DNS records, interact with Cloudflare, or read config files.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from exceptions import IpFetchError

logger = logging.getLogger(__name__)

# NOTE: api.ipify.org returns the caller's public IPv4 as plain text.
_IP_PROVIDER_URL = "https://api.ipify.org"

# Cache TTL in seconds.  A 30-second window collapses concurrent timer polls
# (scheduler + SSE on-connect) into a single upstream call per interval.
_CACHE_TTL = 30.0


class IpService:
    """
    Fetches the host machine's current public IPv4 address.

    Results are cached on app.state.ip_cache for _CACHE_TTL seconds so that
    multiple concurrent callers (scheduler, SSE on-connect, API endpoint) share
    a single upstream call per interval instead of each issuing their own.

    Uses an injected httpx.AsyncClient so the service is fully testable
    without real network calls (use respx.mock in tests).

    Collaborators:
        - httpx.AsyncClient: injected; must be kept alive externally
        - app_state: Starlette/FastAPI app.state object; holds ip_cache dict
    """

    def __init__(self, http_client: httpx.AsyncClient, app_state: Any = None) -> None:
        """
        Initialises the service with a shared HTTP client and optional app state.

        Args:
            http_client: A long-lived httpx.AsyncClient instance created
                         during application startup.
            app_state: The FastAPI app.state object used to store the shared
                       IP cache.  When None (e.g. in unit tests) caching is
                       disabled and every call fetches fresh.
        """
        self._client = http_client
        self._app_state = app_state

    async def get_public_ip(self) -> str:
        """
        Returns the current public IPv4 address of the host machine.

        Returns a cached result when app_state.ip_cache is set and the
        cached value is still within _CACHE_TTL seconds.  On a cache miss
        (or when app_state is unavailable) fetches fresh from the upstream
        provider and updates the cache.

        Returns:
            The public IP address as a plain string, e.g. "1.2.3.4".

        Raises:
            IpFetchError: If the upstream provider is unreachable or returns
                          a non-200 response.
        """
        # ---------------------------------------------------------------------------
        # Cache read — skip network call if the cached value is still fresh
        # ---------------------------------------------------------------------------
        if self._app_state is not None:
            cache = getattr(self._app_state, "ip_cache", None)
            if cache is not None:
                cached_ip: str | None = cache.get("ip")
                fetched_at: float = cache.get("fetched_at", 0.0)
                if cached_ip and (time.monotonic() - fetched_at) < _CACHE_TTL:
                    logger.debug("Public IP served from cache: %s", cached_ip)
                    return cached_ip

        # ---------------------------------------------------------------------------
        # Cache miss — fetch from upstream and populate cache
        # ---------------------------------------------------------------------------
        try:
            response = await self._client.get(_IP_PROVIDER_URL)
            response.raise_for_status()
            ip = response.text.strip()
            logger.debug("Current public IP fetched from upstream: %s", ip)
        except httpx.HTTPStatusError as exc:
            raise IpFetchError(
                f"IP provider returned status {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise IpFetchError(
                f"Could not reach IP provider ({_IP_PROVIDER_URL}): {exc}"
            ) from exc

        # Write through to cache
        if self._app_state is not None and hasattr(self._app_state, "ip_cache"):
            self._app_state.ip_cache["ip"] = ip
            self._app_state.ip_cache["fetched_at"] = time.monotonic()

        return ip
