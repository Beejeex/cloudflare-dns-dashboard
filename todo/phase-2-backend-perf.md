# Phase 2 — Backend Performance Fixes

## Status: ✅ Done

## Root causes identified

| # | Problem | Impact |
|---|---|---|
| 1 | `IpService.get_public_ip()` called independently from 3 concurrent timers — no cache | 2–3 redundant ipify calls per 30 s |
| 2 | One `GET /zones/{zone_id}/dns_records` call **per managed record** per poll | N Cloudflare API calls per 30 s (N+1) |
| 3 | `config_repo.load()` called 5–6 times per request for the same `AppConfig` row | Redundant SQLite round-trips |
| 4 | `stats_service.get_for_record()` called per record — N individual `SELECT` statements | N DB queries where 1 bulk query would suffice |
| 5 | `RecordConfig.cf_enabled` model default is `False`; app-layer default is `True` | Rows created by `update-record-config` silently disable Cloudflare DDNS |

---

## Tasks

- [x] **2.1** Add in-memory IP cache to `services/ip_service.py`
  - Store `(ip: str, fetched_at: float)` on `app.state.ip_cache`
  - TTL: 30 seconds
  - `get_public_ip()` returns cached result within TTL; otherwise fetches fresh and updates cache
  - Cache lives on `app.state` (shared across all requests in the process)
  - Update `dependencies.py`: pass `request.app.state` into `IpService` constructor
  - Update `app.py` lifespan: `app.state.ip_cache = {"ip": None, "fetched_at": 0.0}`

- [x] **2.2** Add `DnsService.fetch_zone_record_map()` to `services/dns_service.py`
  - Signature: `async def fetch_zone_record_map(self, managed_records: list[str], zones: dict[str, str]) -> dict[str, DnsRecord | None]`
  - Calls `dns_provider.list_records(zone_id)` **once per zone** (not once per record)
  - Builds and returns `{fqdn: DnsRecord | None}` lookup dict from the zone batch result
  - Dashboard full-page render (`GET /`) and records poll (`GET /api/records`) both switch to this
  - Old `check_single_record()` is kept — the scheduler still uses it for targeted per-record updates

- [x] **2.3** Add `StatsRepository.get_bulk()` to `repositories/stats_repository.py`
  - Signature: `def get_bulk(self, names: list[str]) -> dict[str, RecordStats]`
  - Uses `select(RecordStats).where(RecordStats.record_name.in_(names))` — single query
  - All dashboard and records-poll renderers switch to this
  - Per-record `get_for_record()` is kept — scheduler write path still uses it

- [x] **2.4** Add request-scoped config cache to `services/config_service.py`
  - Add `_config: AppConfig | None = None` instance attribute
  - Extract private `_load() -> AppConfig` that returns `self._config` if set, otherwise calls `self._repo.load()` and caches
  - All `get_*` helper methods call `self._load()` instead of `self._repo.load()` directly
  - Invalidate: `self._config = None` inside any `save_config()` / mutating method so next read is fresh

- [x] **2.5** Fix `RecordConfig.cf_enabled` default mismatch
  - `db/models.py`: change `cf_enabled: bool = Field(default=False, ...)` → `default=True`
  - `db/database.py` `_run_migrations()`: add guard to correct existing rows that were set to `False` by the old default
    ```python
    # WORKAROUND: cf_enabled was incorrectly defaulted to False in earlier versions.
    # Correct any existing rows that have cf_enabled=0 and no intentional user override.
    cursor.execute("UPDATE record_config SET cf_enabled = 1 WHERE cf_enabled = 0")
    ```
    Wrap in the same `PRAGMA table_info` / column-existence guard pattern used by existing migrations.

## Files touched

- `services/ip_service.py`
- `services/dns_service.py`
- `services/config_service.py`
- `repositories/stats_repository.py`
- `db/models.py`
- `db/database.py`
- `dependencies.py`
- `app.py`
