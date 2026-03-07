"""
routes/api_routes.py

Responsibility: JSON and HTMX partial API endpoints consumed by the frontend
for live data (log tail, IP status, records refresh, SSE event stream) and
manual actions such as triggering an immediate sync cycle.
Does NOT: render full pages or manage DB sessions directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from cloudflare.unifi_client import UnifiClient
from dependencies import (
    get_broadcaster,
    get_config_service,
    get_dns_service,
    get_ip_service,
    get_log_service,
    get_record_config_repo,
    get_stats_repo,
    get_unifi_client,
    get_unifi_http_client,
)
from exceptions import DnsProviderError, IpFetchError, UnifiProviderError
from repositories.record_config_repository import RecordConfigRepository
from repositories.stats_repository import StatsRepository
from scheduler import run_ddns_check_now
from services.broadcast_service import BroadcastService
from services.config_service import ConfigService
from services.dns_service import DnsService
from services.ip_service import IpService
from services.log_service import LogService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
from shared_templates import templates  # noqa: E402

# NOTE: Configurable via SSE_PING_INTERVAL env var so integration tests can
# set it to a short value (e.g. 0.1) and avoid a 25-second hang on teardown.
_SSE_PING_INTERVAL: float = float(os.getenv("SSE_PING_INTERVAL", "25.0"))


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------


@router.get("/events")
async def sse_events(
    request: Request,
    broadcaster: BroadcastService = Depends(get_broadcaster),
    config_service: ConfigService = Depends(get_config_service),
    dns_service: DnsService = Depends(get_dns_service),
    ip_service: IpService = Depends(get_ip_service),
    stats_repo: StatsRepository = Depends(get_stats_repo),
    record_config_repo: RecordConfigRepository = Depends(get_record_config_repo),
    unifi_client: UnifiClient = Depends(get_unifi_client),
) -> EventSourceResponse:
    """
    Server-Sent Events stream that pushes live IP and records updates to clients.

    On connect the client immediately receives the current public IP
    (``ip_updated``) and a rendered records-table fragment (``records_updated``)
    so there is no blank display period even after an SSE reconnect.

    Subsequent events are forwarded from the BroadcastService queue as they
    arrive.  A ``ping`` event is sent every 25 seconds to prevent proxy
    connection timeouts.

    Args:
        request: The incoming FastAPI request.
        broadcaster: Fan-out bus — provides the subscriber queue.
        config_service: Provides managed records and zone config.
        dns_service: Fetches live DNS state via fetch_zone_record_map().
        ip_service: Provides the current public IP (cache-aware).
        stats_repo: Bulk stats lookup for the initial render.
        record_config_repo: Per-record settings for the initial render.
        unifi_client: Provides UniFi DNS policy state.

    Returns:
        An EventSourceResponse that streams SSE events to the client.
    """
    async def _generator():
        q = broadcaster.subscribe()
        try:
            # --- On-connect: push current IP immediately ---
            current_ip = "Unavailable"
            try:
                current_ip = await ip_service.get_public_ip()
            except IpFetchError as exc:
                logger.warning("SSE on-connect: could not fetch public IP: %s", exc)
            # NOTE: Plain text so HTMX sse-swap can set it directly as innerHTML
            yield {"event": "ip_updated", "data": current_ip}

            # NOTE: records_updated is NOT sent on-connect because the dashboard
            # page is already rendered fresh by the template.  Sending it here
            # would immediately trigger a location.reload() loop in the unified
            # grid view.  The scheduler pushes records_updated after each sync cycle.

            # --- Stream: forward queue events until disconnect ---
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=_SSE_PING_INTERVAL)
                    yield msg
                except asyncio.TimeoutError:
                    # NOTE: Keep-alive ping prevents proxy connection timeouts.
                    yield {"event": "ping", "data": ""}
        finally:
            broadcaster.unsubscribe(q)

    return EventSourceResponse(_generator())


async def _render_records_for_sse(
    *,
    request: Request,
    config_service: ConfigService,
    dns_service: DnsService,
    stats_repo: StatsRepository,
    record_config_repo: RecordConfigRepository,
    unifi_client: UnifiClient,
    current_ip: str,
) -> str:
    """
    Renders the records-table template as an HTML string for SSE delivery.

    Uses fetch_zone_record_map() to batch Cloudflare lookups (one call per
    zone) and get_bulk() for stats — both added in Phase 2.  Called only
    on SSE connect so the cost is paid once per new client connection.

    Args:
        request: The FastAPI request (forwarded to the template context).
        config_service: Provides config, zones, and managed records.
        dns_service: Fetches the DNS record map in bulk.
        stats_repo: Provides per-record stats via a single bulk query.
        record_config_repo: Provides per-record settings.
        unifi_client: Fetches UniFi DNS policies.
        current_ip: The host's current public IP (already fetched by caller).

    Returns:
        Rendered HTML string from partials/records_table.html.
    """
    config = await config_service.get_config()
    zones = await config_service.get_zones()
    managed_records = await config_service.get_managed_records()
    record_configs = record_config_repo.get_all(managed_records)
    stats_by_name = stats_repo.get_bulk(managed_records)

    _, _, unifi_site_id, unifi_default_ip, unifi_enabled = await config_service.get_unifi_config()

    # Batch Cloudflare lookup — one API call per zone
    zone_record_map: dict[str, object] = {}
    if config.api_token and zones:
        try:
            zone_record_map = await dns_service.fetch_zone_record_map(managed_records, zones)
        except Exception as exc:
            logger.warning("SSE records render: CF zone fetch failed: %s", exc)

    # Batch UniFi policy lookup
    unifi_policy_map: dict[str, object] = {}
    if unifi_enabled and unifi_client.is_configured() and unifi_site_id:
        try:
            policies = await unifi_client.list_records(unifi_site_id)
            unifi_policy_map = {p.name: p for p in policies}
        except UnifiProviderError as exc:
            logger.warning("SSE records render: UniFi policy fetch failed: %s", exc)

    record_data = []
    for record_name in managed_records:
        dns_record = zone_record_map.get(record_name)
        stats = stats_by_name.get(record_name)
        rc = record_configs.get(record_name)
        dns_ip = dns_record.content if dns_record else "Not Found"
        cf_enabled = rc.cf_enabled if rc else True
        is_up_to_date = (
            None if not cf_enabled
            else (dns_record is not None and dns_ip == current_ip)
        )
        unifi_policy = unifi_policy_map.get(record_name)
        local_name = _to_local_policy_name(record_name)
        unifi_local_policy = unifi_policy_map.get(local_name) if local_name != record_name else None

        record_data.append({
            "name": record_name,
            "cf_record_id": dns_record.id if dns_record else None,
            "dns_ip": dns_ip,
            "is_up_to_date": is_up_to_date,
            "updates": stats.updates if stats else 0,
            "failures": stats.failures if stats else 0,
            "last_checked": stats.last_checked.isoformat() if stats and stats.last_checked else None,
            "last_updated": stats.last_updated.isoformat() if stats and stats.last_updated else None,
            "unifi_ip": unifi_policy.content if unifi_policy else None,
            "unifi_local_ip": unifi_local_policy.content if unifi_local_policy else None,
            "unifi_record_id": unifi_policy.id if unifi_policy else None,
            "cfg_cf_enabled": rc.cf_enabled if rc else True,
            "cfg_ip_mode": rc.ip_mode if rc else "dynamic",
            "cfg_static_ip": rc.static_ip if rc else "",
            "cfg_unifi_enabled": rc.unifi_enabled if rc else False,
            "cfg_unifi_static_ip": rc.unifi_static_ip if rc else "",
            "cfg_unifi_local_enabled": rc.unifi_local_enabled if rc else False,
            "cfg_unifi_local_static_ip": rc.unifi_local_static_ip if rc else "",
        })

    return templates.get_template("partials/records_table.html").render(
        {
            "request": request,
            "records": record_data,
            "unifi_enabled": unifi_enabled,
            "unifi_default_ip": unifi_default_ip,
        }
    )


# ---------------------------------------------------------------------------
# Manual sync trigger
# ---------------------------------------------------------------------------


@router.post("/trigger-sync", response_class=HTMLResponse)
async def trigger_sync(request: Request) -> HTMLResponse:
    """
    Runs one full DDNS + UniFi sync cycle immediately on demand.

    Pulls the shared HTTP clients from app.state so the job uses the same
    connections as the scheduler. Returns an HTMX-friendly indicator that
    is swapped into the button area and triggers a page reload on completion.

    Args:
        request: The incoming FastAPI request (used to access app.state).

    Returns:
        An HTMLResponse confirming the sync was triggered.
    """
    await run_ddns_check_now(
        http_client=request.app.state.http_client,
        unifi_http_client=request.app.state.unifi_http_client,
        broadcaster=getattr(request.app.state, "broadcaster", None),
    )
    # Empty body — the HTMX after-request handler triggers location.reload()
    return HTMLResponse(content="", status_code=200)


@router.get("/logs/recent", response_class=HTMLResponse)
async def get_recent_logs(
    request: Request,
    log_service: LogService = Depends(get_log_service),
) -> HTMLResponse:
    """
    Returns the recent log entries as an HTML fragment for HTMX polling.

    The dashboard polls this endpoint every N seconds (configured via the
    hx-trigger attribute on the log panel) and swaps in the result.

    Args:
        request: The incoming FastAPI request.
        log_service: Provides recent log entries from the DB.

    Returns:
        An HTMLResponse containing the log-panel partial fragment.
    """
    recent_logs = log_service.get_recent(limit=100)
    return templates.TemplateResponse(
        request,
        "partials/log_panel.html",
        {"logs": recent_logs},
    )


@router.get("/current-ip", response_class=PlainTextResponse)
async def current_ip(request: Request) -> str:
    """
    Returns the host's current public IP as plain text for the navbar HTMX poll.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The public IP address string, or "Unavailable" on failure.
    """
    try:
        from services.ip_service import IpService
        ip_service = IpService(request.app.state.http_client, app_state=request.app.state)
        return await ip_service.get_public_ip()
    except IpFetchError as exc:
        logger.warning("Could not fetch public IP for navbar: %s", exc)
        return "Unavailable"


@router.get("/unifi/sites", response_class=HTMLResponse)
async def get_unifi_sites(
    request: Request,
    unifi_host: str = Query(default="", alias="unifi_host"),
    unifi_api_key: str = Query(default="", alias="unifi_api_key"),
    http_client: httpx.AsyncClient = Depends(get_unifi_http_client),
) -> HTMLResponse:
    """
    Queries the UniFi controller for all available sites and returns an HTML
    partial so the settings page can auto-fill or show a picker for the Site ID.

    Accepts the host and api_key as query parameters so the user does not need
    to save settings first.

    Args:
        unifi_host: UniFi controller host (IP or hostname).
        unifi_api_key: UniFi API key.
        http_client: Shared async client with verify=False.

    Returns:
        HTML partial rendered from partials/unifi_sites.html.
    """
    context: dict = {"request": request, "sites": [], "error": None}
    if not unifi_host or not unifi_api_key:
        context["error"] = "Enter a host and API key first."
    else:
        client = UnifiClient(http_client=http_client, api_key=unifi_api_key, host=unifi_host)
        try:
            context["sites"] = await client.list_sites()
        except UnifiProviderError as exc:
            logger.warning("UniFi site discovery failed: %s", exc)
            context["error"] = str(exc)
    return templates.TemplateResponse("partials/unifi_sites.html", context)


@router.get("/health/json")
async def health_json() -> dict:
    """
    Returns application health as a JSON response.

    Returns:
        A dict with a "status" key set to "ok".
    """
    return {"status": "ok"}


@router.get("/next-check-in")
async def next_check_in(request: Request) -> dict:
    """
    Returns the seconds remaining until the next scheduled DDNS check.

    Reads the live next_run_time from APScheduler so the dashboard countdown
    stays accurate across page refreshes.

    Args:
        request: The incoming FastAPI request.

    Returns:
        A dict with "seconds" (int) and "interval" (int) keys.
    """
    from datetime import datetime, timezone
    from repositories.config_repository import ConfigRepository
    from db.database import engine
    from sqlmodel import Session

    interval = 300
    try:
        with Session(engine) as session:
            interval = ConfigRepository(session).load().interval
    except Exception:
        pass

    seconds_remaining = interval
    try:
        scheduler = request.app.state.scheduler
        job = scheduler.get_job("ddns_check")
        if job and job.next_run_time:
            delta = job.next_run_time - datetime.now(timezone.utc)
            seconds_remaining = max(0, int(delta.total_seconds()))
    except Exception as exc:
        logger.debug("Could not read scheduler next_run_time: %s", exc)

    return {"seconds": seconds_remaining, "interval": interval}


# ---------------------------------------------------------------------------
# Records live refresh
# ---------------------------------------------------------------------------


def _to_local_policy_name(record_name: str) -> str:
    """Converts a managed FQDN to its .local counterpart (keep in sync with scheduler.py)."""
    name = record_name.strip()
    if name.endswith(".local"):
        return name
    parts = name.rsplit(".", 1)
    if len(parts) == 1:
        return name
    return f"{parts[0]}.local"


@router.get("/records", response_class=HTMLResponse)
async def get_records(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    dns_service: DnsService = Depends(get_dns_service),
    stats_repo: StatsRepository = Depends(get_stats_repo),
    unifi_client: UnifiClient = Depends(get_unifi_client),
    record_config_repo: RecordConfigRepository = Depends(get_record_config_repo),
) -> HTMLResponse:
    """
    Returns the managed records table as an HTMX fragment, plus OOB stat card updates.

    Triggered by the SSE `records_updated` event (or a manual sync).  Uses
    bulk zone + stats lookups to avoid N individual Cloudflare API calls.

    Args:
        request: The incoming FastAPI request.
        config_service: Provides configuration and managed records.
        dns_service: Fetches live DNS record state from Cloudflare.
        stats_repo: Provides per-record update/failure counters (bulk query).
        unifi_client: Fetches live UniFi DNS policies.
        record_config_repo: Provides per-record settings.

    Returns:
        An HTMLResponse with the records-table partial followed by
        hx-swap-oob elements that update the three dynamic stat cards.
    """
    config = await config_service.get_config()
    zones = await config_service.get_zones()
    managed_records = await config_service.get_managed_records()
    record_configs = record_config_repo.get_all(managed_records)

    # Fetch current public IP — fall back to empty string on failure.
    current_ip = ""
    try:
        from services.ip_service import IpService
        ip_service = IpService(request.app.state.http_client, app_state=request.app.state)
        current_ip = await ip_service.get_public_ip()
    except IpFetchError as exc:
        logger.warning("Could not fetch public IP for records refresh: %s", exc)

    _, _, unifi_site_id, unifi_default_ip, unifi_enabled = await config_service.get_unifi_config()
    unifi_policy_map: dict[str, object] = {}
    if unifi_enabled and unifi_client.is_configured() and unifi_site_id:
        try:
            policies = await unifi_client.list_records(unifi_site_id)
            unifi_policy_map = {p.name: p for p in policies}
        except UnifiProviderError as exc:
            logger.warning("UniFi policy fetch failed during records refresh: %s", exc)

    # Bulk DNS fetch (one call per zone) + bulk stats (one DB SELECT IN)
    zone_record_map: dict = {}
    if config.api_token and zones:
        try:
            zone_record_map = await dns_service.fetch_zone_record_map(managed_records, zones)
        except DnsProviderError as exc:
            logger.warning("records refresh: bulk CF lookup failed: %s", exc)

    stats_bulk = stats_repo.get_bulk(managed_records)

    record_data = []
    for record_name in managed_records:
        dns_record = zone_record_map.get(record_name)
        stats = stats_bulk.get(record_name)
        dns_ip = dns_record.content if dns_record else "Not Found"
        rc = record_configs.get(record_name)
        cf_enabled = rc.cf_enabled if rc else True
        if not cf_enabled:
            is_up_to_date = None
        else:
            is_up_to_date = dns_record is not None and (dns_ip == current_ip)

        unifi_policy = unifi_policy_map.get(record_name)
        unifi_local_policy = unifi_policy_map.get(_to_local_policy_name(record_name))

        record_data.append({
            "name": record_name,
            "cf_record_id": dns_record.id if dns_record else None,
            "dns_ip": dns_ip,
            "is_up_to_date": is_up_to_date,
            "updates": stats.updates if stats else 0,
            "failures": stats.failures if stats else 0,
            "last_checked": stats.last_checked.isoformat() if stats and stats.last_checked else None,
            "last_updated": stats.last_updated.isoformat() if stats and stats.last_updated else None,
            "unifi_ip": unifi_policy.content if unifi_policy else None,
            "unifi_local_ip": unifi_local_policy.content if unifi_local_policy else None,
            "unifi_record_id": unifi_policy.id if unifi_policy else None,
            "cfg_cf_enabled": rc.cf_enabled if rc else True,
            "cfg_ip_mode": rc.ip_mode if rc else "dynamic",
            "cfg_static_ip": rc.static_ip if rc else "",
            "cfg_unifi_enabled": rc.unifi_enabled if rc else False,
            "cfg_unifi_static_ip": rc.unifi_static_ip if rc else "",
            "cfg_unifi_local_enabled": rc.unifi_local_enabled if rc else False,
            "cfg_unifi_local_static_ip": rc.unifi_local_static_ip if rc else "",
        })

    # Render records table partial as the main swap target.
    records_html = templates.get_template("partials/records_table.html").render(
        {
            "request": request,
            "records": record_data,
            "unifi_enabled": unifi_enabled,
            "unifi_default_ip": unifi_default_ip,
        }
    )

    # Append OOB element so HTMX updates the managed-count stat card without a full page reload.
    oob = f'<span id="stat-managed" hx-swap-oob="true">{len(record_data)}</span>'

    return HTMLResponse(content=records_html + oob)


# ---------------------------------------------------------------------------
# Per-record error log
# ---------------------------------------------------------------------------


@router.get("/logs/record/{record_name:path}", response_class=HTMLResponse)
async def get_record_error_logs(
    request: Request,
    record_name: str,
    log_service: LogService = Depends(get_log_service),
) -> HTMLResponse:
    """
    Returns recent ERROR/WARNING log entries that mention the given record as an HTML fragment.

    Used by the dashboard to populate the inline error panel when the
    user clicks on a failure counter.

    Args:
        request: The incoming FastAPI request.
        record_name: The FQDN to filter log entries by (path parameter).
        log_service: Provides log entry access.

    Returns:
        An HTMLResponse containing a small HTML fragment with the matching entries.
    """
    entries = log_service.get_errors_for_record(record_name, limit=20)
    return templates.TemplateResponse(
        request,
        "partials/record_error_log.html",
        {"entries": entries, "record_name": record_name},
    )