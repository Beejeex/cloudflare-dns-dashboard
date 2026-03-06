# Phase 1 — Dependency Additions

## Status: ✅ Done

## Tasks

- [x] **1.1** Create branch `feature/ui-v3` from current HEAD and push it
  ```bash
  git checkout -b feature/ui-v3
  git push -u origin feature/ui-v3
  ```

- [x] **1.2** Add `sse-starlette>=1.8` to `requirements.txt`

- [x] **1.3** Download Alpine.js v3 minified build into `static/alpinejs.min.js`
  - Source: `https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js`
  - Must be served locally — no CDN link in production templates

- [x] **1.4** Download HTMX SSE extension into `static/htmx-sse.js`
  - Source: `https://unpkg.com/htmx-ext-sse/sse.js`

- [x] **1.5** Add both script tags to `templates/base.html` after the existing HTMX script tag
  ```html
  <script src="/static/alpinejs.min.js" defer></script>
  <script src="/static/htmx-sse.js" defer></script>
  ```

## Files touched

- `requirements.txt`
- `static/alpinejs.min.js` (new)
- `static/htmx-sse.js` (new)
- `templates/base.html`
