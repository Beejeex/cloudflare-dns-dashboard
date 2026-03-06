# Phase 6 — Cleanup & Correctness

## Status: ✅ Complete — commit `6bad275`

---

## Tasks

- [ ] **6.1** Resolve orphaned `templates/partials/k8s_records_table.html`

  The partial is fully implemented but not referenced by any template or HTMX endpoint.

  **Recommended action:** Wire it up properly
  - Add `GET /api/k8s-records` endpoint to `routes/api_routes.py` returning this partial
  - Add a "Refresh K8s" button in the Discovery card:
    ```html
    <button hx-get="/api/k8s-records"
            hx-target="#k8s-records-container"
            hx-swap="innerHTML"
            hx-indicator="#k8s-spinner">
      Refresh K8s
    </button>
    <span id="k8s-spinner" class="htmx-indicator">loading…</span>
    <div id="k8s-records-container"></div>
    ```
  - **Alternative:** Delete the file if K8s discovery is sufficiently covered by the Discovery card grid

- [ ] **6.2** Remove dead `partials/status_bar.html` and `GET /api/status` endpoint

  Both exist but no template references them. The SSE `sync_complete` event makes a polled status bar redundant.
  - Delete `templates/partials/status_bar.html`
  - Remove the `GET /api/status` handler from `routes/api_routes.py`
  - Confirm no template has an `hx-get="/api/status"` reference before deleting

- [ ] **6.3** Unify log display limit to 100 everywhere

  Current inconsistency: `GET /logs` loads 200 entries; `GET /api/logs/recent` polls for 50 entries.
  Every poll truncates the visible log to the last 50, discarding the initial 200.

  Changes:
  - `services/log_service.py` — change `get_recent()` default arg: `limit: int = 100`
  - `routes/ui_routes.py` — `GET /logs` handler: pass `limit=100`
  - `routes/api_routes.py` — `GET /api/logs/recent` handler: pass `limit=100`

- [ ] **6.4** Resolve `aiofiles` in `requirements.txt`

  `aiofiles` is listed as a dependency but not imported or used anywhere in the codebase.
  - Either **remove** it from `requirements.txt` (preferred — keep deps minimal)
  - Or **add usage**: async config backup written to `/config/ddns_backup.json` on each `save_config()` call

- [ ] **6.5** Add and update tests

  - [ ] `tests/unit/test_broadcast_service.py` **(new)**
    - `subscribe()` returns a queue; queue is in the internal set
    - `publish()` puts the event string onto all subscriber queues
    - `unsubscribe()` removes the queue; subsequent publish does not reach it
    - Concurrent subscribers all receive the event

  - [ ] `tests/unit/test_dns_service.py`
    - Add happy-path test for `fetch_zone_record_map()`:
      verifies exactly **one** `list_records(zone_id)` call per zone, not per record
    - Add test: record not found in zone → value is `None` in returned dict

  - [ ] `tests/unit/test_stats_repository.py`
    - Add happy-path test for `get_bulk()`: returns `dict[str, RecordStats]` keyed by name
    - Add test: unknown name is absent from result dict (not a `KeyError`)

  - [ ] `tests/unit/test_ip_service.py`
    - Add test: result within TTL is returned from cache; ipify is **not** called again
    - Add test: expired cache triggers a fresh ipify call and updates the cache

  - [ ] `tests/integration/test_api_routes.py`
    - Add test for `GET /api/events`:
      connect, assert first event received is `ip_updated` or `records_updated`, disconnect cleanly
    - Verify no queue leak after disconnect (`broadcaster._queues` is empty)

  - [ ] Review and update existing tests that may assert the presence of `hx-trigger="every 30s"` in rendered HTML (those attributes are now removed)

## Files touched

- `templates/partials/k8s_records_table.html` (wire or delete)
- `templates/partials/status_bar.html` (delete)
- `routes/api_routes.py`
- `services/log_service.py`
- `routes/ui_routes.py`
- `requirements.txt`
- `tests/unit/test_broadcast_service.py` (new)
- `tests/unit/test_dns_service.py`
- `tests/unit/test_stats_repository.py`
- `tests/unit/test_ip_service.py`
- `tests/integration/test_api_routes.py`
