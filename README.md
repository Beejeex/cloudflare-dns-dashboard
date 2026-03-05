# Cloudflare DNS Dashboard

> ⚠️ **Beta Software** — This project is in active development. Expect breaking changes between versions. Use in production at your own risk.

A self-hosted Dynamic DNS dashboard. Monitors your host machine's public IP address and automatically updates Cloudflare DNS A-records when it changes. Optionally manages internal DNS policies on a UniFi Network controller and discovers service hostnames from Kubernetes Ingress resources — all from a single Docker container with no external dependencies.

---

## Features

### Cloudflare DDNS
- **Automatic IP tracking** — polls a public IP provider on a configurable interval and updates Cloudflare A-records when the IP changes
- **Multi-zone support** — manage records across multiple Cloudflare zones from one instance
- **Per-record IP mode** — each record can use the auto-detected public IP (`dynamic`) or a fixed IP (`static`) regardless of what other records do
- **Create & manage records** — create new Cloudflare A-records or track existing ones directly from the UI
- **Per-record enable/disable** — exclude individual records from the DDNS cycle without removing them

### UniFi Internal DNS
- **UniFi DNS policy sync** — optionally creates and maintains DNS policies on your local UniFi Network controller for internal hostname resolution
- **Per-record UniFi toggle** — enable or disable the UniFi policy independently for each record
- **Per-record internal IP** — each record has its own internal IP override; falls back to a global default
- **Optional `.local` companion record** — per record, you can also enable `<host>.local` with its own IP override
- **TTL auto** — policies are created with TTL `0` (inherits the site's global setting)
- **Automatic cleanup** — when UniFi is disabled for a record, both primary and optional `.local` policies are deleted on the next cycle

### Kubernetes Discovery
- **Ingress hostname discovery** — reads all Kubernetes Ingress resources across namespaces and shows discovered hostnames in a discovery panel
- **Auto-detect connection** — uses in-cluster service account when running inside a cluster; falls back to `/config/kubeconfig` for out-of-cluster use
- **Read-only** — never modifies cluster state; discovery only

### Dashboard & UI
- **Dark nav / light card layout** — built with FastAPI + Jinja2 + HTMX; no page reloads, no JavaScript framework
- **Stat cards** — live counters: managed records, total updates, total failures, check interval, next-check countdown
- **Live log viewer** — per-update audit log stored in SQLite, auto-refreshes every 5 seconds
- **Status indicators** — per-record badges showing current DNS IP vs. detected IP, UniFi sync status, and K8s discovery status
- **Provider active dots** — header indicators show when UniFi and Kubernetes integrations are live
- **Error banners** — inline alerts for Cloudflare API errors and UniFi API errors with a direct link to Settings

### Infrastructure
- **Single container** — SQLite database, background scheduler, and file watcher all in one `python:3.12-slim` image
- **Health endpoint** — `GET /health` for Docker `HEALTHCHECK` and uptime monitors
- **Automatic DB migrations** — new schema columns are applied on startup; no migration tool required
- **Settings UI** — configure all credentials and options via web form; no JSON editing, no environment variables

---

## Requirements

- Docker (any recent version)
- A Cloudflare account with an API token scoped to `Zone:DNS:Edit`

**Optional integrations:**
- UniFi Network Application (self-hosted) with an API key
- Kubernetes cluster accessible from the container (in-cluster or via kubeconfig)

---

## Quick Start

```bash
docker run -d \
  --name ddns-dashboard \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /path/to/your/config:/config \
  ghcr.io/beejeex/cloudflare-dns-dashboard:latest
```

Open `http://localhost:8080` and go to **Settings** to enter your Cloudflare API token and zone IDs.

### Build locally

```bash
git clone https://github.com/Beejeex/cloudflare-dns-dashboard.git
cd cloudflare-dns-dashboard
docker build -t ddns-dashboard .
docker run -d \
  --name ddns-dashboard \
  --restart unless-stopped \
  -p 8080:8080 \
  -v "$PWD/config:/config" \
  ddns-dashboard
```

---

## Configuration

All configuration is stored in `/config/ddns.db` (SQLite) inside the container. Mount `/config` as a volume so settings and logs survive restarts. Everything is managed through the Settings page — no environment variables or config files required.

### Cloudflare

| Setting | Description |
|---|---|
| **API Token** | Cloudflare API token with `Zone:DNS:Edit` permission |
| **Zones** | One or more domain → Zone ID pairs (e.g. `example.com` → `abc123...`) |
| **Check Interval** | How often (seconds) to check for an IP change (default: `300`) |
| **Log Retention** | How many days to keep log entries (default: `30`) |

### UniFi Internal DNS *(optional)*

| Setting | Description |
|---|---|
| **UniFi Host** | Hostname or IP of the UniFi Network Application (e.g. `192.168.1.1`) |
| **API Key** | UniFi API key with DNS write access (Settings → Admins → API Keys) |
| **Site ID** | UniFi site UUID used as the DNS policy zone |
| **Default Internal IP** | Fallback IP used when a record has no per-record IP set |
| **Enable UniFi** | Master toggle — disables all UniFi sync when off |

### Kubernetes Discovery *(optional)*

| Setting | Description |
|---|---|
| **Enable Kubernetes** | Master toggle for Ingress discovery |
| **kubeconfig** | Place a kubeconfig file at `/config/kubeconfig` for out-of-cluster access |

#### In-cluster (recommended)

Run the container as a Kubernetes Deployment. The app auto-detects the pod's service account token at `/var/run/secrets/kubernetes.io/serviceaccount`. Create a read-only `ClusterRole` that can list Ingress resources:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dns-dashboard
  namespace: dns-dashboard
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dns-dashboard-ingress-reader
rules:
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dns-dashboard-ingress-reader
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: dns-dashboard-ingress-reader
subjects:
  - kind: ServiceAccount
    name: dns-dashboard
    namespace: dns-dashboard
```

Set `serviceAccountName: dns-dashboard` in your Deployment spec.

#### Out-of-cluster (homelab / bare metal)

Export a kubeconfig for a service account that has the `ClusterRole` above bound to it, then place the file at `/config/kubeconfig` (inside the container's config volume). The app checks for this file automatically when no in-cluster token is found.

---

## Per-Record Settings

Each managed record has independent controls accessible from the dashboard:

| Setting | Default | Description |
|---|---|---|
| **Cloudflare DDNS** | `on` | Include this record in the DDNS update cycle |
| **IP Mode** | `dynamic` | `dynamic` = auto-detect public IP; `static` = use a fixed IP |
| **Static IP** | — | Fixed external IP (used when IP mode is `static`) |
| **UniFi DNS** | `off` | Create and maintain a UniFi DNS policy for this record |
| **Internal IP** | — | IP for the UniFi policy; falls back to the global default |
| **Create `.local` record** | `off` | Also manage a `<host>.local` UniFi policy for this record |
| **`.local` IP** | — | Optional IP override for `<host>.local`; falls back to Internal IP, then global default |

---

## Scheduler Cycle

Every interval the scheduler runs two sequential passes:

1. **Cloudflare DDNS pass** — for each record with Cloudflare enabled, fetches the current public IP (or uses the configured static IP) and updates the A-record if the IP has changed.
2. **UniFi sync pass** — for each record:
   - UniFi enabled → create the DNS policy if it doesn't exist, or update it if the IP has changed
  - Optional `.local` enabled → create/update `<host>.local` using its IP override/fallback chain
   - UniFi disabled → delete the DNS policy from the controller if one exists
  - Optional `.local` disabled → delete the `<host>.local` policy if one exists
   - Skipped entirely when the global UniFi toggle is off or credentials are absent

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI |
| Templates | Jinja2 + HTMX |
| HTTP client | httpx (async) |
| Scheduler | APScheduler (`AsyncIOScheduler`) |
| Database | SQLite via SQLModel |
| Container | python:3.12-slim |

---

## Container Registry

Images are published to GitHub Container Registry:

```
ghcr.io/beejeex/cloudflare-dns-dashboard:latest      # most recent release
ghcr.io/beejeex/cloudflare-dns-dashboard:v2.0.27     # pinned release
```

---

## Project Status

| Version | Status |
|---|---|
| `v1.x` | Legacy Flask app — archived |
| `v2.0.13` | FastAPI rewrite with UniFi + Kubernetes integration |
| `v2.0.14` | Managed records redesigned as full-width rows with expandable config panel |
| `v2.0.15` | Fix UniFi .local policy name to preserve full subdomain structure |
| `v2.0.16` | Fixed badge column alignment; added per-record clear-failures button |
| `v2.0.17` | Dashboard live-refresh every 30s (records table + stat cards) without full page reload |
| `v2.0.18` | UniFi-only records now stamp last_checked timestamp on each sync cycle |
| `v2.0.19` | Auto-reset failure counter on recovery; removed Total Updates/Failures stat cards |
| `v2.0.20` | Hide Cloudflare IP column for records that have Cloudflare disabled |
| `v2.0.21` | Reset button moved to updates counter; failures show count-only (auto-reset on recovery) |
| `v2.0.22` | Remove private domain name from public source code |
| `v2.0.23` | New managed records default all checkboxes off |
| `v2.0.24` | UniFi .local discovery merge fix; delete buttons on unmanaged cards; 3-checkbox UniFi panel |
| `v2.0.25` | Sync Now button; zone-based FQDN reconstruction for orphaned .local policies |
| `v2.0.26` | Color-coded rip-labels per service; K8s Ingress shown in managed record rows |
| `v2.0.27` | **Current** — Discovery search + filter bar; multi-service badges; fix create-record error handling |

This is **beta software**. The database schema may change between minor versions. Pin to a specific image tag in production.

Known limitations:
- No authentication on the web UI — do not expose port 8080 to the public internet without a reverse proxy + auth layer
- No HTTPS built-in — terminate TLS at your reverse proxy (nginx, Caddy, Traefik)
- Single-instance only — no HA or clustering support

---

## License

CC BY-NC-SA 4.0 — Free for personal/non-commercial use; modifications must be shared under the same license. See [LICENSE](LICENSE) file.
