# Phase 4 — Dashboard HTML Rewrite

## Status: ✅ Complete

- [x] **4.1** Rewrite `templates/dashboard.html` — 60/40 grid, SSE `records_container`, Alpine.js discovery filter, removed `filterDiscovery()`/`setDiscoveryFilter()` JS
- [x] **4.2** Update navbar IP in `templates/base.html` — SSE swap on dashboard, one-shot load on other pages; body tag SSE ext wired conditionally
- [x] **4.3** Update `routes/ui_routes.py` `dashboard()` — `fetch_zone_record_map()` + `get_bulk()` replace N per-record calls; `stats_service` → `stats_repo`
- [x] **4.4** Update `routes/api_routes.py` `GET /api/records` — same bulk approach; `stats_service` → `stats_repo` in signature

Commit: `6c8da2d`

```
BEFORE
──────────────────────────────────────────────────────
  [ Stat cards — full width row ]
  [ Managed records table — full width             ]
  [ Discovery grid — full width below              ]

AFTER
──────────────────────────────────────────────────────
  [ Stat cards — full width row                    ]
  [  Managed records card  │  Discovery card       ]
  [       ~60% width       │     ~40% width        ]
  [  Live via SSE push     │  Server-side on load  ]
```

Records already in **Managed** are excluded from the Discovery card — no duplicates.

---

## Tasks

- [ ] **4.1** Rewrite `templates/dashboard.html`

  **Remove:**
  - All `hx-trigger="every 30s"` attributes
  - Hand-written `filterDiscovery()` / `setDiscoveryFilter()` `<script>` block (replaced by Alpine.js)

  **Add SSE connection:**
  ```html
  <div hx-ext="sse" sse-connect="/api/events">
  ```

  **Managed records card (left ~60%):**
  ```html
  <div id="records-container"
       hx-ext="sse"
       sse-swap="records_updated"
       hx-swap="innerHTML">
    {# rendered server-side on first paint #}
  </div>
  ```

  **Discovery card (right ~40%):**
  - Rendered server-side on page load only (K8s is expensive — not polled)
  - Server-side: pass only records where `name not in managed_names` to Jinja context
  - Alpine.js `x-data` replaces all hand-written JS:
    ```html
    <div x-data="{
      search: '',
      filter: 'all',
      get visible() {
        return this.filter === 'all' || card.dataset.sources.includes(this.filter)
      }
    }">
    ```
  - Text search input: `x-model="search"` — filters card `data-name` attribute
  - Source filter buttons: `@click="filter = 'cf'"` / `'unifi'` / `'k8s'` / `'all'`
  - "Add" confirmation tooltip: Alpine.js `x-show="confirming"` replaces manual show/hide

  **Stat cards:**
  - Values still updated via SSE `records_updated` OOB swap (same `hx-swap-oob="true"` pattern — triggered by SSE instead of timer)
  - New richer card design (see Phase 5.2)

- [ ] **4.2** Update navbar IP display in `templates/base.html`

  **Remove:**
  ```html
  hx-trigger="load, every 30s"
  ```

  **Dashboard pages** (has SSE connection): use SSE swap
  ```html
  <strong id="current-ip"
          sse-swap="ip_updated"
          hx-swap="innerHTML">—</strong>
  ```

  **Logs / Settings pages** (no SSE): one-shot fetch on load only
  ```html
  <strong id="current-ip"
          hx-get="/api/current-ip"
          hx-trigger="load"
          hx-swap="innerHTML">—</strong>
  ```
  Use Jinja `{% if request.url.path == '/' %}` to conditionally render the correct variant.

- [ ] **4.3** Update `routes/ui_routes.py` `dashboard()` handler
  - Replace per-record `check_single_record()` calls → `dns_service.fetch_zone_record_map()` (Phase 2.2)
  - Replace per-record `stats_service.get_for_record()` calls → `stats_repo.get_bulk()` (Phase 2.3)
  - Config loaded once via `config_service` (Phase 2.4 eliminates redundant loads)
  - Pass `managed_names: set[str]` to template context so Jinja excludes managed records from Discovery card

- [ ] **4.4** Update `routes/api_routes.py` `GET /api/records`
  - Existing endpoint kept as SSE-on-connect fallback (first paint for reconnecting clients)
  - Switch from per-record lookups → `fetch_zone_record_map()` + `get_bulk()` (same as 4.3)

## Files touched

- `templates/dashboard.html`
- `templates/base.html`
- `templates/partials/records_table.html` (verify OOB stat spans work with SSE trigger)
- `routes/ui_routes.py`
- `routes/api_routes.py`
