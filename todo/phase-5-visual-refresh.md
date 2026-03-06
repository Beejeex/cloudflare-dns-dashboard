# Phase 5 — Visual Refresh (All Pages)

## Status: ⬜ Not started

## Constraints

- Slate color palette from copilot-instructions.md is unchanged
- No external CSS frameworks (no Tailwind, Bootstrap, or CDN CSS)
- All styles remain in `templates/base.html` `<style>` block
- CSS custom properties added at `:root` level — single source of truth for all color values

---

## Tasks

- [ ] **5.1** CSS custom properties refactor in `templates/base.html`

  Add at the top of `<style>`:
  ```css
  :root {
    --slate-950: #0f172a;
    --slate-800: #1e293b;
    --slate-700: #334155;
    --slate-600: #475569;
    --slate-500: #64748b;
    --slate-400: #94a3b8;
    --slate-300: #cbd5e1;
    --slate-200: #e2e8f0;
    --slate-100: #f1f5f9;
    --slate-50:  #f8fafc;
    --sky-600:   #0284c7;
    --sky-700:   #0369a1;
    --green-600: #16a34a;
    --green-700: #15803d;
    --amber-500: #f59e0b;
    --red-600:   #dc2626;
    --red-700:   #b91c1c;
    --lime-400:  #a3e635;
    --violet-700:#6d28d9;
    --violet-100:#ede9fe;
  }
  ```
  Replace all hardcoded hex values throughout the 756-line style block with `var(--*)` references.
  Organize the `<style>` block with section-separator comments:
  ```css
  /* ---------------------------------------------------------------------------
     Reset & base
     --------------------------------------------------------------------------- */
  /* ---------------------------------------------------------------------------
     Layout
     --------------------------------------------------------------------------- */
  /* ---------------------------------------------------------------------------
     Navigation
     --------------------------------------------------------------------------- */
  /* etc. */
  ```

- [ ] **5.2** Stat cards redesign (`templates/dashboard.html` + `base.html` CSS)

  New `.stat-card` CSS:
  ```css
  .stat-card {
    border-top: 4px solid var(--sky-600);  /* color varies per card type */
    border-radius: 0.5rem;
    padding: 1.25rem 1.5rem;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .stat-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.10);
  }
  .stat-value {
    font-size: 2.25rem;
    font-weight: 800;
    letter-spacing: -0.01em;
    line-height: 1;
  }
  .stat-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--slate-500);
    margin-top: 0.35rem;
  }
  ```

  Card accent border colors:
  - `stat-managed` → `var(--sky-600)`
  - `stat-updates` → `var(--green-600)`
  - `stat-failures` → `var(--red-600)`
  - `stat-last-check` → `var(--amber-500)`

- [ ] **5.3** Expandable managed record rows in `partials/records_table.html`

  Replace flat `<tr>` table rows with `<div class="record-card">` Alpine.js components:

  ```html
  <div class="record-card" x-data="{ expanded: false }">
    <!-- Header: always visible -->
    <div class="record-card-header" @click="expanded = !expanded">
      <span class="record-fqdn">{{ record.name }}</span>
      <span class="badge badge-cf">{{ record.dns_ip }}</span>
      <span class="badge {{ 'badge-ok' if record.is_up_to_date else 'badge-warning' }}">
        {{ 'Up to date' if record.is_up_to_date else 'Needs update' }}
      </span>
      <span class="record-meta">{{ record.last_checked }}</span>
      <!-- Quick actions -->
      <button hx-post="/trigger-record-sync" ...>Sync</button>
      <button hx-post="/delete-record" hx-confirm="Delete?" ...>Delete</button>
      <span class="expand-icon" x-text="expanded ? '▲' : '▼'"></span>
    </div>
    <!-- Body: per-record config form, shown on expand -->
    <div class="record-card-body" x-show="expanded" x-cloak>
      <!-- CF enabled, IP mode, static IP, UniFi options -->
    </div>
  </div>
  ```

  New CSS:
  ```css
  .record-card { border: 1px solid var(--slate-200); border-radius: 0.5rem; margin-bottom: 0.5rem; }
  .record-card-header { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1rem; cursor: pointer; }
  .record-card-header:hover { background: var(--slate-50); }
  .record-card-body { padding: 1rem; border-top: 1px solid var(--slate-200); background: var(--slate-50); }
  [x-cloak] { display: none !important; }
  ```

  This eliminates the "—" flicker after mutations — the header always shows last-known data.

- [ ] **5.4** Discovery grid responsive CSS in `base.html`

  Change:
  ```css
  /* BEFORE */
  .discovery-grid { grid-template-columns: repeat(6, 1fr); }

  /* AFTER */
  .discovery-grid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  ```
  This prevents horizontal scrollbar when the grid is inside the 40%-wide Discovery card.

- [ ] **5.5** Typography pass (all pages, via `base.html`)

  ```css
  body { font-size: 0.9375rem; }           /* 15px — up from implied 14px */
  h2   { font-size: 1rem; font-weight: 700; }
  .section-heading {
    color: var(--slate-600);
    border-left: 3px solid var(--sky-600);
    padding-left: 0.6rem;
  }
  ```

- [ ] **5.6** Log terminal improvements (`templates/logs.html`)

  - Replace `hx-trigger="every {{ refresh }}s"` with SSE `log_appended` swap
  - Add Alpine.js Follow toggle:
    ```html
    <div x-data="{ follow: true }">
      <button class="btn btn-sm btn-secondary"
              @click="follow = !follow"
              x-text="follow ? 'Following ▼' : 'Paused ⏸'">
      </button>
      <pre id="log-content"
           class="log-viewer"
           x-ref="log"
           @htmx:after-swap.window="if(follow) $nextTick(() => $refs.log.scrollTop = $refs.log.scrollHeight)">
      </pre>
    </div>
    ```

- [ ] **5.7** Settings page Alpine.js cleanup (`templates/settings.html`)

  Replace the ~80-line `addZoneRow()` / `removeZoneRow()` vanilla JS `<script>` block with:
  ```html
  <div x-data="{ zones: {{ zones | tojson }}, showToken: false }">
    <template x-for="(zone, i) in zones" :key="i">
      <div class="zone-row">
        <input type="text" :name="'domain_' + i" x-model="zone.domain" placeholder="example.com">
        <input type="text" :name="'zone_id_' + i" x-model="zone.zone_id" placeholder="Zone ID">
        <button type="button" @click="zones.splice(i, 1)">Remove</button>
      </div>
    </template>
    <button type="button" @click="zones.push({ domain: '', zone_id: '' })">+ Add Zone</button>

    <!-- Token show/hide -->
    <input :type="showToken ? 'text' : 'password'" name="api_token" ...>
    <button type="button" @click="showToken = !showToken" x-text="showToken ? 'Hide' : 'Show'"></button>
  </div>
  ```
  No functional changes — same field names, same POST target, same validation.

## Files touched

- `templates/base.html` (CSS vars, typography, record-card styles, discovery grid)
- `templates/dashboard.html` (stat card markup)
- `templates/partials/records_table.html` (expandable card components)
- `templates/logs.html` (follow toggle, SSE swap)
- `templates/settings.html` (Alpine.js zones + token toggle)
