"""
dependencies.py

Responsibility: Declares all FastAPI Depends() provider functions for
services and repositories used throughout the application.
Does NOT: contain business logic, HTTP handlers, or DB schema definitions.
"""

from __future__ import annotations

import httpx
from fastapi import Depends, Request
from sqlmodel import Session

from cloudflare.cloudflare_client import CloudflareClient
from cloudflare.dns_provider import DNSProvider
from cloudflare.unifi_client import UnifiClient
from db.database import get_session
from repositories.config_repository import ConfigRepository
from repositories.record_config_repository import RecordConfigRepository
from repositories.stats_repository import StatsRepository
from services.broadcast_service import BroadcastService
from services.config_service import ConfigService
from services.dns_service import DnsService
from services.ip_service import IpService
from services.kubernetes_service import KubernetesService
from services.log_service import LogService
from services.stats_service import StatsService

# ---------------------------------------------------------------------------
# Infrastructure — shared app-level resources
# ---------------------------------------------------------------------------


def get_http_client(request: Request) -> httpx.AsyncClient:
    """
    Returns the shared httpx.AsyncClient stored on app.state.

    The client is created once during the FastAPI lifespan and reused for
    all requests to avoid connection-pool overhead.

    Args:
        request: The current FastAPI Request (injected automatically).

    Returns:
        The application-level httpx.AsyncClient.
    """
    return request.app.state.http_client


def get_unifi_http_client(request: Request) -> httpx.AsyncClient:
    """
    Returns the dedicated UniFi httpx.AsyncClient (verify=False) from app.state.

    A separate client is used for UniFi because controllers use self-signed
    certificates. Keeping it isolated avoids disabling SSL verification globally.

    Args:
        request: The current FastAPI Request (injected automatically).

    Returns:
        The UniFi-specific httpx.AsyncClient.
    """
    return request.app.state.unifi_http_client


def get_broadcaster(request: Request) -> BroadcastService:
    """
    Returns the shared BroadcastService stored on app.state.

    The broadcaster is created once during the FastAPI lifespan and reused
    for all requests so all SSE subscribers share the same fan-out bus.

    Args:
        request: The current FastAPI Request (injected automatically).

    Returns:
        The application-level BroadcastService instance.
    """
    return request.app.state.broadcaster


# ---------------------------------------------------------------------------
# Repository providers
# ---------------------------------------------------------------------------


def get_config_repo(session: Session = Depends(get_session)) -> ConfigRepository:
    """
    Provides a ConfigRepository for the current request's DB session.

    Args:
        session: The DB session injected by get_session.

    Returns:
        A ConfigRepository instance.
    """
    return ConfigRepository(session)


def get_stats_repo(session: Session = Depends(get_session)) -> StatsRepository:
    """
    Provides a StatsRepository for the current request's DB session.

    Args:
        session: The DB session injected by get_session.

    Returns:
        A StatsRepository instance.
    """
    return StatsRepository(session)


def get_record_config_repo(
    session: Session = Depends(get_session),
) -> RecordConfigRepository:
    """
    Provides a RecordConfigRepository for the current request's DB session.

    Args:
        session: The DB session injected by get_session.

    Returns:
        A RecordConfigRepository instance.
    """
    return RecordConfigRepository(session)


# ---------------------------------------------------------------------------
# Service providers
# ---------------------------------------------------------------------------


def get_config_service(
    config_repo: ConfigRepository = Depends(get_config_repo),
) -> ConfigService:
    """
    Provides a ConfigService backed by the current request's DB session.

    Args:
        config_repo: The repository injected by get_config_repo.

    Returns:
        A ConfigService instance.
    """
    return ConfigService(config_repo)


def get_stats_service(
    stats_repo: StatsRepository = Depends(get_stats_repo),
) -> StatsService:
    """
    Provides a StatsService backed by the current request's DB session.

    Args:
        stats_repo: The repository injected by get_stats_repo.

    Returns:
        A StatsService instance.
    """
    return StatsService(stats_repo)


def get_log_service(session: Session = Depends(get_session)) -> LogService:
    """
    Provides a LogService backed by the current request's DB session.

    Args:
        session: The DB session injected by get_session.

    Returns:
        A LogService instance.
    """
    return LogService(session)


def get_ip_service(
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> IpService:
    """
    Provides an IpService using the shared HTTP client and app.state for caching.

    The app.state reference allows IpService to read and write the shared
    ip_cache dict, so concurrent callers within the same interval share a
    single upstream call rather than each issuing their own.

    Args:
        request: The current FastAPI Request (injected automatically).
        http_client: The application-level httpx.AsyncClient.

    Returns:
        An IpService instance with cache access.
    """
    return IpService(http_client, app_state=request.app.state)


async def get_dns_provider(
    config_service: ConfigService = Depends(get_config_service),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> DNSProvider:
    """
    Provides a CloudflareClient initialised with the current API token.

    Loads the API token from config on every request so any token change
    takes effect without a restart.

    Args:
        config_service: Provides the current API token from the DB.
        http_client: The application-level httpx.AsyncClient.

    Returns:
        A CloudflareClient instance satisfying the DNSProvider protocol.
    """
    api_token = await config_service.get_api_token()
    return CloudflareClient(http_client=http_client, api_token=api_token)


def get_dns_service(
    dns_provider: DNSProvider = Depends(get_dns_provider),
    ip_service: IpService = Depends(get_ip_service),
    stats_service: StatsService = Depends(get_stats_service),
    log_service: LogService = Depends(get_log_service),
) -> DnsService:
    """
    Provides a fully wired DnsService for the current request.

    All collaborators are injected via Depends() so the DnsService itself
    depends only on abstractions.

    Args:
        dns_provider: The active DNSProvider implementation.
        ip_service: Provides the current public IP.
        stats_service: Records update/failure stats.
        log_service: Writes UI-visible log entries.

    Returns:
        A DnsService instance ready to use.
    """
    return DnsService(dns_provider, ip_service, stats_service, log_service)


async def get_kubernetes_service(
    config_service: ConfigService = Depends(get_config_service),
) -> KubernetesService:
    """
    Provides a KubernetesService reflecting the current enable/disable toggle.

    The service skips discovery and returns an empty list when k8s_enabled is
    False. Connection is auto-detected: in-cluster SA first, then
    /config/kubeconfig as a fallback.

    Args:
        config_service: Provides the k8s_enabled flag from the DB.

    Returns:
        A KubernetesService instance.
    """
    k8s_enabled = await config_service.get_k8s_enabled()
    return KubernetesService(enabled=k8s_enabled)


async def get_unifi_client(
    config_service: ConfigService = Depends(get_config_service),
    http_client: httpx.AsyncClient = Depends(get_unifi_http_client),
) -> UnifiClient:
    """
    Provides a UnifiClient initialised with the current host, API key, and site.

    The client's is_configured() returns False when no key is set,
    allowing callers to skip UniFi calls gracefully.

    Args:
        config_service: Provides the UniFi config from the DB.
        http_client: The UniFi-specific httpx.AsyncClient (verify=False).

    Returns:
        A UnifiClient instance.
    """
    host, api_key, _, _, _ = await config_service.get_unifi_config()
    return UnifiClient(http_client=http_client, api_key=api_key, host=host or "localhost")
