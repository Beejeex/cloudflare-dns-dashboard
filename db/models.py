"""
db/models.py

Responsibility: Defines all SQLModel table models used by the application.
Does NOT: contain business logic, repositories, or session management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# AppConfig — single-row application configuration table
# ---------------------------------------------------------------------------


class AppConfig(SQLModel, table=True):
    """
    Stores the application's runtime configuration as a single DB row.

    Fields map directly to the former config.json structure.
    Only one row is expected; it is loaded and saved by ConfigRepository.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # Cloudflare API token
    api_token: str = Field(default="")

    # JSON-encoded dict: {"example.com": "zone_id_abc123"}
    zones_json: str = Field(default="{}")

    # JSON-encoded list of managed record FQDNs: ["home.example.com"]
    records_json: str = Field(default="[]")

    # UI auto-refresh interval in seconds (for HTMX polling)
    refresh: int = Field(default=30)

    # Background DDNS check interval in seconds
    interval: int = Field(default=300)

    # JSON-encoded UI section visibility state
    ui_state_json: str = Field(default='{"settings": true, "all_records": true, "logs": true}')

    # Whether Kubernetes Ingress discovery is enabled (off by default)
    k8s_enabled: bool = Field(default=False)

    # UniFi API key with DNS write access (from local controller → Settings → Admins → API Keys)
    unifi_api_key: str = Field(default="")

    # UniFi site UUID used as the zone_id for DNS policy calls
    unifi_site_id: str = Field(default="")

    # Hostname or IP of the local UniFi Network Application (e.g. 192.168.1.1)
    unifi_host: str = Field(default="")

    # Default internal IPv4 used when creating new UniFi DNS policies from the dashboard
    unifi_default_ip: str = Field(default="")

    # Whether UniFi internal DNS management is enabled (off by default)
    unifi_enabled: bool = Field(default=False)


# ---------------------------------------------------------------------------
# RecordStats — per-DNS-record update/failure counters
# ---------------------------------------------------------------------------


class RecordStats(SQLModel, table=True):
    """
    Tracks update and failure counts for each managed DNS record.

    One row per record FQDN. Updated by StatsRepository after every
    check cycle regardless of whether an update was performed.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # Fully-qualified DNS name, e.g. "home.example.com"
    record_name: str = Field(unique=True, index=True)

    last_checked: Optional[datetime] = Field(default=None)
    last_updated: Optional[datetime] = Field(default=None)

    # Cumulative counters since the record was first tracked
    updates: int = Field(default=0)
    failures: int = Field(default=0)


# ---------------------------------------------------------------------------
# RecordConfig — per-DNS-record behaviour settings
# ---------------------------------------------------------------------------


class RecordConfig(SQLModel, table=True):
    """
    Stores per-record DDNS behaviour settings.

    One optional row per managed FQDN. When no row exists the application
    uses sensible defaults (all integrations disabled; dynamic IP mode).

    Collaborators:
        - RecordConfigRepository: reads and writes these rows
        - DnsService: reads cf_enabled/ip_mode to decide how to update
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # Fully-qualified DNS name this config belongs to
    record_name: str = Field(unique=True, index=True)

    # Whether Cloudflare DDNS updates are active for this record
    # NOTE: default=True because opting INTO Cloudflare DDNS is the expected
    # behaviour when a record is added. An earlier model had default=False
    # which silently disabled updates for all newly created rows.
    cf_enabled: bool = Field(default=True)

    # "dynamic" — always use the current public IP detected by IpService
    # "static"  — always use static_ip, never auto-update
    ip_mode: str = Field(default="dynamic")

    # The fixed external IP to use when ip_mode == "static"
    static_ip: str = Field(default="")

    # Whether this record is also pushed to UniFi DNS policies
    unifi_enabled: bool = Field(default=False)

    # The fixed IP to set in the UniFi DNS policy (defaults to public IP when empty)
    unifi_static_ip: str = Field(default="")

    # Whether an additional "<host>.local" UniFi DNS policy is managed
    unifi_local_enabled: bool = Field(default=False)

    # Optional IP override for the ".local" policy (falls back to unifi_static_ip/default)
    unifi_local_static_ip: str = Field(default="")


# ---------------------------------------------------------------------------
# LogEntry — persistent DDNS log entries shown in the UI log panel
# ---------------------------------------------------------------------------


class LogEntry(SQLModel, table=True):
    """
    Represents a single line in the DDNS activity log.

    Written by LogService; read and filtered by LogService for the UI panel.
    Log level is stored as a plain string (e.g. "INFO", "WARNING", "ERROR").
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = Field(default="INFO")

    # The human-readable log message; may include DNS record names or IPs
    message: str = Field(default="")
