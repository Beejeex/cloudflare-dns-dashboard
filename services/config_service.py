"""
services/config_service.py

Responsibility: Provides a clean, business-level API for reading and writing
application configuration. Delegates all persistence to ConfigRepository.
Does NOT: make HTTP calls, manage DNS records, or interact with the scheduler.
"""

from __future__ import annotations

import logging

from db.models import AppConfig
from repositories.config_repository import ConfigRepository

logger = logging.getLogger(__name__)


class ConfigService:
    """
    High-level API for reading and writing application configuration.

    Abstracts the JSON encoding/decoding details of ConfigRepository and
    provides intent-named methods such as add_managed_record() that route
    handlers can call directly.

    Collaborators:
        - ConfigRepository: handles all database access
    """

    def __init__(self, config_repo: ConfigRepository) -> None:
        """
        Initialises the service with a config repository.

        Args:
            config_repo: An initialised ConfigRepository for the current session.
        """
        self._repo = config_repo
        # Request-scoped cache — populated on first read; invalidated on any write
        self._config: AppConfig | None = None

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _load(self) -> AppConfig:
        """
        Returns the cached AppConfig for this request, loading from DB if needed.

        All read methods call this instead of self._repo.load() directly so that
        the ConfigRepository is only hit once per request regardless of how many
        service methods the handler calls.

        Returns:
            The AppConfig ORM instance.
        """
        if self._config is None:
            self._config = self._repo.load()
        return self._config

    # ---------------------------------------------------------------------------
    # Read operations
    # ---------------------------------------------------------------------------

    async def get_config(self) -> AppConfig:
        """
        Returns the current AppConfig row (creating defaults if absent).

        Returns:
            The AppConfig ORM instance.
        """
        return self._load()

    async def get_api_token(self) -> str:
        """
        Returns the stored Cloudflare API token.

        Returns:
            The API token string, or an empty string if not configured.
        """
        return self._load().api_token

    async def get_zones(self) -> dict[str, str]:
        """
        Returns the configured DNS zones mapping.

        Returns:
            A dict mapping base domain strings to Cloudflare zone IDs,
            e.g. {"example.com": "zone_id_abc123"}.
        """
        return self._repo.get_zones(self._load())

    async def get_managed_records(self) -> list[str]:
        """
        Returns the list of DNS record FQDNs currently being managed.

        Returns:
            A list of fully-qualified DNS names, e.g. ["home.example.com"].
        """
        return self._repo.get_records(self._load())

    async def get_refresh_interval(self) -> int:
        """
        Returns the UI auto-refresh interval in seconds.

        Returns:
            The interval as an integer number of seconds.
        """
        return self._load().refresh

    async def get_check_interval(self) -> int:
        """
        Returns the background DDNS check interval in seconds.

        Returns:
            The interval as an integer number of seconds.
        """
        return self._load().interval

    async def get_k8s_enabled(self) -> bool:
        """
        Returns whether Kubernetes Ingress discovery is enabled.

        Returns:
            True if the feature is enabled, False otherwise.
        """
        return self._load().k8s_enabled

    async def get_unifi_config(self) -> tuple[str, str, str, str, bool]:
        """
        Returns the UniFi integration configuration.

        Returns:
            A tuple of (host, api_key, site_id, default_ip, enabled).
        """
        config = self._load()
        return (
            config.unifi_host,
            config.unifi_api_key,
            config.unifi_site_id,
            config.unifi_default_ip,
            config.unifi_enabled,
        )

    async def get_ui_state(self) -> dict[str, bool]:
        """
        Returns the UI section visibility state.

        Returns:
            A dict of section-name to boolean visibility flags.
        """
        return self._repo.get_ui_state(self._load())

    # ---------------------------------------------------------------------------
    # Write operations
    # ---------------------------------------------------------------------------

    async def update_credentials(
        self,
        api_token: str,
        zones: dict[str, str],
        refresh: int,
        interval: int,
        k8s_enabled: bool = False,
        unifi_host: str = "",
        unifi_api_key: str = "",
        unifi_site_id: str = "",
        unifi_default_ip: str = "",
        unifi_enabled: bool = False,
    ) -> AppConfig:
        """
        Saves new Cloudflare credentials, timing configuration, Kubernetes
        and UniFi settings.

        Args:
            api_token: The Cloudflare API token with DNS edit permissions.
            zones: A dict mapping base domain strings to Cloudflare zone IDs.
            refresh: UI auto-refresh interval in seconds.
            interval: Background DDNS check interval in seconds.
            k8s_enabled: Whether Kubernetes Ingress discovery is enabled.
            unifi_host: Hostname or IP of the local UniFi Network Application.
            unifi_api_key: UniFi API key with DNS write access.
            unifi_site_id: UniFi site UUID used as the DNS policy zone.
            unifi_default_ip: Default internal IP used when creating new UniFi DNS policies.
            unifi_enabled: Whether UniFi internal DNS management is enabled.

        Returns:
            The saved AppConfig instance.
        """
        config = self._repo.load()
        config.api_token = api_token
        self._repo.set_zones(config, zones)
        config.refresh = refresh
        config.interval = interval
        config.k8s_enabled = k8s_enabled
        config.unifi_host = unifi_host
        config.unifi_api_key = unifi_api_key
        config.unifi_site_id = unifi_site_id
        config.unifi_default_ip = unifi_default_ip
        config.unifi_enabled = unifi_enabled
        self._repo.save(config)
        # Invalidate cache so subsequent reads within this request see the new values
        self._config = None
        logger.info("Credentials and intervals updated.")
        return config

    async def add_managed_record(self, record_name: str) -> bool:
        """
        Adds a DNS record FQDN to the managed list if not already present.

        Args:
            record_name: The fully-qualified DNS name to add.

        Returns:
            True if the record was added, False if it was already in the list.
        """
        config = self._repo.load()
        records = self._repo.get_records(config)

        if record_name in records:
            return False

        records.append(record_name)
        self._repo.set_records(config, records)
        self._repo.save(config)
        self._config = None
        logger.info("Added managed record: %s", record_name)
        return True

    async def remove_managed_record(self, record_name: str) -> bool:
        """
        Removes a DNS record FQDN from the managed list.

        Args:
            record_name: The fully-qualified DNS name to remove.

        Returns:
            True if the record was removed, False if it was not in the list.
        """
        config = self._repo.load()
        records = self._repo.get_records(config)

        if record_name not in records:
            return False

        records.remove(record_name)
        self._repo.set_records(config, records)
        self._repo.save(config)
        self._config = None
        logger.info("Removed managed record: %s", record_name)
        return True

    async def set_ui_state(self, ui_state: dict[str, bool]) -> None:
        """
        Persists the UI section visibility state.

        Args:
            ui_state: A dict mapping section names to visibility booleans.

        Returns:
            None
        """
        config = self._repo.load()
        self._repo.set_ui_state(config, ui_state)
        self._repo.save(config)
        self._config = None
