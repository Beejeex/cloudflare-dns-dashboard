"""
services/dns_service.py

Responsibility: Orchestrates the DDNS update cycle — compares each managed
record's current IP against the host's public IP and triggers updates when
they differ.
Does NOT: make HTTP calls directly, read configuration from the DB, or
manage log file I/O.
"""

from __future__ import annotations

import logging

import tldextract

from cloudflare.dns_provider import DnsRecord, DNSProvider
from db.models import RecordConfig
from exceptions import DnsProviderError, IpFetchError
from services.ip_service import IpService
from services.log_service import LogService
from services.stats_service import StatsService

logger = logging.getLogger(__name__)


class DnsService:
    """
    Orchestrates the DDNS update cycle for all managed DNS records.

    For each managed record, compares the IP stored in the DNS provider
    against the host's current public IP and updates the record when they
    differ.  All results are recorded by StatsService and surfaced via
    LogService.

    Collaborators:
        - DNSProvider: abstract interface satisfied by CloudflareClient (or
          any future provider such as a Kubernetes Ingress writer)
        - IpService: provides the current public IP
        - StatsService: records update/failure counts per record
        - LogService: writes UI-visible activity log entries
    """

    def __init__(
        self,
        dns_provider: DNSProvider,
        ip_service: IpService,
        stats_service: StatsService,
        log_service: LogService,
    ) -> None:
        """
        Initialises the service with all required collaborators.

        Args:
            dns_provider: Any DNSProvider implementation (e.g. CloudflareClient).
            ip_service: Provides the current public IP of the host machine.
            stats_service: Records per-record check/update/failure stats.
            log_service: Writes UI-visible log entries for all DDNS events.
        """
        self._provider = dns_provider
        self._ip_service = ip_service
        self._stats = stats_service
        self._log = log_service

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def run_check_cycle(
        self,
        managed_records: list[str],
        zones: dict[str, str],
        record_configs: dict[str, RecordConfig] | None = None,
    ) -> None:
        """
        Runs a single DDNS check/update cycle for all managed records.

        Fetches the current public IP once, then checks each record. Respects
        per-record settings from RecordConfig:
        - cf_enabled=False  → record is skipped entirely this cycle
        - ip_mode='static'  → uses cfg.static_ip instead of the public IP
        - ip_mode='dynamic' → uses the detected public IP (default behaviour)

        Args:
            managed_records: List of FQDNs to check, e.g. ["home.example.com"].
            zones: Mapping of base domain to provider zone ID,
                   e.g. {"example.com": "zone_id_abc"}.
            record_configs: Optional per-record config map returned by
                RecordConfigRepository.get_all(). When None all records
                default to CF-enabled, dynamic mode.

        Returns:
            None
        """
        if not managed_records:
            logger.debug("No managed records — skipping check cycle.")
            return

        configs = record_configs or {}

        # Fetch the current public IP once — may be overridden per record in static mode
        current_ip: str | None = None
        try:
            current_ip = await self._ip_service.get_public_ip()
        except IpFetchError as exc:
            # NOTE: Only fatal if every managed record uses dynamic mode.
            # Static-IP records can still be checked without a public IP.
            all_static = all(
                configs.get(r) and configs[r].ip_mode == "static" and configs[r].static_ip
                for r in managed_records
            )
            if not all_static:
                self._log.log(f"Could not fetch public IP: {exc}", level="ERROR")
                logger.error("IP fetch failed; aborting check cycle: %s", exc)
                return
            logger.warning("IP fetch failed but all records are static — continuing: %s", exc)

        logger.info("Check cycle started — current IP: %s", current_ip)
        self._log.log(f"Check cycle started. Current public IP: {current_ip or 'N/A (static mode)'}", level="INFO")

        updated_count = 0
        skipped_count = 0
        failed_count = 0

        for record_name in managed_records:
            cfg = configs.get(record_name)

            # Skip CF update entirely if the user has disabled Cloudflare for this record
            if cfg is not None and not cfg.cf_enabled:
                logger.debug("Cloudflare DDNS disabled for %s — skipping.", record_name)
                # Auto-clear any stale failures that accumulated while CF was enabled.
                prior_stats = await self._stats.get_for_record(record_name)
                if prior_stats and prior_stats.failures > 0:
                    await self._stats.reset_failures(record_name)
                    self._log.log(
                        f"Cloudflare: {record_name} — stale failure(s) cleared (CF disabled).",
                        level="INFO",
                    )
                skipped_count += 1
                continue

            # Determine which IP to target: static override or detected public IP
            if cfg is not None and cfg.ip_mode == "static" and cfg.static_ip:
                target_ip = cfg.static_ip
                logger.debug("%s using static IP %s.", record_name, target_ip)
            else:
                if current_ip is None:
                    self._log.log(
                        f"Skipped {record_name}: public IP unavailable and no static IP configured.",
                        level="WARNING",
                    )
                    skipped_count += 1
                    continue
                target_ip = current_ip

            result = await self._check_record(record_name, target_ip, zones)
            if result == "updated":
                updated_count += 1
            elif result == "failed":
                failed_count += 1

        # Log a compact cycle summary instead of per-record noise for skipped records
        active_count = len(managed_records) - skipped_count
        summary_parts = [f"{active_count} record(s) checked via Cloudflare"]
        if skipped_count:
            summary_parts.append(f"{skipped_count} skipped (CF DDNS disabled)")
        if updated_count:
            summary_parts.append(f"{updated_count} updated")
        if failed_count:
            summary_parts.append(f"{failed_count} failed")
        self._log.log("Cloudflare pass: " + ", ".join(summary_parts) + ".", level="INFO")

    async def fetch_zone_record_map(
        self,
        managed_records: list[str],
        zones: dict[str, str],
    ) -> dict[str, DnsRecord | None]:
        """
        Returns a map of {fqdn: DnsRecord | None} for all managed records.

        Calls list_records() exactly once per unique zone rather than once per
        record, reducing Cloudflare API calls from N (one per record) to Z
        (one per zone).  Records not found in their zone map to None.

        Args:
            managed_records: List of FQDNs whose current state is needed.
            zones: Mapping of base domain to provider zone ID.

        Returns:
            dict keyed by FQDN.  Value is the DnsRecord if the record exists
            in the zone, or None if it was not found.
        """
        # --- Determine which zone IDs are needed ---
        zone_to_records: dict[str, list[str]] = {}
        result: dict[str, DnsRecord | None] = {r: None for r in managed_records}

        for record_name in managed_records:
            zone_id = self._resolve_zone_id(record_name, zones)
            if zone_id is not None:
                zone_to_records.setdefault(zone_id, []).append(record_name)

        # --- One list_records() call per zone ---
        for zone_id, names in zone_to_records.items():
            try:
                all_zone_records = await self._provider.list_records(zone_id)
                # Build a name → DnsRecord lookup from the full zone listing
                by_name = {r.name: r for r in all_zone_records}
                for name in names:
                    result[name] = by_name.get(name)
            except DnsProviderError as exc:
                logger.warning(
                    "fetch_zone_record_map: could not list records for zone %s: %s",
                    zone_id,
                    exc,
                )
                # Leave affected records as None — caller handles missing entries

        return result

    async def check_single_record(
        self,
        record_name: str,
        zones: dict[str, str],
    ) -> DnsRecord | None:
        """
        Fetches the current DNS record from the provider without updating it.

        Used by route handlers to display the live record state in the UI.

        Args:
            record_name: The fully-qualified DNS name to look up.
            zones: Mapping of base domain to provider zone ID.

        Returns:
            The DnsRecord if found, or None if the record does not exist.

        Raises:
            DnsProviderError: If the provider API call fails.
        """
        zone_id = self._resolve_zone_id(record_name, zones)
        if zone_id is None:
            return None
        return await self._provider.get_record(zone_id, record_name)

    async def list_zone_records(self, zones: dict[str, str]) -> list[DnsRecord]:
        """
        Returns all A-records across all configured zones.

        Used by the UI to populate the "add record" dropdown.

        Args:
            zones: Mapping of base domain to provider zone ID.

        Returns:
            A flat list of DnsRecord instances from all configured zones.
        """
        all_records: list[DnsRecord] = []
        errors: list[str] = []
        for zone_id in zones.values():
            try:
                records = await self._provider.list_records(zone_id)
                all_records.extend(records)
            except DnsProviderError as exc:
                logger.warning("Could not list records for zone %s: %s", zone_id, exc)
                errors.append(str(exc))

        # NOTE: Re-raise if every zone failed so callers can surface the error.
        if errors and not all_records:
            raise DnsProviderError(errors[0])
        return all_records

    async def create_dns_record(
        self,
        record_name: str,
        ip: str,
        zones: dict[str, str],
    ) -> DnsRecord:
        """
        Creates a new Cloudflare A-record and logs the action.

        Args:
            record_name: The fully-qualified DNS name to create.
            ip: The IPv4 address for the new record.
            zones: Mapping of base domain to provider zone ID.

        Returns:
            The newly created DnsRecord.

        Raises:
            DnsProviderError: If no zone is configured for the name, or if
                the provider API call fails.
        """
        zone_id = self._resolve_zone_id(record_name, zones)
        if zone_id is None:
            raise DnsProviderError(f"No zone configured for record: {record_name}")
        record = await self._provider.create_record(zone_id, record_name, ip)
        self._log.log(f"Created DNS record: {record_name} → {ip}", level="INFO")
        return record

    async def delete_dns_record(
        self,
        record_id: str,
        record_name: str,
        zones: dict[str, str],
    ) -> None:
        """
        Deletes a DNS record from the provider and removes it from tracking.

        Args:
            record_id: The provider-assigned unique identifier of the record.
            record_name: The FQDN of the record (used for zone resolution).
            zones: Mapping of base domain to provider zone ID.

        Returns:
            None

        Raises:
            DnsProviderError: If the provider API call fails.
        """
        zone_id = self._resolve_zone_id(record_name, zones)
        if zone_id is None:
            raise DnsProviderError(f"No zone configured for record: {record_name}")
        await self._provider.delete_record(zone_id, record_id)
        self._log.log(f"Deleted DNS record: {record_name}", level="INFO")
        await self._stats.delete_for_record(record_name)

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    async def _check_record(
        self,
        record_name: str,
        target_ip: str,
        zones: dict[str, str],
    ) -> str:
        """
        Checks a single DNS record and updates it if the IP has changed.

        All outcomes (up-to-date, updated, failed) are recorded in stats
        and the log service. If the record had prior failures and the current
        check succeeds, the failure counter is automatically reset to zero
        and a recovery entry is written to the activity log.

        Args:
            record_name: The FQDN to check.
            target_ip: The desired IP for the record. May be the current public IP
                (dynamic mode) or a user-configured static IP.
            zones: Mapping of base domain to provider zone ID.

        Returns:
            "updated"   — record was updated
            "unchanged" — record was already correct
            "failed"    — an error occurred
        """
        zone_id = self._resolve_zone_id(record_name, zones)
        if zone_id is None:
            self._log.log(
                f"No zone configured for {record_name} — skipping.",
                level="WARNING",
            )
            await self._stats.record_failed(record_name)
            return "failed"

        try:
            # NOTE: Snapshot the failure count BEFORE the check so we can detect
            # recovery (failures > 0 → next successful outcome).
            prior_stats = await self._stats.get_for_record(record_name)
            prior_failures = prior_stats.failures if prior_stats is not None else 0

            dns_record = await self._provider.get_record(zone_id, record_name)
            await self._stats.record_checked(record_name)

            if dns_record is None:
                # Record doesn't exist in Cloudflare yet — create it automatically.
                self._log.log(
                    f"Cloudflare: record not found — creating {record_name} → {target_ip}…",
                    level="INFO",
                )
                await self._provider.create_record(zone_id, record_name, target_ip)
                self._log.log(
                    f"Cloudflare: created {record_name} → {target_ip} ✓",
                    level="INFO",
                )
                await self._stats.record_updated(record_name)
                if prior_failures > 0:
                    await self._stats.reset_failures(record_name)
                return "updated"

            if dns_record.content == target_ip:
                logger.debug("%s is already up to date (%s).", record_name, target_ip)
                self._log.log(f"Cloudflare: {record_name} already up to date ({target_ip}).", level="INFO")
                # Auto-reset failures on recovery after previous failures.
                if prior_failures > 0:
                    await self._stats.reset_failures(record_name)
                    self._log.log(
                        f"Cloudflare: {record_name} recovered — {prior_failures} failure(s) cleared.",
                        level="INFO",
                    )
                    logger.info("Recovery: %s failure count reset after %d prior failure(s).", record_name, prior_failures)
                return "unchanged"

            # IP mismatch — update the record
            self._log.log(
                f"Cloudflare: IP change detected for {record_name} — {dns_record.content} → {target_ip}. Updating…",
                level="INFO",
            )
            updated = await self._provider.update_record(zone_id, dns_record, target_ip)
            self._log.log(
                f"Cloudflare: updated {updated.name} → {target_ip} ✓",
                level="INFO",
            )
            await self._stats.record_updated(record_name)
            # Auto-reset failures on recovery after previous failures.
            if prior_failures > 0:
                await self._stats.reset_failures(record_name)
                self._log.log(
                    f"Cloudflare: {record_name} recovered — {prior_failures} failure(s) cleared.",
                    level="INFO",
                )
                logger.info("Recovery: %s failure count reset after %d prior failure(s).", record_name, prior_failures)
            return "updated"

        except DnsProviderError as exc:
            self._log.log(f"Cloudflare: failed to update {record_name} — {exc}", level="ERROR")
            await self._stats.record_failed(record_name)
            logger.error("DnsProviderError for %s: %s", record_name, exc)
            return "failed"

    @staticmethod
    def _resolve_zone_id(record_name: str, zones: dict[str, str]) -> str | None:
        """
        Extracts the base domain from an FQDN and looks up its zone ID.

        Args:
            record_name: A fully-qualified DNS name, e.g. "home.example.com".
            zones: Mapping of base domain to provider zone ID.

        Returns:
            The zone ID string if found, or None if no zone is configured for
            the record's base domain.
        """
        ext = tldextract.extract(record_name)
        base_domain = f"{ext.domain}.{ext.suffix}"
        zone_id = zones.get(base_domain)

        if not zone_id:
            logger.warning("No zone ID found for base domain: %s", base_domain)

        return zone_id
