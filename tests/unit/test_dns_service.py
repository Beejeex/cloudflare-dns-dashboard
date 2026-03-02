"""
tests/unit/test_dns_service.py

Unit tests for services/dns_service.py.
Uses respx to mock Cloudflare calls and the in-memory DB session for stats/logs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx
import respx

from cloudflare.dns_provider import DnsRecord
from exceptions import DnsProviderError, IpFetchError
from repositories.stats_repository import StatsRepository
from services.dns_service import DnsService
from services.log_service import LogService
from services.stats_service import StatsService


def _make_dns_service(db_session, dns_provider, ip_service):
    stats_repo = StatsRepository(db_session)
    stats_service = StatsService(stats_repo)
    log_service = LogService(db_session)
    return DnsService(dns_provider, ip_service, stats_service, log_service)


def _mock_record(content="1.2.3.4"):
    return DnsRecord(
        id="rec1",
        name="home.example.com",
        content=content,
        type="A",
        ttl=1,
        proxied=False,
        zone_id="zone123",
    )


# ---------------------------------------------------------------------------
# Happy path — IP already up to date
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_no_update_needed(db_session):
    """When DNS IP matches public IP, no update is performed."""
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "1.2.3.4"

    provider = AsyncMock()
    provider.get_record.return_value = _mock_record(content="1.2.3.4")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    provider.update_record.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path — IP changed, update performed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_updates_when_ip_changed(db_session):
    """When DNS IP differs from public IP, update_record is called once."""
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "9.9.9.9"

    provider = AsyncMock()
    provider.get_record.return_value = _mock_record(content="1.2.3.4")
    provider.update_record.return_value = _mock_record(content="9.9.9.9")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    provider.update_record.assert_called_once()


# ---------------------------------------------------------------------------
# Failure path — IP fetch error aborts cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_aborts_on_ip_fetch_failure(db_session):
    """When IpService raises IpFetchError, the cycle aborts and no DNS call is made."""
    ip_service = AsyncMock()
    ip_service.get_public_ip.side_effect = IpFetchError("timeout")

    provider = AsyncMock()
    service = _make_dns_service(db_session, provider, ip_service)

    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    provider.get_record.assert_not_called()


# ---------------------------------------------------------------------------
# Failure path — DNS provider error increments failure counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_records_failure_on_provider_error(db_session):
    """When the DNS provider raises DnsProviderError, failure stats are incremented."""
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "9.9.9.9"

    provider = AsyncMock()
    provider.get_record.side_effect = DnsProviderError("API down")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    from repositories.stats_repository import StatsRepository
    repo = StatsRepository(db_session)
    stats = repo.get_by_name("home.example.com")
    assert stats is not None
    assert stats.failures == 1


# ---------------------------------------------------------------------------
# No records — skips gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_skips_when_no_records(db_session):
    """When managed_records is empty, no IP fetch or DNS call is made."""
    ip_service = AsyncMock()
    provider = AsyncMock()
    service = _make_dns_service(db_session, provider, ip_service)

    await service.run_check_cycle(managed_records=[], zones={})

    ip_service.get_public_ip.assert_not_called()
    provider.get_record.assert_not_called()


# ---------------------------------------------------------------------------
# Zone resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_skips_record_with_no_zone(db_session):
    """When no zone is configured for a record's base domain, failures counter increments."""
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "1.2.3.4"

    provider = AsyncMock()
    service = _make_dns_service(db_session, provider, ip_service)

    # zones dict is empty — no zone for example.com
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={},
    )

    provider.get_record.assert_not_called()


# ---------------------------------------------------------------------------
# Recovery — failure counter auto-resets after a successful check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_check_cycle_resets_failures_on_unchanged_recovery(db_session):
    """
    When a record has prior failures and the next check is 'unchanged',
    the failure counter must be automatically reset to zero.
    """
    from repositories.stats_repository import StatsRepository

    # Seed an existing failure for the record
    repo = StatsRepository(db_session)
    repo.record_failure("home.example.com")
    repo.record_failure("home.example.com")
    stats_before = repo.get_by_name("home.example.com")
    assert stats_before.failures == 2

    # DNS IP now matches public IP → "unchanged" (recovery)
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "1.2.3.4"
    provider = AsyncMock()
    provider.get_record.return_value = _mock_record(content="1.2.3.4")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    stats_after = repo.get_by_name("home.example.com")
    assert stats_after.failures == 0


@pytest.mark.asyncio
async def test_run_check_cycle_resets_failures_on_updated_recovery(db_session):
    """
    When a record has prior failures and the next check performs an update,
    the failure counter must be automatically reset to zero.
    """
    from repositories.stats_repository import StatsRepository

    # Seed three failures
    repo = StatsRepository(db_session)
    repo.record_failure("home.example.com")
    repo.record_failure("home.example.com")
    repo.record_failure("home.example.com")

    # DNS IP differs from public IP → update performed (recovery)
    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "9.9.9.9"
    provider = AsyncMock()
    provider.get_record.return_value = _mock_record(content="1.2.3.4")
    provider.update_record.return_value = _mock_record(content="9.9.9.9")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    stats_after = repo.get_by_name("home.example.com")
    assert stats_after.failures == 0


@pytest.mark.asyncio
async def test_run_check_cycle_no_reset_when_no_prior_failures(db_session):
    """
    When a record has no prior failures, a successful check leaves the
    failure counter at zero (no spurious reset_failures call needed).
    """
    from repositories.stats_repository import StatsRepository

    ip_service = AsyncMock()
    ip_service.get_public_ip.return_value = "1.2.3.4"
    provider = AsyncMock()
    provider.get_record.return_value = _mock_record(content="1.2.3.4")

    service = _make_dns_service(db_session, provider, ip_service)
    await service.run_check_cycle(
        managed_records=["home.example.com"],
        zones={"example.com": "zone123"},
    )

    repo = StatsRepository(db_session)
    stats = repo.get_by_name("home.example.com")
    # May be None if stats row was never created (no failures, just a check)
    assert stats is None or stats.failures == 0
