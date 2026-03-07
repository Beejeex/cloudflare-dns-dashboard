"""
scheduler.py

Responsibility: Sets up the APScheduler AsyncIOScheduler and registers the
DDNS background check job. Exposes start/stop/reschedule helpers.
Does NOT: contain DNS business logic, config reading, or HTTP calls directly
— those are delegated entirely to DnsService and its collaborators.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session

from cloudflare.cloudflare_client import CloudflareClient
from cloudflare.dns_provider import DnsRecord
from cloudflare.unifi_client import UnifiClient
from db.database import engine
from exceptions import UnifiProviderError
from log_cleanup import run_cleanup
from repositories.config_repository import ConfigRepository
from repositories.record_config_repository import RecordConfigRepository
from repositories.stats_repository import StatsRepository
from services.dns_service import DnsService
from services.ip_service import IpService
from services.log_service import LogService
from services.stats_service import StatsService

if TYPE_CHECKING:
    from services.broadcast_service import BroadcastService

logger = logging.getLogger(__name__)

# Job ID used to identify the DDNS check job in APScheduler
_JOB_ID = "ddns_check"


def _to_local_policy_name(record_name: str) -> str:
    """
    Converts a managed FQDN into its UniFi local policy name.

    Replaces only the TLD (last label) with "local", preserving all
    intermediate labels so the full subdomain structure is retained.

    Args:
        record_name: Managed DNS name, e.g. "home.example.net".

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


# ---------------------------------------------------------------------------
# Scheduler job
# ---------------------------------------------------------------------------


async def _ddns_check_job(
    http_client: httpx.AsyncClient,
    unifi_http_client: httpx.AsyncClient,
    broadcaster: BroadcastService | None = None,
) -> None:
    """
    APScheduler job: runs one DDNS check cycle and optional log cleanup.

    Opens a fresh DB session for each run so stats and logs are committed
    atomically. All business logic is delegated to DnsService — this
    function only wires up collaborators and triggers the cycle.

    After the Cloudflare cycle, runs a UniFi sync pass for every record
    whose RecordConfig has unifi_enabled=True, creating or updating the
    corresponding UniFi DNS policy.

    After the full cycle completes, publishes SSE events via BroadcastService
    when one is provided:
    - ``ip_updated``      — current public IP JSON
    - ``records_updated`` — records-table HTML fragment (stats-only; no live CF call)
    - ``log_appended``    — empty signal to trigger log panel refresh

    Args:
        http_client: The long-lived shared httpx.AsyncClient from app.state.
        unifi_http_client: The UniFi-specific client (verify=False) from app.state.
        broadcaster: Optional BroadcastService to push SSE events after the cycle.

    Returns:
        None
    """
    logger.debug("DDNS check job triggered.")

    with Session(engine) as session:
        config_repo = ConfigRepository(session)
        stats_repo = StatsRepository(session)
        log_service = LogService(session)

        config = config_repo.load()
        zones = config_repo.get_zones(config)
        records = config_repo.get_records(config)

        if not config.api_token:
            logger.warning("No API token configured — skipping DDNS check cycle.")
            return

        # Load per-record settings so the cycle respects static IPs and disabled flags
        record_configs = RecordConfigRepository(session).get_all(records)

        cloudflare_client = CloudflareClient(
            http_client=http_client,
            api_token=config.api_token,
        )
        ip_service = IpService(http_client=http_client)
        stats_service = StatsService(stats_repo)
        dns_service = DnsService(cloudflare_client, ip_service, stats_service, log_service)

        await dns_service.run_check_cycle(records, zones, record_configs=record_configs)

        # -----------------------------------------------------------------
        # UniFi DNS policy sync
        # -----------------------------------------------------------------
        # For every managed record:
        #   unifi_enabled=True  → create or update the UniFi DNS policy
        #   unifi_enabled=False → delete the policy if one exists
        if config.unifi_enabled and config.unifi_host and config.unifi_api_key and config.unifi_site_id:
            unifi_client = UnifiClient(
                http_client=unifi_http_client,
                api_key=config.unifi_api_key,
                host=config.unifi_host,
            )
            unifi_enabled_records = [r for r in records if (rc := record_configs.get(r)) and rc.unifi_enabled]
            log_service.log(
                f"UniFi pass: syncing {len(unifi_enabled_records)} of {len(records)} record(s) to {config.unifi_host}.",
                level="INFO",
            )
            # NOTE: Fetch ALL existing policies in ONE call here, then do dict
            # lookups per record. Without this, each get_record() triggers a
            # separate GET /dns/policies, causing a burst that returns 502s.
            try:
                _all_policies = await unifi_client.list_records(config.unifi_site_id)
                existing_policies: dict[str, DnsRecord] = {p.name: p for p in _all_policies}
            except UnifiProviderError as exc:
                log_service.log(
                    f"UniFi pass: could not list policies — {exc}",
                    level="ERROR",
                )
                logger.error("UniFi list_records failed, aborting pass: %s", exc)
                return
            unifi_created = unifi_updated = unifi_unchanged = unifi_deleted = unifi_failed = 0

            for record_name in records:
                cfg = record_configs.get(record_name)

                # --- Deletion pass: remove policy when user disables UniFi for this record ---
                if cfg is None or not cfg.unifi_enabled:
                    try:
                        existing = existing_policies.get(record_name)
                        if existing is not None:
                            await unifi_client.delete_record(config.unifi_site_id, existing.id)
                            log_service.log(
                                f"UniFi: removed policy '{record_name}' (disabled by user).",
                                level="INFO",
                            )
                            unifi_deleted += 1
                    except UnifiProviderError as exc:
                        log_service.log(
                            f"UniFi: failed to remove policy '{record_name}' — {exc}",
                            level="ERROR",
                        )
                        logger.error("UniFi policy removal failed for %s: %s", record_name, exc)
                        unifi_failed += 1

                    # Also remove derived .local policy when UniFi is disabled.
                    local_record_name = _to_local_policy_name(record_name)
                    if local_record_name != record_name:
                        try:
                            existing_local = existing_policies.get(local_record_name)
                            if existing_local is not None:
                                await unifi_client.delete_record(config.unifi_site_id, existing_local.id)
                                log_service.log(
                                    f"UniFi: removed local policy '{local_record_name}' (disabled by user).",
                                    level="INFO",
                                )
                                unifi_deleted += 1
                        except UnifiProviderError as exc:
                            log_service.log(
                                f"UniFi: failed to remove local policy '{local_record_name}' — {exc}",
                                level="ERROR",
                            )
                            logger.error("UniFi local policy removal failed for %s: %s", local_record_name, exc)
                            unifi_failed += 1
                    continue

                # Determine target IP: per-record static → global default
                target_ip = (
                    cfg.unifi_static_ip.strip()
                    or config.unifi_default_ip.strip()
                )
                if not target_ip:
                    log_service.log(
                        f"UniFi: skipped '{record_name}' — no IP configured.",
                        level="WARNING",
                    )
                    unifi_failed += 1
                    continue

                try:
                    existing = existing_policies.get(record_name)
                    if existing is None:
                        await unifi_client.create_record(config.unifi_site_id, record_name, target_ip)
                        log_service.log(
                            f"UniFi: created policy '{record_name}' → {target_ip} ✓",
                            level="INFO",
                        )
                        unifi_created += 1
                    elif existing.content != target_ip:
                        await unifi_client.update_record(config.unifi_site_id, existing, target_ip)
                        log_service.log(
                            f"UniFi: updated policy '{record_name}' → {target_ip} ✓",
                            level="INFO",
                        )
                        unifi_updated += 1
                    else:
                        logger.debug("UniFi policy '%s' already up to date (%s).", record_name, target_ip)
                        log_service.log(
                            f"UniFi: '{record_name}' already in sync ({target_ip}).",
                            level="INFO",
                        )
                        unifi_unchanged += 1
                    # NOTE: Stamp last_checked so CF-disabled records always show
                    # a timestamp on the dashboard, not just CF-enabled ones.
                    stats_repo.record_check(record_name)
                except UnifiProviderError as exc:
                    log_service.log(
                        f"UniFi: failed to sync '{record_name}' — {exc}",
                        level="ERROR",
                    )
                    logger.error("UniFi sync failed for %s: %s", record_name, exc)
                    unifi_failed += 1

                # --- Optional .local sync pass for this record ---
                local_record_name = _to_local_policy_name(record_name)
                # NOTE: If the managed record itself is already *.local there is
                # no separate secondary name to manage.
                if local_record_name == record_name:
                    continue

                if not cfg.unifi_local_enabled:
                    try:
                        existing_local = existing_policies.get(local_record_name)
                        if existing_local is not None:
                            await unifi_client.delete_record(config.unifi_site_id, existing_local.id)
                            log_service.log(
                                f"UniFi: removed local policy '{local_record_name}' (disabled by user).",
                                level="INFO",
                            )
                            unifi_deleted += 1
                    except UnifiProviderError as exc:
                        log_service.log(
                            f"UniFi: failed to remove local policy '{local_record_name}' — {exc}",
                            level="ERROR",
                        )
                        logger.error("UniFi local policy removal failed for %s: %s", local_record_name, exc)
                        unifi_failed += 1
                    continue

                local_target_ip = (
                    cfg.unifi_local_static_ip.strip()
                    or cfg.unifi_static_ip.strip()
                    or config.unifi_default_ip.strip()
                )
                if not local_target_ip:
                    log_service.log(
                        f"UniFi: skipped local policy '{local_record_name}' — no IP configured.",
                        level="WARNING",
                    )
                    unifi_failed += 1
                    continue

                try:
                    existing_local = existing_policies.get(local_record_name)
                    if existing_local is None:
                        await unifi_client.create_record(config.unifi_site_id, local_record_name, local_target_ip)
                        log_service.log(
                            f"UniFi: created local policy '{local_record_name}' → {local_target_ip} ✓",
                            level="INFO",
                        )
                        unifi_created += 1
                    elif existing_local.content != local_target_ip:
                        await unifi_client.update_record(config.unifi_site_id, existing_local, local_target_ip)
                        log_service.log(
                            f"UniFi: updated local policy '{local_record_name}' → {local_target_ip} ✓",
                            level="INFO",
                        )
                        unifi_updated += 1
                    else:
                        log_service.log(
                            f"UniFi: local '{local_record_name}' already in sync ({local_target_ip}).",
                            level="INFO",
                        )
                        unifi_unchanged += 1
                except UnifiProviderError as exc:
                    log_service.log(
                        f"UniFi: failed to sync local '{local_record_name}' — {exc}",
                        level="ERROR",
                    )
                    logger.error("UniFi local sync failed for %s: %s", local_record_name, exc)
                    unifi_failed += 1

            # Summary log for the UniFi pass
            summary_parts: list[str] = []
            if unifi_unchanged:
                summary_parts.append(f"{unifi_unchanged} in sync")
            if unifi_created:
                summary_parts.append(f"{unifi_created} created")
            if unifi_updated:
                summary_parts.append(f"{unifi_updated} updated")
            if unifi_deleted:
                summary_parts.append(f"{unifi_deleted} removed")
            if unifi_failed:
                summary_parts.append(f"{unifi_failed} failed")
            log_service.log(
                "UniFi pass complete: " + (", ".join(summary_parts) if summary_parts else "nothing to do") + ".",
                level="INFO" if not unifi_failed else "WARNING",
            )

        # Run daily log cleanup at the end of each cycle if due
        run_cleanup(session, days_to_keep=7)

    # -------------------------------------------------------------------------
    # SSE broadcasts — fire after the DB session is closed (data is committed)
    # -------------------------------------------------------------------------
    if broadcaster is not None:
        import asyncio  # noqa: PLC0415 — local import keeps startup fast
        from shared_templates import templates  # noqa: PLC0415

        # Publish ip_updated with the last successfully fetched IP.
        # NOTE: ip_service is out of scope here; re-fetch from ipify (cached on
        # app.state when the scheduler was called with app_state, but the
        # scheduler job doesn't carry app_state — so this is a lightweight call
        # that will hit the cache if the job used the same client recently).
        try:
            _ip_svc = IpService(http_client=http_client)
            _current_ip = await _ip_svc.get_public_ip()
            # NOTE: Plain text — HTMX sse-swap uses it as innerHTML directly
            broadcaster.publish("ip_updated", _current_ip)
        except Exception as exc:
            logger.debug("Broadcaster: could not publish ip_updated: %s", exc)

        # Publish records_updated with a stats-based render (no extra CF calls).
        # This gives connected clients a quick UI update; the SSE on-connect
        # render provides the full live state for newly connected clients.
        try:
            with Session(engine) as _bcast_session:
                _bcast_config_repo = ConfigRepository(_bcast_session)
                _bcast_config = _bcast_config_repo.load()
                _bcast_records = _bcast_config_repo.get_records(_bcast_config)
                _bcast_stats = StatsRepository(_bcast_session).get_bulk(_bcast_records)
                _bcast_cfgs = RecordConfigRepository(_bcast_session).get_all(_bcast_records)
                _, _, _, _unifi_default_ip, _unifi_enabled = (
                    _bcast_config.unifi_host,
                    _bcast_config.unifi_api_key,
                    _bcast_config.unifi_site_id,
                    _bcast_config.unifi_default_ip,
                    _bcast_config.unifi_enabled,
                )
            rows = [
                {
                    "name": r,
                    "cf_record_id": None,
                    "dns_ip": "\u2014",
                    "is_up_to_date": None,
                    "updates": (_bcast_stats.get(r).updates if _bcast_stats.get(r) else 0),
                    "failures": (_bcast_stats.get(r).failures if _bcast_stats.get(r) else 0),
                    "last_checked": (
                        _bcast_stats.get(r).last_checked.isoformat()
                        if _bcast_stats.get(r) and _bcast_stats.get(r).last_checked else None
                    ),
                    "last_updated": (
                        _bcast_stats.get(r).last_updated.isoformat()
                        if _bcast_stats.get(r) and _bcast_stats.get(r).last_updated else None
                    ),
                    "unifi_ip": None,
                    "unifi_local_ip": None,
                    "unifi_record_id": None,
                    "cfg_cf_enabled": _bcast_cfgs.get(r).cf_enabled if _bcast_cfgs.get(r) else False,
                    "cfg_ip_mode": _bcast_cfgs.get(r).ip_mode if _bcast_cfgs.get(r) else "dynamic",
                    "cfg_static_ip": _bcast_cfgs.get(r).static_ip if _bcast_cfgs.get(r) else "",
                    "cfg_unifi_enabled": _bcast_cfgs.get(r).unifi_enabled if _bcast_cfgs.get(r) else False,
                    "cfg_unifi_static_ip": _bcast_cfgs.get(r).unifi_static_ip if _bcast_cfgs.get(r) else "",
                    "cfg_unifi_local_enabled": _bcast_cfgs.get(r).unifi_local_enabled if _bcast_cfgs.get(r) else False,
                    "cfg_unifi_local_static_ip": _bcast_cfgs.get(r).unifi_local_static_ip if _bcast_cfgs.get(r) else "",
                }
                for r in _bcast_records
            ]
            _html = templates.get_template("partials/records_table.html").render(
                {"records": rows, "unifi_enabled": _unifi_enabled, "unifi_default_ip": _unifi_default_ip}
            )
            broadcaster.publish("records_updated", _html)
        except Exception as exc:
            logger.warning("Broadcaster: could not publish records_updated: %s", exc)

        # Signal log panel to refresh
        broadcaster.publish("log_appended", "{}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_scheduler(
    http_client: httpx.AsyncClient,
    unifi_http_client: httpx.AsyncClient,
    interval_seconds: int = 300,
    broadcaster: BroadcastService | None = None,
) -> AsyncIOScheduler:
    """
    Creates and returns a configured AsyncIOScheduler with the DDNS check job.

    The job runs immediately on startup (next_run_time=now) and then at the
    configured interval.

    Args:
        http_client: The shared httpx.AsyncClient to pass into the job.
        unifi_http_client: The UniFi-specific client (verify=False) to pass into the job.
        interval_seconds: Seconds between DDNS check cycles (default 300).
        broadcaster: Optional BroadcastService for SSE push after each cycle.

    Returns:
        A configured but not yet started AsyncIOScheduler.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _ddns_check_job,
        trigger="interval",
        seconds=interval_seconds,
        id=_JOB_ID,
        kwargs={
            "http_client": http_client,
            "unifi_http_client": unifi_http_client,
            "broadcaster": broadcaster,
        },
        # NOTE: next_run_time=now triggers the first check immediately on startup
        # rather than waiting a full interval before the first run.
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,  # Prevent overlapping runs if a cycle takes too long
    )
    logger.info("DDNS check job scheduled — interval: %ds.", interval_seconds)
    return scheduler


def reschedule(scheduler: AsyncIOScheduler, http_client: httpx.AsyncClient, interval_seconds: int) -> None:
    """
    Changes the DDNS check job's interval without restarting the scheduler.

    Called by action routes when the user saves a new check interval via the UI.

    Args:
        scheduler: The running AsyncIOScheduler instance from app.state.
        http_client: The shared httpx.AsyncClient (passed to the rescheduled job).
        interval_seconds: New interval in seconds.

    Returns:
        None
    """
    scheduler.reschedule_job(
        _JOB_ID,
        trigger="interval",
        seconds=interval_seconds,
    )
    logger.info("DDNS check job rescheduled — new interval: %ds.", interval_seconds)


async def run_ddns_check_now(
    http_client: httpx.AsyncClient,
    unifi_http_client: httpx.AsyncClient,
    broadcaster: BroadcastService | None = None,
) -> None:
    """
    Runs one DDNS check cycle immediately, outside the normal schedule.

    Intended for the manual "Sync Now" UI trigger. Delegates entirely to
    _ddns_check_job so behaviour is identical to a scheduled run.

    Args:
        http_client: The shared httpx.AsyncClient from app.state.
        unifi_http_client: The UniFi-specific client from app.state.
        broadcaster: Optional BroadcastService for SSE push after the cycle.

    Returns:
        None
    """
    logger.info("Manual sync triggered via UI.")
    await _ddns_check_job(
        http_client=http_client,
        unifi_http_client=unifi_http_client,
        broadcaster=broadcaster,
    )
