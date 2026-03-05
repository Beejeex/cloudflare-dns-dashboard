"""
routes/ui_routes.py

Responsibility: GET handlers that render full HTML pages using Jinja2 templates.
Does NOT: mutate state, return HTMX fragments, or call DNS/IP services directly.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from dependencies import (
    get_config_service,
    get_dns_service,
    get_kubernetes_service,
    get_log_service,
    get_record_config_repo,
    get_stats_service,
    get_unifi_client,
)
from exceptions import DnsProviderError, IpFetchError, KubernetesError, UnifiProviderError
from cloudflare.unifi_client import UnifiClient
from repositories.record_config_repository import RecordConfigRepository
from services.config_service import ConfigService
from services.dns_service import DnsService
from services.kubernetes_service import KubernetesService
from services.log_service import LogService
from services.stats_service import StatsService

logger = logging.getLogger(__name__)

router = APIRouter()
from shared_templates import templates  # noqa: E402


def _to_local_policy_name(record_name: str) -> str:  # noqa: keep in sync with scheduler.py
    """
    Converts a managed FQDN into its UniFi local policy name.

    Replaces only the TLD (last label) with "local", preserving all
    intermediate labels so the full subdomain structure is retained.

    Args:
        record_name: Managed FQDN, e.g. "home.example.net".

    Returns:
        Local DNS name, e.g. "home.example.local".
    """
    name = record_name.strip()
    if name.endswith(".local"):
        return name
    # rsplit on the last dot so we keep all intermediate labels intact.
    parts = name.rsplit(".", 1)
    if len(parts) == 1:
        # No dot present — nothing to replace.
        return name
    return f"{parts[0]}.local"


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    dns_service: DnsService = Depends(get_dns_service),
    stats_service: StatsService = Depends(get_stats_service),
    kubernetes_service: KubernetesService = Depends(get_kubernetes_service),
    unifi_client: UnifiClient = Depends(get_unifi_client),
    record_config_repo: RecordConfigRepository = Depends(get_record_config_repo),
) -> HTMLResponse:
    """
    Renders the main DDNS dashboard page.

    Shows per-record DNS status across Cloudflare and UniFi, stats,
    a live countdown to the next check, and (when enabled) hostnames
    discovered from Kubernetes Ingress resources.

    Args:
        request: The incoming FastAPI request.
        config_service: Provides application configuration.
        dns_service: Fetches live record state from the DNS provider.
        stats_service: Provides per-record update/failure stats.
        kubernetes_service: Discovers hostnames from cluster Ingress resources.
        unifi_client: Fetches internal DNS policies from UniFi.

    Returns:
        An HTMLResponse rendering templates/dashboard.html.
    """
    config = await config_service.get_config()
    zones = await config_service.get_zones()
    managed_records = await config_service.get_managed_records()
    local_parent_by_name = {
        _to_local_policy_name(name): name
        for name in managed_records
        if _to_local_policy_name(name) != name
    }

    # Load all per-record settings up front in one query
    record_configs = record_config_repo.get_all(managed_records)

    # Fetch current public IP — display "Unavailable" on failure rather than 500
    current_ip = "Unavailable"
    try:
        from services.ip_service import IpService
        ip_service = IpService(request.app.state.http_client)
        current_ip = await ip_service.get_public_ip()
    except IpFetchError as exc:
        logger.warning("Could not fetch public IP for dashboard: %s", exc)

    # Detect not-yet-configured state before hitting the API
    api_error: str | None = None
    if not config.api_token or not zones:
        api_error = "No API token or zones configured. Go to Settings to set them up."

    # Build per-record display data (Cloudflare + UniFi side by side)
    record_data = []

    # Fetch all UniFi DNS policies in one call upfront (avoid N per-record requests)
    unifi_error: str | None = None
    unifi_policy_map: dict[str, object] = {}
    _, _, unifi_site_id, unifi_default_ip, unifi_enabled = await config_service.get_unifi_config()
    if unifi_enabled and unifi_client.is_configured() and unifi_site_id:
        try:
            policies = await unifi_client.list_records(unifi_site_id)
            unifi_policy_map = {p.name: p for p in policies}
        except UnifiProviderError as exc:
            logger.warning("UniFi DNS policy fetch failed: %s", exc)
            unifi_error = str(exc)

    # Discover hostnames from Kubernetes Ingress resources before the managed loop
    # so per-record entries can include k8s_namespace / k8s_ingress_name.
    k8s_records: list = []
    k8s_error: str | None = None
    if kubernetes_service.is_enabled():
        try:
            k8s_records = await kubernetes_service.list_ingress_records()
        except KubernetesError as exc:
            logger.warning("Kubernetes ingress discovery failed: %s", exc)
            k8s_error = str(exc)
    k8s_by_hostname = {r.hostname: r for r in k8s_records}

    for record_name in managed_records:
        dns_record = None
        try:
            dns_record = await dns_service.check_single_record(record_name, zones)
        except DnsProviderError as exc:
            logger.warning("Could not fetch DNS record %s: %s", record_name, exc)
            if not api_error:
                api_error = str(exc)

        stats = await stats_service.get_for_record(record_name)
        dns_ip = dns_record.content if dns_record else "Not Found"
        rc = record_configs.get(record_name)
        cf_enabled = rc.cf_enabled if rc else True
        # NOTE: Only evaluate up-to-date status when CF DDNS is enabled for this record.
        # If CF is disabled, the record won't exist in Cloudflare by design — show Unknown,
        # not "Needs update", to avoid misleading the user.
        if not cf_enabled:
            is_up_to_date = None
        else:
            is_up_to_date = dns_record is not None and dns_ip == current_ip

        # NOTE: Match unified policy by domain name from the pre-fetched map
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
            # Per-record settings (from RecordConfig, defaults if no row exists)
            "cfg_cf_enabled": rc.cf_enabled if rc else True,
            "cfg_ip_mode": rc.ip_mode if rc else "dynamic",
            "cfg_static_ip": rc.static_ip if rc else "",
            "cfg_unifi_enabled": rc.unifi_enabled if rc else False,
            "cfg_unifi_static_ip": rc.unifi_static_ip if rc else "",
            "cfg_unifi_local_enabled": rc.unifi_local_enabled if rc else False,
            "cfg_unifi_local_static_ip": rc.unifi_local_static_ip if rc else "",
            # K8s ingress — populated when a matching Ingress hostname is found
            "k8s_namespace": k8s_by_hostname[record_name].namespace if record_name in k8s_by_hostname else None,
            "k8s_ingress_name": k8s_by_hostname[record_name].ingress_name if record_name in k8s_by_hostname else None,
        })

    # Fetch all A-records in the zone for the discovery panel
    zone_records: list = []
    zone_records_error: str | None = None
    if not api_error:
        try:
            zone_records = await dns_service.list_zone_records(zones)
        except DnsProviderError as exc:
            logger.warning("Could not fetch zone records: %s", exc)
            zone_records_error = str(exc)

    # Build unified discovery list — one entry per hostname, merging CF, UniFi and K8s.
    # Keyed by hostname so sources are automatically coalesced.
    discovery_map: dict[str, dict] = {}

    def _entry(name: str) -> dict:
        return {
            "name": name,
            "cf_ip": None, "cf_record_id": None,
            "unifi_ip": None, "unifi_record_id": None,
            "unifi_local_ip": None, "unifi_local_record_id": None,
            "k8s_namespace": None, "k8s_ingress_name": None,
            "local_only": False,
        }

    # Pass 1: Add all sources into discovery_map without .local merging yet.
    # UniFi .local policies store into unifi_local_* on their own provisional entry
    # so that pass 2 can find them regardless of which source provided the parent.
    for r in zone_records:
        e = discovery_map.setdefault(r.name, _entry(r.name))
        e["cf_ip"] = r.content
        e["cf_record_id"] = r.id

    for name, policy in unifi_policy_map.items():
        e = discovery_map.setdefault(name, _entry(name))
        if name.endswith(".local"):
            # Store local data on this provisional entry; pass 2 will merge it
            # into the non-.local parent once all sources are loaded.
            e["unifi_local_ip"] = policy.content
            e["unifi_local_record_id"] = policy.id
        else:
            e["unifi_ip"] = policy.content
            e["unifi_record_id"] = policy.id

    for r in k8s_records:
        e = discovery_map.setdefault(r.hostname, _entry(r.hostname))
        e["k8s_namespace"] = r.namespace
        e["k8s_ingress_name"] = r.ingress_name

    # Pass 2: Merge standalone *.local entries into their non-.local parent card.
    # A parent is found by:
    #   1. The explicit managed-record mapping (local_parent_by_name), or
    #   2. Any existing discovery entry whose name shares the same subdomain
    #      prefix (everything before the last dot) and is not itself .local.
    # This handles the case where the parent is discovered only via K8s or CF
    # and is therefore absent from local_parent_by_name.
    # When NO parent exists at all (truly orphaned .local policy), the entry is
    # renamed to the stripped parent name so the + Manage button adds the right
    # record, and is tagged local_only=True so the template can hint the route
    # to auto-enable unifi_local_enabled.
    local_names_to_remove: list[str] = []
    for name in list(discovery_map.keys()):
        if not name.endswith(".local"):
            continue
        prefix = name[: -len(".local")]  # e.g. "headlamp.batenryck"
        parent_name: str | None = local_parent_by_name.get(name)
        if not parent_name:
            for existing in discovery_map:
                if (
                    existing != name
                    and not existing.endswith(".local")
                    and existing.startswith(prefix + ".")
                ):
                    parent_name = existing
                    break
        if parent_name and parent_name in discovery_map:
            local_entry = discovery_map[name]
            parent_entry = discovery_map[parent_name]
            # NOTE: Only copy if the parent does not already have local data set
            # by the managed-record pre-fetch path above.
            if not parent_entry["unifi_local_ip"] and local_entry["unifi_local_ip"]:
                parent_entry["unifi_local_ip"] = local_entry["unifi_local_ip"]
                parent_entry["unifi_local_record_id"] = local_entry["unifi_local_record_id"]
            local_names_to_remove.append(name)
        else:
            # No parent found anywhere — rename this entry to the reconstructed
            # FQDN (matching against configured zones) so + Manage adds the
            # correct record name (e.g. "longhorn.batenryck.net" instead of
            # the bare prefix "longhorn.batenryck"). Mark local_only=True so
            # the route auto-enables unifi_local_enabled on the new record.
            #
            # Zone match: for zone "batenryck.net" split into sld="batenryck"
            # and tld="net". If prefix ends with ".batenryck" the full FQDN
            # is reconstructed as prefix + "." + tld.
            reconstructed = prefix  # fallback: leave as-is when no zone matches
            for zone_domain in zones:
                parts = zone_domain.rsplit(".", 1)
                if len(parts) == 2:
                    sld, tld = parts
                    if prefix.endswith("." + sld) or prefix == sld:
                        reconstructed = prefix + "." + tld
                        break
            local_entry = discovery_map.pop(name)
            stripped_entry = _entry(reconstructed)
            stripped_entry["unifi_local_ip"] = local_entry["unifi_local_ip"]
            stripped_entry["unifi_local_record_id"] = local_entry["unifi_local_record_id"]
            stripped_entry["local_only"] = True
            discovery_map[reconstructed] = stripped_entry

    for name in local_names_to_remove:
        del discovery_map[name]

    discovery_records: list[dict] = sorted(discovery_map.values(), key=lambda x: x["name"])

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "current_ip": current_ip,
            "records": record_data,
            "interval": config.interval,
            "api_error": api_error,
            "managed_names": managed_records,
            "unifi_enabled": unifi_enabled,
            "unifi_default_ip": unifi_default_ip,
            "unifi_error": unifi_error,
            "discovery_records": discovery_records,
            "zone_records_error": zone_records_error,
            "k8s_enabled": kubernetes_service.is_enabled(),
            "k8s_error": k8s_error,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    log_service: LogService = Depends(get_log_service),
    config_service: ConfigService = Depends(get_config_service),
) -> HTMLResponse:
    """
    Renders the full-page activity log viewer.

    Args:
        request: The incoming FastAPI request.
        log_service: Provides recent log entries.
        config_service: Provides the UI refresh interval for HTMX polling.

    Returns:
        An HTMLResponse rendering templates/logs.html.
    """
    recent_logs = log_service.get_recent(limit=200)
    refresh = await config_service.get_refresh_interval()
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "logs": recent_logs,
            "refresh": refresh,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
) -> HTMLResponse:
    """
    Renders the settings / configuration page.

    Args:
        request: The incoming FastAPI request.
        config_service: Provides current application configuration.

    Returns:
        An HTMLResponse rendering templates/settings.html.
    """
    import json
    config = await config_service.get_config()
    zones = await config_service.get_zones()
    refresh = await config_service.get_refresh_interval()
    unifi_host, unifi_api_key, unifi_site_id, unifi_default_ip, unifi_enabled = await config_service.get_unifi_config()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "api_token": config.api_token,
            "zones": json.dumps(zones),
            "interval": config.interval,
            "refresh": refresh,
            "k8s_enabled": config.k8s_enabled,
            "unifi_host": unifi_host,
            "unifi_api_key": unifi_api_key,
            "unifi_site_id": unifi_site_id,
            "unifi_default_ip": unifi_default_ip,
            "unifi_enabled": unifi_enabled,
        },
    )
