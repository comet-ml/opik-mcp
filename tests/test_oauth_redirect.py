"""Tests for AS-probe proxy routes.

MCP host SDKs probe AS-discovery + OAuth-flow endpoints at the resource
server's host before they have a token. opik-mcp proxies those probes to
the configured AS (``OPIK_MCP_AS_URL``) so split-host deployments (local
docker-compose, dev clusters where opik-mcp and opik-backend bind to
different addresses) work the same as the production single-edge deploy.

Proxying (rather than redirect) because some host SDKs refuse to follow
cross-origin OAuth-discovery redirects and silently break their bootstrap.
"""

import httpx
import pytest
import respx

from opik_mcp.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_well_known_as_metadata_proxied_to_configured_as(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPIK_MCP_AS_URL", "http://localhost:5173/api")
    get_settings.cache_clear()
    upstream_body = {
        "issuer": "http://localhost:5173/api",
        "authorization_endpoint": "http://localhost:5173/api/oauth/authorize",
        "token_endpoint": "http://localhost:5173/api/oauth/token",
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(
            "http://localhost:5173/api/.well-known/oauth-authorization-server"
        ).mock(return_value=httpx.Response(200, json=upstream_body))
        r = await http_client.get(
            "/.well-known/oauth-authorization-server", follow_redirects=False
        )
    assert r.status_code == 200
    assert r.json() == upstream_body


@pytest.mark.anyio
async def test_oidc_config_proxied_to_as_metadata(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SDKs that fall back to OIDC discovery get the same AS metadata doc —
    RFC 8414 metadata is a superset of what the SDK needs.
    """
    monkeypatch.setenv("OPIK_MCP_AS_URL", "http://localhost:5173/api")
    get_settings.cache_clear()
    upstream_body = {"issuer": "http://localhost:5173/api"}
    with respx.mock(assert_all_called=False) as mock:
        mock.get(
            "http://localhost:5173/api/.well-known/oauth-authorization-server"
        ).mock(return_value=httpx.Response(200, json=upstream_body))
        r = await http_client.get(
            "/.well-known/openid-configuration", follow_redirects=False
        )
    assert r.status_code == 200
    assert r.json() == upstream_body


@pytest.mark.anyio
async def test_register_post_proxied_with_body(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DCR is ``POST /oauth/register`` on the AS. The proxy forwards the
    JSON body and returns the AS's 201 with the minted client_id.
    """
    monkeypatch.setenv("OPIK_MCP_AS_URL", "http://localhost:5173/api")
    get_settings.cache_clear()
    upstream_body = {
        "client_id": "abc-123",
        "client_name": "test",
        "redirect_uris": ["http://x"],
    }
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://localhost:5173/api/oauth/register").mock(
            return_value=httpx.Response(201, json=upstream_body)
        )
        r = await http_client.post(
            "/register",
            json={"client_name": "test", "redirect_uris": ["http://x"]},
            follow_redirects=False,
        )
    assert r.status_code == 201
    assert r.json() == upstream_body
    assert route.called


@pytest.mark.anyio
async def test_authorize_get_preserves_query_string(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/oauth/authorize`` carries client_id, PKCE, state, etc. — must
    round-trip through the proxy. A redirect from the AS (302 to the
    consent UI) propagates back as a 302 to the SDK / browser.
    """
    monkeypatch.setenv("OPIK_MCP_AS_URL", "http://localhost:5173/api")
    get_settings.cache_clear()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(
            "http://localhost:5173/api/oauth/authorize"
        ).mock(
            return_value=httpx.Response(
                302,
                headers={"location": "http://localhost:5173/oauth/consent?x=1"},
            )
        )
        r = await http_client.get(
            "/authorize?client_id=abc&state=xyz", follow_redirects=False
        )
    assert r.status_code == 302
    assert r.headers["location"] == "http://localhost:5173/oauth/consent?x=1"
    # Confirm the proxy forwarded the query string upstream.
    assert "client_id=abc" in str(route.calls.last.request.url)
    assert "state=xyz" in str(route.calls.last.request.url)


@pytest.mark.anyio
async def test_proxy_unconfigured_returns_503(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPIK_MCP_AS_URL", raising=False)
    get_settings.cache_clear()
    r = await http_client.get(
        "/.well-known/oauth-authorization-server", follow_redirects=False
    )
    assert r.status_code == 503


@pytest.mark.anyio
async def test_proxy_paths_are_auth_exempt(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probes happen pre-token, so the auth middleware must NOT
    intercept them. The integration ``http_client`` runs in dev-token
    mode; a 401 here would indicate the auth middleware fired before
    the proxy route ran.
    """
    monkeypatch.setenv("OPIK_MCP_AS_URL", "http://localhost:5173/api")
    get_settings.cache_clear()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(
            "http://localhost:5173/api/.well-known/oauth-authorization-server"
        ).mock(return_value=httpx.Response(200, json={"issuer": "x"}))
        # No Authorization header — must NOT 401.
        r = await http_client.get(
            "/.well-known/oauth-authorization-server", follow_redirects=False
        )
    assert r.status_code != 401, "AS probe must bypass the auth middleware"


@pytest.mark.anyio
async def test_path_prefixed_well_known_bypasses_auth(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some SDKs probe path-prefixed variants like
    ``/.well-known/oauth-protected-resource/mcp`` and
    ``/mcp/.well-known/openid-configuration``. We don't serve those
    routes, but the auth middleware must let them through to the 404
    handler instead of intercepting with 401 — otherwise the SDK
    interprets the 401 as a fatal auth failure and aborts bootstrap.
    """
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server/mcp",
        "/mcp/.well-known/openid-configuration",
    ):
        r = await http_client.get(path, follow_redirects=False)
        assert r.status_code != 401, f"{path} must bypass auth, got 401"


@pytest.mark.anyio
async def test_404_returns_json_not_plain_text(
    http_client: httpx.AsyncClient,
) -> None:
    """Starlette's default 404 is ``text/plain`` with body ``Not Found``;
    MCP host SDKs that JSON-parse every response abort with "Failed to
    parse JSON" when they probe a path-prefixed well-known endpoint and
    hit that 404. Our default-route handler returns JSON instead.
    """
    r = await http_client.get(
        "/.well-known/oauth-protected-resource/mcp", follow_redirects=False
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": "not_found"}
