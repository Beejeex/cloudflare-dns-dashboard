"""
tests/unit/test_unifi_client.py

Unit tests for UnifiClient.
All HTTP calls are intercepted by respx — no real UniFi API is required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from cloudflare.dns_provider import DnsRecord
from cloudflare.unifi_client import UnifiClient, _UNIFI_PATH
from exceptions import UnifiProviderError


_SITE_ID = "11111111-0000-0000-0000-000000000001"
_POLICY_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_HOST = "192.168.1.1"
_BASE = f"https://{_HOST}{_UNIFI_PATH}"

_POLICY_A = {
    "type": "A_RECORD",
    "id": _POLICY_ID,
    "enabled": True,
    "domain": "home.example.com",
    "ipv4Address": "192.168.1.10",
    "ttlSeconds": 14400,
}

_POLICY_B = {
    "type": "A_RECORD",
    "id": "bbbbbbbb-0000-0000-0000-000000000001",
    "enabled": True,
    "domain": "api.example.com",
    "ipv4Address": "192.168.1.20",
    "ttlSeconds": 14400,
}

_CNAME_POLICY = {
    "type": "CNAME_RECORD",
    "id": "cccccccc-0000-0000-0000-000000000001",
    "enabled": True,
    "domain": "alias.example.com",
    "target": "home.example.com",
}


def _list_response(*policies) -> dict:
    """Build a UniFi list-response envelope."""
    return {"offset": 0, "limit": 200, "count": len(policies), "totalCount": len(policies), "data": list(policies)}


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_is_configured_returns_false_when_no_key():
    client = UnifiClient(http_client=httpx.AsyncClient(), api_key="", host=_HOST)
    assert client.is_configured() is False


def test_is_configured_returns_false_for_whitespace_key():
    client = UnifiClient(http_client=httpx.AsyncClient(), api_key="   ", host=_HOST)
    assert client.is_configured() is False


def test_is_configured_returns_true_when_key_set():
    client = UnifiClient(http_client=httpx.AsyncClient(), api_key="my-secret-key", host=_HOST)
    assert client.is_configured() is True


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_records_returns_a_records_only(mock_http):
    """list_records must filter out non-A_RECORD types."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(200, json=_list_response(_POLICY_A, _POLICY_B, _CNAME_POLICY))
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        records = await client.list_records(_SITE_ID)

    assert len(records) == 2
    names = [r.name for r in records]
    assert "home.example.com" in names
    assert "api.example.com" in names
    assert "alias.example.com" not in names


@pytest.mark.asyncio
async def test_list_records_returns_empty_for_empty_site(mock_http):
    """An empty site returns an empty list without raising."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(200, json=_list_response())
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        records = await client.list_records(_SITE_ID)

    assert records == []


@pytest.mark.asyncio
async def test_list_records_raises_on_http_error(mock_http):
    """A 403 from the UniFi API must raise UnifiProviderError."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="403"):
            await client.list_records(_SITE_ID)


# ---------------------------------------------------------------------------
# get_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_record_returns_matching_policy(mock_http):
    """get_record must return the DnsRecord whose domain matches the requested name."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(200, json=_list_response(_POLICY_A, _POLICY_B))
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        record = await client.get_record(_SITE_ID, "home.example.com")

    assert record is not None
    assert record.name == "home.example.com"
    assert record.content == "192.168.1.10"
    assert record.id == _POLICY_ID


@pytest.mark.asyncio
async def test_get_record_returns_none_when_not_found(mock_http):
    """get_record returns None when no matching domain exists."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(200, json=_list_response(_POLICY_A))
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        record = await client.get_record(_SITE_ID, "unknown.example.com")

    assert record is None


# ---------------------------------------------------------------------------
# create_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_record_posts_correct_payload(mock_http):
    """create_record must POST an A_RECORD body and return the created DnsRecord."""
    created = {**_POLICY_A, "id": "new-id-0001"}
    route = mock_http.post(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(201, json=created)
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        record = await client.create_record(_SITE_ID, "home.example.com", "192.168.1.10")

    assert record.id == "new-id-0001"
    assert record.name == "home.example.com"
    assert record.content == "192.168.1.10"
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "A_RECORD"
    assert body["domain"] == "home.example.com"
    assert body["ipv4Address"] == "192.168.1.10"


@pytest.mark.asyncio
async def test_create_record_raises_on_error(mock_http):
    """A 500 from create raises UnifiProviderError."""
    mock_http.post(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="500"):
            await client.create_record(_SITE_ID, "home.example.com", "192.168.1.10")


# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_record_puts_new_ip(mock_http):
    """update_record must PUT the updated policy and return the updated DnsRecord."""
    updated = {**_POLICY_A, "ipv4Address": "192.168.1.99"}
    existing = DnsRecord(
        id=_POLICY_ID, name="home.example.com", content="192.168.1.10",
        type="A", ttl=14400, proxied=False, zone_id="",
    )
    mock_http.put(f"{_BASE}/sites/{_SITE_ID}/dns/policies/{_POLICY_ID}").mock(
        return_value=httpx.Response(200, json=updated)
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        record = await client.update_record(_SITE_ID, existing, "192.168.1.99")

    assert record.content == "192.168.1.99"


@pytest.mark.asyncio
async def test_update_record_raises_on_http_error(mock_http):
    """update_record raises UnifiProviderError when the controller returns a 500."""
    existing = DnsRecord(
        id=_POLICY_ID, name="home.example.com", content="192.168.1.10",
        type="A", ttl=14400, proxied=False, zone_id="",
    )
    mock_http.put(f"{_BASE}/sites/{_SITE_ID}/dns/policies/{_POLICY_ID}").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="500"):
            await client.update_record(_SITE_ID, existing, "192.168.1.99")


@pytest.mark.asyncio
async def test_update_record_raises_on_network_error(mock_http):
    """update_record raises UnifiProviderError on a network-level ConnectError."""
    existing = DnsRecord(
        id=_POLICY_ID, name="home.example.com", content="192.168.1.10",
        type="A", ttl=14400, proxied=False, zone_id="",
    )
    mock_http.put(f"{_BASE}/sites/{_SITE_ID}/dns/policies/{_POLICY_ID}").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="connection refused"):
            await client.update_record(_SITE_ID, existing, "192.168.1.99")


# ---------------------------------------------------------------------------
# delete_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_record_sends_delete_request(mock_http):
    """delete_record must call DELETE on the policy endpoint without raising."""
    mock_http.delete(f"{_BASE}/sites/{_SITE_ID}/dns/policies/{_POLICY_ID}").mock(
        return_value=httpx.Response(204)
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        await client.delete_record(_SITE_ID, _POLICY_ID)
    # No exception = success


@pytest.mark.asyncio
async def test_delete_record_raises_on_http_error(mock_http):
    """delete_record raises UnifiProviderError when the controller returns a 500."""
    mock_http.delete(f"{_BASE}/sites/{_SITE_ID}/dns/policies/{_POLICY_ID}").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="500"):
            await client.delete_record(_SITE_ID, _POLICY_ID)


@pytest.mark.asyncio
async def test_delete_record_raises_on_not_found(mock_http):
    """delete_record raises UnifiProviderError when the policy does not exist (404)."""
    mock_http.delete(f"{_BASE}/sites/{_SITE_ID}/dns/policies/nonexistent-id").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="404"):
            await client.delete_record(_SITE_ID, "nonexistent-id")


# ---------------------------------------------------------------------------
# Connection error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_error_raises_unifi_provider_error(mock_http):
    """A network-level ConnectError must be wrapped in UnifiProviderError."""
    mock_http.get(f"{_BASE}/sites/{_SITE_ID}/dns/policies").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        with pytest.raises(UnifiProviderError, match="connection refused"):
            await client.list_records(_SITE_ID)


# ---------------------------------------------------------------------------
# list_sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sites_returns_normalised_list(mock_http):
    """list_sites must normalise controller-specific field names to id + name."""
    mock_http.get(f"{_BASE}/sites").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"siteId": "uuid-1", "name": "HQ"},
                    {"id": "uuid-2", "internalReference": "default"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        sites = await client.list_sites()
    assert sites == [
        {"id": "uuid-1", "name": "HQ"},
        {"id": "uuid-2", "name": "default"},
    ]


@pytest.mark.asyncio
async def test_list_sites_returns_empty_for_empty_controller(mock_http):
    """list_sites must return [] when the data array is empty."""
    mock_http.get(f"{_BASE}/sites").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="key", host=_HOST)
        sites = await client.list_sites()
    assert sites == []


@pytest.mark.asyncio
async def test_list_sites_raises_on_http_error(mock_http):
    """list_sites must raise UnifiProviderError when the controller returns 401."""
    mock_http.get(f"{_BASE}/sites").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    async with httpx.AsyncClient() as http_client:
        client = UnifiClient(http_client=http_client, api_key="bad-key", host=_HOST)
        with pytest.raises(UnifiProviderError):
            await client.list_sites()
