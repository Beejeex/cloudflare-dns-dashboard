# Phase 3 — SSE Broadcast Infrastructure

## Status: ✅ Done

## Architecture

```
scheduler / _ddns_check_job
    └─► BroadcastService.publish("records_updated", { html fragment })
    └─► BroadcastService.publish("ip_updated", { "ip": "1.2.3.4" })
    └─► BroadcastService.publish("log_appended", {})

POST /update-config  (after save)
    └─► BroadcastService.publish("records_updated", { html fragment })

GET /api/events  ◄── browser SSE connection (EventSourceResponse)
    subscribe()  →  asyncio.Queue
    on connect:  immediately push ip_updated + records_updated (no blank wait period)
    stream from queue until client disconnects
    finally:     unsubscribe()  →  prevents queue leak
```

## Event types

| Event | Payload | Consumers |
|---|---|---|
| `ip_updated` | `{ "ip": "1.2.3.4" }` | Navbar `#current-ip` swap |
| `records_updated` | rendered HTML fragment | `#records-container` swap |
| `log_appended` | `{}` (signal only) | Log page `#log-content` refetch |
| `sync_complete` | `{ "triggered_by": "scheduler" \| "manual" }` | Optional status bar |

---

## Tasks

- [x] **3.1** Create `services/broadcast_service.py`
  - Class `BroadcastService`
  - Internal `set[asyncio.Queue[str]]` of subscriber queues
  - `subscribe() -> asyncio.Queue` — creates a new queue, adds to set, returns it
  - `unsubscribe(q: asyncio.Queue) -> None` — removes queue from set; no-op if already gone
  - `publish(event_type: str, data: str) -> None` — puts `f"event: {event_type}\ndata: {data}\n\n"` onto all queues
  - Single instance stored on `app.state.broadcaster`
  - Module docstring SRP boundary: manages subscriber queues and event dispatch — does NOT fetch IP, render HTML, or touch the DB

- [x] **3.2** Add `GET /api/events` SSE endpoint to `routes/api_routes.py`
  - Uses `EventSourceResponse` from `sse-starlette`
  - Generator function yielding from the subscriber queue
  - **On connect**: immediately yield current IP (`ip_updated`) and current record HTML (`records_updated`) — zero wait period for fresh clients and SSE reconnects
  - **Stream**: `await asyncio.wait_for(q.get(), timeout=25)` — yields a keep-alive comment on timeout to prevent proxy disconnection
  - **Finally**: `broadcaster.unsubscribe(q)` always runs on disconnect
  - Inject `broadcaster` via `Depends(get_broadcaster)`, `dns_service` via `Depends(get_dns_service)`, `ip_service` via `Depends(get_ip_service)`

- [x] **3.3** Add `get_broadcaster` provider to `dependencies.py`
  ```python
  def get_broadcaster(request: Request) -> BroadcastService:
      return request.app.state.broadcaster
  ```

- [x] **3.4** Wire scheduler to broadcaster in `scheduler.py`
  - `create_scheduler()` gains parameter `broadcaster: BroadcastService`
  - After each `_ddns_check_job` completes: render the records HTML fragment and call `await broadcaster.publish("records_updated", html)`
  - After public IP is fetched: `await broadcaster.publish("ip_updated", json.dumps({"ip": current_ip}))`
  - After any log write: `await broadcaster.publish("log_appended", "{}")`

- [x] **3.5** Wire `POST /update-config` to broadcaster in `routes/action_routes.py`
  - After successful config save: re-render records fragment and call `await broadcaster.publish("records_updated", html)`
  - Reschedule (interval change) still happens before broadcast

- [x] **3.6** Initialize broadcaster and wire into scheduler in `app.py` lifespan
  ```python
  app.state.broadcaster = BroadcastService()
  # ... existing scheduler setup ...
  scheduler = create_scheduler(http_client, unifi_http_client, broadcaster=app.state.broadcaster)
  ```

## Files touched

- `services/broadcast_service.py` (new)
- `routes/api_routes.py`
- `routes/action_routes.py`
- `dependencies.py`
- `scheduler.py`
- `app.py`
