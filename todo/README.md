# UI v3 — Feature Branch: `feature/ui-v3`

Target release: `v2.1.0`

## Summary

Full UI overhaul: replace HTMX timer polls with Server-Sent Events (SSE),
side-by-side Managed | Discovered records layout, Alpine.js replacing
~300 lines of hand-written DOM script, full visual refresh on slate palette.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Polling strategy | Server-Sent Events (SSE) | Server-to-client push only; `sse-starlette` single dep; zero unnecessary polls |
| Record layout | Side-by-side cards | User sees both panels simultaneously; no hidden context |
| JS interactivity | Alpine.js from `/static/` | No build step; pairs naturally with HTMX; replaces ~300 lines of vanilla DOM hacks |
| Visual scope | Full visual refresh | Same slate palette; CSS custom properties; expandable record cards; richer stat cards |

## Progress

| Phase | Description | File | Status |
|---|---|---|---|
| 0 | Create branch `feature/ui-v3` | — | ✅ |
| 1 | Dependency additions | `phase-1-dependencies.md` | ✅ |
| 2 | Backend performance fixes | `phase-2-backend-perf.md` | ✅ |
| 3 | SSE broadcast infrastructure | `phase-3-sse.md` | ✅ |
| 4 | Dashboard HTML rewrite | `phase-4-dashboard-html.md` | ⬜ |
| 5 | Visual refresh (all pages) | `phase-5-visual-refresh.md` | ⬜ |
| 6 | Cleanup & correctness | `phase-6-cleanup.md` | ⬜ |
| 7 | Release prep | `phase-7-release.md` | ⬜ |

## Status legend

⬜ Not started | 🔄 In progress | ✅ Done | ❌ Blocked
