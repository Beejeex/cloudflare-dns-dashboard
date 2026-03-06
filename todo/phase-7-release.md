# Phase 7 — Release Prep (v2.1.0)

## Status: ⬜ Not started

> Per copilot-instructions.md: all four version locations must be updated together.
> README must be updated **before** any version bump in code or tags.

---

## Version checklist

| File | Location | Change |
|---|---|---|
| `README.md` | Container Registry code block | `ghcr.io/beejeex/cloudflare-dns-dashboard:v2.1.0` |
| `README.md` | Project Status table | Add new row; mark v2.0.x row as no longer **Current** |
| `shared_templates.py` | `APP_VERSION` | `"v2.1.0"` |
| `app.py` | `FastAPI(version=...)` | `"2.1.0"` |

---

## Tasks

- [ ] **7.1** Update `README.md`

  - Pinned tag example → `ghcr.io/beejeex/cloudflare-dns-dashboard:v2.1.0`
  - Add row to Project Status table (mark `v2.0.28` as no longer Current)
  - Feature list for v2.1.0:
    - Server-Sent Events replace all HTMX timer polls — zero redundant network calls
    - Side-by-side Managed | Discovered records layout — no more overlap/duplication
    - Alpine.js for local interactivity (tabs, toggles, filters) — served from `/static/`
    - Batch Cloudflare lookups — one API call per zone instead of per managed record
    - In-memory public IP cache (30 s TTL) — eliminates redundant ipify calls
    - Full visual refresh: CSS custom properties, expandable record cards, richer stat cards, log follow toggle
    - Settings page zones management rewritten with Alpine.js
    - Bug fix: `RecordConfig.cf_enabled` defaulted to `False` in DB model — corrected to `True`
    - Bug fix: log display limit inconsistency (200 on load vs 50 on poll) — unified to 100

- [ ] **7.2** Update `shared_templates.py`
  ```python
  APP_VERSION = "v2.1.0"
  ```

- [ ] **7.3** Update `app.py`
  ```python
  FastAPI(version="2.1.0", ...)
  ```

- [ ] **7.4** Run full test suite in Docker — must be green before tagging
  ```bash
  docker build -t cloudflare-dns-dashboard:test -f dockerfile .
  docker run --rm cloudflare-dns-dashboard:test pytest -q
  ```

- [ ] **7.5** Manual smoke tests before tagging
  - Open dashboard in two browser tabs
  - Trigger "Sync Now" → both tabs update without full page reload
  - Disconnect one tab → reconnect → SSE sends current state immediately (no blank period)
  - Add a record from Discovery card → it disappears from Discovery and appears in Managed on both tabs
  - On Logs page: confirm new log entries appear via push, no timer-based flicker
  - On Settings page: add/remove a zone row using Alpine.js controls; verify form submits correctly

- [ ] **7.6** Commit, tag, and push
  ```bash
  git add -A
  git commit -m "v2.1.0 — UI v3: SSE, side-by-side records, Alpine.js, visual refresh"
  git tag v2.1.0
  git push
  git push origin v2.1.0
  ```

- [ ] **7.7** Build and push GHCR images
  ```bash
  docker build -t cloudflare-dns-dashboard:v2.1.0 -t cloudflare-dns-dashboard:latest .
  docker tag cloudflare-dns-dashboard:v2.1.0 ghcr.io/beejeex/cloudflare-dns-dashboard:v2.1.0
  docker tag cloudflare-dns-dashboard:latest ghcr.io/beejeex/cloudflare-dns-dashboard:latest
  docker push ghcr.io/beejeex/cloudflare-dns-dashboard:v2.1.0
  docker push ghcr.io/beejeex/cloudflare-dns-dashboard:latest
  ```

## Files touched

- `README.md`
- `shared_templates.py`
- `app.py`
