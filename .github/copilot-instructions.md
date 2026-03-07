# Copilot Instructions — Cloudflare DDNS Dashboard

## Project Overview

This is a FastAPI-based Dynamic DNS (DDNS) dashboard for Cloudflare.
It monitors the host machine's public IP address and automatically updates
Cloudflare DNS A-records when the IP changes. A web UI (Jinja2 + HTMX) allows
the user to manage which records are tracked, view logs, and configure API
credentials. Configuration and stats are persisted in a local SQLite database
managed by SQLModel.

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Use 3.12+ features; `match` statements allowed |
| Web framework | FastAPI | Async, Pydantic-native, built-in `Depends()` DI |
| Templates | Jinja2 + HTMX | Jinja2 renders HTML; HTMX drives partial updates |
| HTTP client | httpx | Async-first; replaces `requests` everywhere |
| Scheduler | APScheduler | `AsyncIOScheduler` manages the periodic DDNS check job |
| File watcher | watchdog | Detects out-of-band config changes on the volume |
| Database | SQLite via SQLModel | Stores config, stats, and log entries |
| Container | python:3.12-slim | Single self-contained image; no external services |
| Testing | pytest + pytest-asyncio + respx | Unit and integration tests; no real network calls |

---

## Core Refactoring Goal: SOLID Principles

All code written or modified in this project must follow the **SOLID** principles.
Below is what each principle means in the context of THIS codebase.

---

### S — Single Responsibility Principle
> Every module, class, or function should have **one reason to change**.

- Each file must own exactly **one concern**. Do not mix database access with business logic, or HTTP calls with UI rendering.
- Config DB access lives in `config_repository.py`; IP fetching lives in `ip_service.py`; UI/application config logic lives in `config_service.py`.
- Route handlers must be thin. They receive a request, call a service, and return a rendered template fragment (for HTMX) or a full response. All business logic lives in service classes.
- Log writes and reads are separated: `log_service.py` reads/filters log entries; `log_cleanup.py` handles cleanup scheduling.
- `cloudflare_client.py` contains **all** Cloudflare HTTP calls. Routes must never call `httpx` directly for Cloudflare endpoints.
- The APScheduler job delegates all business logic to `dns_service` — the job function itself only triggers the service.
- `watcher.py` owns the watchdog observer setup; it contains no DNS or config business logic.

**Module structure:**
```
services/
    ip_service.py          # Fetches and caches the current public IP (uses httpx)
    dns_service.py         # Business logic: compare IPs, decide whether to update
    stats_service.py       # Tracks per-record update/failure counters
    config_service.py      # Reads and writes application config (delegates to repo)
    log_service.py         # Reads, filters, and parses log entries for the UI
    broadcast_service.py   # SSE event broadcaster: pushes named events to connected clients

repositories/
    config_repository.py         # SQLModel DB access for AppConfig table (load, save)
    stats_repository.py          # SQLModel DB access for RecordStats table
    record_config_repository.py  # SQLModel DB access for RecordConfig table (per-record settings)

db/
    database.py            # SQLite engine, session factory, create_all + _run_migrations on startup
    models.py              # SQLModel table definitions: AppConfig, RecordStats, LogEntry, RecordConfig

cloudflare/
    dns_provider.py        # Abstract Protocol / ABC: DNSProvider interface
    cloudflare_client.py   # Implements DNSProvider; all Cloudflare REST calls (httpx)
    unifi_client.py        # Implements DNSProvider; UniFi Network API DNS policies (httpx, verify=False)
    kubernetes_client.py   # Kubernetes Ingress discovery (reads hostnames only, no writes)

routes/
    ui_routes.py           # GET handlers that render full pages
    action_routes.py       # POST handlers that return HTMX partial fragments
    api_routes.py          # JSON API endpoints (current IP, logs, UniFi sites)

log_cleanup.py             # Cleanup scheduler: trims old LogEntry rows from SQLite
scheduler.py               # APScheduler setup; registers the DDNS check job + UniFi sync pass
watcher.py                 # watchdog observer; detects out-of-band config file changes
exceptions.py              # All custom exception classes: DnsProviderError, UnifiProviderError, KubernetesError, ConfigLoadError, IpFetchError
dependencies.py            # FastAPI Depends() providers for all services and DB sessions
app.py                     # FastAPI app factory; lifespan wires up DB, scheduler, watcher
```

---

### O — Open/Closed Principle
> Code should be **open for extension, closed for modification**.

- Define an abstract base class (or protocol) `DNSProvider` with methods such as `get_record()`, `update_record()`, `create_record()`, `delete_record()`, and `list_records()`.
- `CloudflareClient` must implement `DNSProvider`. Adding a new DNS provider (e.g., a Kubernetes Ingress DNS writer or a UniFi Network API client for internal DNS records) means creating a new class, not editing existing classes.
- The planned **internal DNS mode** (Kubernetes + UniFi) is **implemented**: `UnifiClient` creates/updates/deletes UniFi DNS policies; `KubernetesClient` discovers Ingress hostnames for the discovery panel.
- `dns_service.py` depends on the `DNSProvider` abstraction, not on `CloudflareClient` directly.
- The scheduler (`scheduler.py`) runs a Cloudflare DDNS cycle **and** a UniFi sync pass each interval. The UniFi pass creates policies for records with `unifi_enabled=True` and deletes them when `unifi_enabled=False`.

---

### L — Liskov Substitution Principle
> Subtypes must be substitutable for their base types **without breaking behavior**.

- Any class implementing `DNSProvider` must honour the full contract: same argument types, consistent return types, no surprising side-effects.
- Service methods must have **consistent return types**. For example, a method that can fail should always return a typed result object (or raise a typed exception) — never sometimes `None` and sometimes a dict.
- Define a `DnsRecord` dataclass to replace raw dicts returned by Cloudflare, so callers always have a stable shape.

---

### I — Interface Segregation Principle
> Clients should not be forced to depend on interfaces they do not use.

- Do not create one large "manager" class with many unrelated methods. Split by usage.
- A route handler that only reads config must not import a function that writes config (and vice versa).
- `dns_service.py` must not know about logging format or UI state.
- `log_service.py` must not know about Cloudflare API calls.
- Keep imports minimal and focused in each file.

---

### D — Dependency Inversion Principle
> High-level modules must not depend on low-level modules. Both should depend on **abstractions**.

- `dns_service.py` (high-level) must receive a `DNSProvider` instance via constructor injection, not import `CloudflareClient` directly.
- The APScheduler job must receive a `dns_service` instance; it must not import `cloudflare_client` directly.
- Route handlers must receive services via **FastAPI's `Depends()` mechanism** declared in `dependencies.py`. No direct module-level instantiation of services inside route bodies.
- `ip_service.py` must accept an optional `httpx.AsyncClient` (or a callable) so it can be unit-tested without real network calls.
- The database session must be injected via `Depends(get_db_session)` — routes and services must never create their own sessions directly.

---

## Code Comment Standards

Every piece of code in this project must be self-documenting. Follow these rules:

### Module-level docstring
Every `.py` file must start with a module docstring that describes:
1. What this module is responsible for (one sentence).
2. What it does NOT do (to enforce SRP boundaries).

```python
"""
ip_service.py

Responsibility: Fetches the current public IP address of the host machine.
Does NOT: parse DNS records, interact with Cloudflare, or read config files.
"""
```

### Class docstrings
All classes must have a docstring explaining their purpose and their main collaborators.

```python
class DnsService:
    """
    Orchestrates the DDNS update cycle for a single DNS record.

    Compares the record's current IP in Cloudflare against the host's
    public IP and triggers an update when they differ.

    Collaborators:
        - DNSProvider: abstract interface for DNS CRUD operations
        - IpService: provides the current public IP
        - StatsService: records update/failure counts
    """
```

### Function/method docstrings
All public functions and methods must have a docstring with:
- A one-line summary.
- `Args:` block (if any parameters).
- `Returns:` block.
- `Raises:` block (if the function can raise).

```python
def update_record_if_needed(self, record_name: str) -> bool:
    """
    Checks a DNS record against the current public IP and updates it if needed.

    Args:
        record_name: The fully-qualified DNS name, e.g. "home.example.com".

    Returns:
        True if an update was performed, False if no change was needed.

    Raises:
        DnsProviderError: If the Cloudflare API call fails.
    """
```

### Inline comments
- Use inline comments to explain **why**, not **what**. The code shows what; the comment shows intent.
- Add a comment above any non-obvious logic, regex, or workaround.
- Use `# NOTE:` for important design decisions, `# TODO:` for known gaps, `# WORKAROUND:` for temporary fixes.

```python
# WORKAROUND: Use atomic rename to prevent config corruption on power loss.
os.replace(temp_path, CONFIG_FILE)
```

### Section separators
Use section comments in longer files to group related logic:

```python
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
```

---

## Logging Setup

- Configure logging **once** in `app.py` using `logging.basicConfig` at module level before the app is created. No other file may call `logging.basicConfig`.
- Use the standard format: `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`.
- All modules obtain their logger via `logger = logging.getLogger(__name__)` — never use `print()` in service or repository code.
- DDNS activity logs (the visible log panel) are stored as `LogEntry` rows in SQLite, written by `dns_service.py` and read by `log_service.py`. There is no separate log file.

---

## Static Files

- Mount the `static/` directory in `app.py` using FastAPI's `StaticFiles`:
  ```python
  from fastapi.staticfiles import StaticFiles
  app.mount("/static", StaticFiles(directory="static"), name="static")
  ```
- HTMX is served from `/static/htmx.min.js`. Reference it in `base.html` as:
  ```html
  <script src="/static/htmx.min.js" defer></script>
  ```
- The HTMX SSE extension is served from `/static/htmx-sse.js`. Load it after htmx.min.js with `hx-ext="sse"` on the `<body>` tag for SSE-connected pages.
- Alpine.js is served from `/static/alpinejs.min.js`. Load it with `defer` after HTMX. Never reference Alpine from a CDN in production.
- Never reference HTMX, Alpine, or any other JS dependency from a CDN in production.

---

## Async

- All service methods and route handlers must be `async def`.
- Use `httpx.AsyncClient` (not `requests`) for all outbound HTTP calls.
- APScheduler async jobs must use `AsyncIOScheduler` from `apscheduler.schedulers.asyncio`.
- Never call blocking I/O (file reads, `time.sleep`) inside an async function. Use `asyncio.sleep` and `aiofiles` where file access is needed.

---

## HTMX Route Pattern

All POST route handlers must return **HTML fragments**, not redirects. The full page is never reloaded; HTMX swaps in the returned fragment.

- A route that mutates state returns a re-rendered partial template of the affected UI section only.
- Use `TemplateResponse` from `fastapi.templating` (Jinja2) to render partials.
- Never return `RedirectResponse` from a POST handler. That is the Flask pattern — do not use it here.
- HTMX attributes (`hx-post`, `hx-target`, `hx-swap`) in templates must match the route paths exactly.

```python
# Example: HTMX-compatible POST handler
@router.post("/add-record", response_class=HTMLResponse)
async def add_record(
    request: Request,
    record_name: str = Form(...),
    config_service: ConfigService = Depends(get_config_service),
) -> HTMLResponse:
    """
    Adds a record to managed list and returns only the records table fragment.
    HTMX swaps this into #records-table on the page without a full reload.
    """
    await config_service.add_managed_record(record_name)
    records = await config_service.list_managed_records()
    return templates.TemplateResponse(
        "partials/records_table.html", {"request": request, "records": records}
    )
```

---

## SQLModel / Database Rules

- All persistent data (config, stats, log entries) lives in SQLite via SQLModel.
- Table models live in `db/models.py`. No table definitions anywhere else.
- Never use raw SQL strings. Use SQLModel `select()` statements.
- DB session is always injected via `Depends(get_db_session)` from `dependencies.py`.
- Repositories receive the session in their constructor — they do not create sessions themselves.
- On startup (`app.py` lifespan), call `SQLModel.metadata.create_all(engine)` once.

---

## Type Hints

All function signatures must use Python type hints (Python 3.12 style).
Use `from __future__ import annotations` at the top of files that reference
forward declarations. Use **SQLModel models** or **Pydantic `BaseModel`** for structured
data passed between layers. Plain `dataclasses` are acceptable for non-DB value objects.
Do not use raw `dict` for structured data — always define a typed model.

---

## Error Handling

- Never use bare `except:` or `except Exception:` without logging the error and providing a typed fallback.
- Raise specific custom exceptions instead of returning `None` on failure. All custom exceptions live in `exceptions.py` — nowhere else:
  - `IpFetchError` — raised by `IpService` when the public IP cannot be fetched.
  - `DnsProviderError` — raised by `CloudflareClient` on Cloudflare API failure.
  - `UnifiProviderError` — raised by `UnifiClient` on UniFi API failure.
  - `KubernetesError` — raised by `KubernetesClient` on Kubernetes API failure.
  - `ConfigLoadError` — raised by `ConfigRepository` when the DB row is missing or corrupt.
- Route handlers must catch service exceptions and return an appropriate HTTP response — they must not let exceptions propagate to FastAPI's default error handler silently.
- Register custom exception handlers in `app.py` using `@app.exception_handler(MyError)` for domain exceptions that cross the HTTP boundary.

---

## Unit Testing

Every service and repository must have unit tests. Route handlers must have integration tests. No test may make a real network call or touch the real database file.

### Tools

| Tool | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-asyncio` | Runs `async def` test functions |
| `respx` | Mocks `httpx.AsyncClient` requests at the transport layer |
| `pytest-cov` | Coverage reporting |
| `fastapi.testclient.TestClient` | Integration tests for route handlers |

### Test folder structure

```
tests/
    conftest.py            # Shared fixtures: in-memory DB session, mock http client
    unit/
        test_ip_service.py
        test_dns_service.py
        test_config_service.py
        test_stats_service.py
        test_log_service.py
        test_cloudflare_client.py
        test_config_repository.py
        test_stats_repository.py
    integration/
        test_ui_routes.py
        test_action_routes.py
        test_api_routes.py
pytest.ini                 # asyncio_mode = auto
```

### Rules

- All test functions that call `async` code must be `async def` and decorated with `@pytest.mark.asyncio` (or set `asyncio_mode = auto` in `pytest.ini`).
- Never instantiate a real `httpx.AsyncClient` in tests — use `respx.mock` or inject a mock transport.
- Never read or write to the real `ddns.db` — always use an in-memory SQLite engine (`"sqlite://"`) passed via fixture.
- Every public service method must have at least one happy-path test and one failure/error-path test.
- Use `pytest.fixture` for shared setup. Never copy-paste setup code between test files.
- Override FastAPI `Depends()` providers in integration tests using `app.dependency_overrides`.
- Run tests inside the project container image, not on the host machine. Host-level `pytest` invocation is not the supported workflow.

### Running tests in Docker (required)

- After any code change, run at least the relevant targeted test scope in Docker before finishing (and run the full suite for release/version bumps).
- A change is not considered complete until containerized tests have been executed and the result is reported.
- Build the image from the repo root:
    ```bash
    docker build -t cloudflare-dns-dashboard:test -f dockerfile .
    ```
- Run the full suite inside the container:
    ```bash
    docker run --rm cloudflare-dns-dashboard:test pytest -q
    ```
- Optional targeted runs inside the container:
    ```bash
    docker run --rm cloudflare-dns-dashboard:test pytest tests/unit -q
    docker run --rm cloudflare-dns-dashboard:test pytest tests/integration -q
    ```

### Key fixtures (define in `conftest.py`)

```python
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool
import respx
import httpx

@pytest.fixture(name="db_session")
def db_session_fixture():
    """
    In-memory SQLite session. Tables are created fresh and dropped after each test.
    Use this everywhere instead of the real engine.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture()
def mock_http():
    """respx router that intercepts all httpx calls. No real network traffic allowed."""
    with respx.mock(assert_all_called=False) as router:
        yield router
```

### Example unit test (IpService)

```python
import pytest
import respx
import httpx
from services.ip_service import IpService

@pytest.mark.asyncio
async def test_get_public_ip_returns_ip(mock_http):
    """
    IpService must return the plain-text IP from the upstream provider.
    """
    mock_http.get("https://api.ipify.org").mock(
        return_value=httpx.Response(200, text="1.2.3.4")
    )
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        ip = await service.get_public_ip()
    assert ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_public_ip_raises_on_failure(mock_http):
    """
    IpService must raise a typed exception when the upstream call fails,
    not silently return None or an empty string.
    """
    mock_http.get("https://api.ipify.org").mock(side_effect=httpx.ConnectError("timeout"))
    async with httpx.AsyncClient() as client:
        service = IpService(http_client=client)
        with pytest.raises(IpFetchError):
            await service.get_public_ip()
```

### Example integration test (route)

```python
from fastapi.testclient import TestClient
from app import app
from dependencies import get_config_service

def test_add_record_returns_fragment(db_session):
    """
    POST /add-record must return an HTML fragment, not a redirect.
    """
    # Override the real config service with one backed by the test DB session
    app.dependency_overrides[get_config_service] = lambda: FakeConfigService(db_session)
    client = TestClient(app)
    response = client.post("/add-record", data={"record_name": "home.example.com"})
    assert response.status_code == 200
    assert "home.example.com" in response.text
    app.dependency_overrides.clear()
```

---

## Docker

The application ships as a **single self-contained container**. There are no external services, no separate database container, no compose file required for basic operation.

### Rules

- **Dev and prod use the same image.** There is no separate dev Dockerfile, no multi-stage build, no docker-compose override for development.
- Base image: `python:3.12-slim`. Do not use Alpine (causes C-extension build issues with SQLModel).
- Start command: `uvicorn app:app --host 0.0.0.0 --port 8080`. Do not use `python app.py`.
- The SQLite database file lives at `/config/ddns.db` inside the container. Mount `/config` as a volume so it survives restarts.
- Log files live at `/config/logs/`. Same volume — no separate log mount needed.
- HTMX must be served from `/static/htmx.min.js` (local file). The container must not require internet access at runtime.
- Include a `HEALTHCHECK` pointing at `GET /health`.
- Pre-create `/config/logs` in the Dockerfile so the container works even without a volume attached.

### Dockerfile template

```dockerfile
FROM python:3.12-slim

# Keep Python output unbuffered so logs appear immediately in docker logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install curl only for the health check probe
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-create the config volume mount point
RUN mkdir -p /config/logs

# Install dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

EXPOSE 8080

# Verify the app responds before marking the container healthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Volume mount (single mount covers everything)

```bash
docker run -d \
  -p 8080:8080 \
  -v /path/to/your/config:/config \
  --name ddns-dashboard \
  ddns-dashboard:latest
```

### Health endpoint

`app.py` must expose:
```python
@app.get("/health")
def health() -> dict:
    """Liveness probe used by Docker HEALTHCHECK."""
    return {"status": "ok"}
```

### Container Registry

The production image is published to **GitHub Container Registry (GHCR)**:

```
ghcr.io/beejeex/cloudflare-dns-dashboard
```

Tag convention:
- `ghcr.io/beejeex/cloudflare-dns-dashboard:v<version>` — immutable release tag (e.g. `v2.0.1`)
- `ghcr.io/beejeex/cloudflare-dns-dashboard:latest` — always points to the most recent release

Build and push workflow:
```bash
docker build -t cloudflare-dns-dashboard:v<version> -t cloudflare-dns-dashboard:latest .
docker tag cloudflare-dns-dashboard:v<version> ghcr.io/beejeex/cloudflare-dns-dashboard:v<version>
docker tag cloudflare-dns-dashboard:latest     ghcr.io/beejeex/cloudflare-dns-dashboard:latest
docker push ghcr.io/beejeex/cloudflare-dns-dashboard:v<version>
docker push ghcr.io/beejeex/cloudflare-dns-dashboard:latest
```

Do **not** push to `docker.io` (Docker Hub). The only registry in use is GHCR.

### Release documentation policy (required)

Before any version increase (`vX.Y.Z`) in code, tags, or image tags, update `README.md` first.

Minimum required README updates per release:
- Container Registry pinned tag example (`ghcr.io/beejeex/cloudflare-dns-dashboard:vX.Y.Z`)
- Project Status current version row
- Any feature/behavior changes introduced in that release

#### Version checklist — every file that must be updated on every release

All four locations must always be updated together. Missing any one will cause a version mismatch.

| File | Location | What to change |
|---|---|---|
| `README.md` | Container Registry code block | Pinned tag line: `ghcr.io/beejeex/cloudflare-dns-dashboard:vX.Y.Z` |
| `README.md` | Project Status table | Add new row; collapse old patch rows; mark previous row as no longer **Current** |
| `shared_templates.py` | `APP_VERSION = "vX.Y.Z"` | String value |
| `app.py` | `FastAPI(version="X.Y.Z", ...)` | Numeric string (no leading `v`) |

Current version: **v2.1.4**

Release order is mandatory:
1. Update `README.md` for the new version and changes.
2. Update `shared_templates.py` — `APP_VERSION`.
3. Update `app.py` — `FastAPI(version=...)`.
4. Commit: `git commit -m "vX.Y.Z — <summary>"`.
5. Create git tag: `git tag vX.Y.Z`.
6. Push commit to remote: `git push`.
7. Push tag to remote: `git push origin vX.Y.Z`.
8. Build/push GHCR images (`vX.Y.Z` and `latest`).

---

## UI Design System

The visual design follows the same system used in `beejeex/madtracked`. Do not deviate from these values.

### Layout
- Dark nav bar + light card body split. Never a fully dark or fully light page.
- Max content width `2160px`, centered, padding `1.75rem 2.5rem`.
- All content lives inside `.card` components (white, `border-radius: 0.5rem`, subtle shadow).

### Color palette (Slate scale)

| Token | Hex | Usage |
|---|---|---|
| `slate-950` | `#0f172a` | Log terminal background |
| `slate-800` | `#1e293b` | Nav background |
| `slate-700` | `#334155` | — |
| `slate-600` | `#475569` | `h2` headings, labels |
| `slate-500` | `#64748b` | Table headers, card titles |
| `slate-400` | `#94a3b8` | Nav links, muted text |
| `slate-300` | `#cbd5e1` | Input borders |
| `slate-200` | `#e2e8f0` | Table borders, card borders |
| `slate-100` | `#f1f5f9` | Page body background |
| `slate-50` | `#f8fafc` | Table row hover |
| `sky-600` | `#0284c7` | Primary button, links |
| `sky-700` | `#0369a1` | Primary button hover |
| `green-600` | `#16a34a` | Success / up-to-date badge |
| `amber-500` | `#f59e0b` | Warning / needs-update badge |
| `red-600` | `#dc2626` | Error / danger button |
| `red-700` | `#b91c1c` | Danger button hover |
| `lime-400` | `#a3e635` | Log terminal text |
| `violet-700` | `#6d28d9` | UniFi badge text |
| `violet-100` | `#ede9fe` | UniFi badge background |
| `green-700` | `#15803d` | K8s badge text |

### Components
- **Badges**: inline-block, `border-radius: 999px`, `font-size: 0.75rem`, `font-weight: 600`. Classes: `.badge-ok`, `.badge-warning`, `.badge-error`, `.badge-unifi` (violet), `.badge-k8s` (green).
- **Unified discovery grid**: `display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem` — one card per hostname across both managed and unmanaged records. Toggle switch on each card (green = managed, gray = unmanaged) posts to `/remove-from-managed` or `/add-to-managed` respectively.
- **Managed record cards**: green border, toggle ON. Per-record expandable config panel includes Cloudflare DDNS checkbox (dynamic/static IP mode) and UniFi DNS checkbox. UniFi section shows IP Address input (prefilled from `unifi_default_ip`) only when UniFi is checked.
- **Stat cards**: CSS grid using `.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; }`. Value in `2rem 700` weight.
- **Tables**: `font-size: 0.875rem`, `th` uppercase + `letter-spacing: 0.05em`, row hover `#f8fafc`.
- **Forms**: `.form-group` spacing, `label` `0.85rem` `#475569`, all inputs full-width with `0.5rem 0.75rem` padding.
- **Log viewer**: `<pre>` with `background:#0f172a; color:#a3e635; white-space:pre-wrap; min-height:200px`.

### No external CSS frameworks
All styles live in `templates/base.html` `<style>` block. Do not add Tailwind, Bootstrap, or any CDN CSS link.

### HTMX patterns in templates
- Log tail: `hx-get="/api/logs/recent" hx-trigger="load, sse:log_appended" hx-swap="innerHTML"` — SSE push replaces timer polling; no `every Ns` trigger.
- Destructive actions: always include `hx-confirm="..."` attribute. The global `htmx:confirm` handler in `base.html` shows a custom modal — guard it with `if (!evt.detail.question) return` because HTMX v1 fires the event for **every** request, not only confirmed ones.
- Dashboard write actions (`/add-to-managed`, `/remove-from-managed`, etc.) use `hx-swap="none"` and rely on a JS `safeReload()` call in `htmx:afterRequest` to refresh the unified grid. Partial fragment swaps are not used here because the grid reorders cards.
- `safeReload()` must close the SSE `EventSource` via `htmx.getInternalData(el).sseEventSource.close()` before calling `location.reload()` to avoid a spurious browser error event.
- Loading indicators: `<span class="htmx-indicator">loading…</span>` with `.htmx-indicator { opacity:0; transition: opacity 0.15s; }` and `.htmx-request.htmx-indicator { opacity:1; }`.
- Partial swaps target the narrowest possible element — never `hx-target="body"` except for full-page resets.

---

## Scheduler — Two-Phase Cycle

Every check cycle performs two sequential passes:

1. **Cloudflare DDNS pass** (`DnsService.run_check_cycle`) — for every managed record with `cfg.cf_enabled=True`, fetches the current public IP (or uses `cfg.static_ip`) and updates the Cloudflare A-record if changed.
2. **UniFi sync pass** (inline in `scheduler.py`) — for every managed record:
   - `cfg.unifi_enabled=True` → create or update the UniFi DNS policy using `cfg.unifi_static_ip` (falls back to `config.unifi_default_ip`).
   - `cfg.unifi_enabled=False` (or no config row) → delete the UniFi policy if one exists.
   - Skipped entirely when `config.unifi_enabled=False` or credentials are absent.

`create_scheduler()` requires both `http_client` (Cloudflare/IP calls) and `unifi_http_client` (verify=False, for UniFi).

---

## Per-Record Settings — RecordConfig

`RecordConfig` (in `db/models.py`) stores per-FQDN overrides. Defaults are applied when no row exists.

| Field | Default | Meaning |
|---|---|---|
| `cf_enabled` | `False` | Include record in Cloudflare DDNS cycle |
| `ip_mode` | `"dynamic"` | `"dynamic"` = auto-detect public IP; `"static"` = use `static_ip` |
| `static_ip` | `""` | Fixed external IP (used when `ip_mode="static"`) |
| `unifi_enabled` | `False` | Manage a UniFi DNS policy for this record |
| `unifi_static_ip` | `""` | IP for the UniFi policy; falls back to `config.unifi_default_ip` |

`RecordConfigRepository.get_all(records)` returns a `dict[str, RecordConfig]` keyed by FQDN.

---

## DB Migrations

`db/database.py` runs `_run_migrations()` after `create_all()` on every startup. New columns must be added there using `ALTER TABLE … ADD COLUMN … DEFAULT …` guarded by a `PRAGMA table_info` existence check. **Never use Alembic** — the single-container architecture does not warrant it.

---


- Do not call `httpx` or `requests` directly inside route handlers or scheduler jobs.
- Do not put database access inside route handlers — use a service or repository.
- Do not import global state (e.g., a module-level `config = load_config()`) — always load fresh via a service injected through `Depends()`.
- Do not mix log writing and log reading in the same class or function.
- Do not silently swallow exceptions with `except Exception: pass`.
- Do not return `RedirectResponse` from POST handlers — use HTMX partial responses.
- Do not define SQLModel table models outside `db/models.py`.
- Do not use `time.sleep()` anywhere in async code — use `asyncio.sleep()`.
- Do not create `httpx.AsyncClient` instances inside service methods — inject them or create once at startup.
- Do not make real network calls in any test — use `respx.mock`.
- Do not use the real `ddns.db` file in tests — always use an in-memory SQLite fixture.
- Do not add a `docker-compose.yml` for basic operation — the app is a single container.
- Do not define custom exceptions inline inside service or route files — all exceptions live in `exceptions.py`.
- Do not reference `data/ddns.db` anywhere — the DB path is always `/config/ddns.db` to match the Docker volume mount.
