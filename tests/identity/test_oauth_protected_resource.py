"""Tests for the RFC 9728 protected-resource metadata endpoint.

This endpoint is the bootstrap entry point for the MCP host's OAuth dance:
hosts fetch it without credentials, parse out ``authorization_servers``, and
then run discovery against the AS. Tests cover the configured-AS happy path,
the no-AS misconfiguration case, and the auth-exempt status.
"""

import httpx
import pytest

from opik_mcp.config import get_settings

PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Settings is ``lru_cache(maxsize=1)``-d. Clear it between tests that
    mutate env so each test sees the values it set, not the previous test's.
    """
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_protected_resource_metadata_unconfigured_returns_503(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPIK_MCP_AS_URL", raising=False)
    get_settings.cache_clear()
    r = await http_client.get(PROTECTED_RESOURCE_METADATA_PATH)
    assert r.status_code == 503
    assert r.json() == {"error": "OPIK_MCP_AS_URL not configured"}


@pytest.mark.anyio
async def test_protected_resource_metadata_configured(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://www.comet.com")
    monkeypatch.setenv("OPIK_MCP_RESOURCE_URI", "https://www.comet.com/api/v1/mcp")
    get_settings.cache_clear()
    r = await http_client.get(PROTECTED_RESOURCE_METADATA_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == ["https://www.comet.com"]
    assert body["resource"] == "https://www.comet.com/api/v1/mcp"


@pytest.mark.anyio
async def test_protected_resource_metadata_without_resource_uri(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resource`` field is optional — omitted from the JSON when unset."""
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://www.comet.com")
    monkeypatch.delenv("OPIK_MCP_RESOURCE_URI", raising=False)
    get_settings.cache_clear()
    r = await http_client.get(PROTECTED_RESOURCE_METADATA_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == ["https://www.comet.com"]
    assert "resource" not in body


@pytest.mark.anyio
async def test_protected_resource_metadata_is_auth_exempt(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrapping the OAuth dance requires reaching this endpoint without
    any credentials — otherwise the host can never discover the AS in the
    first place.
    """
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://www.comet.com")
    get_settings.cache_clear()
    # No Authorization header — would 401 on /mcp; must succeed here.
    r = await http_client.get(PROTECTED_RESOURCE_METADATA_PATH)
    assert r.status_code == 200
