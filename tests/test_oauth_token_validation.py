"""OAuth access-token validation at the HTTP boundary (OPIK-8252).

opik-mcp is the OAuth resource server for the hosted connector. Per the MCP
authorization spec (2026-07-28, "Token Handling") it MUST validate every
access token and MUST answer HTTP 401 for an invalid or expired one — that 401
is the only signal an MCP host has to run the ``refresh_token`` grant. Before
this suite existed a dead token surfaced as a tool error inside HTTP 200, the
host never refreshed, and users lost the connector an hour after connecting.

Seam: the real ASGI app over ``http_client``; opik-backend mocked with respx at
the introspection endpoint (``POST {rest_base}/opik/auth-oauth``). Tests assert
only what a host observes — status, ``WWW-Authenticate``, body, and how many
times the backend was asked.
"""

from collections.abc import Iterator

import httpx
import pytest
import respx

from opik_mcp import oauth_identity
from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX
from opik_mcp.config import get_settings
from opik_mcp.opik_client import opik_rest_base

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}

OAUTH_HEADERS = {
    "Authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}expired-token",
    "Accept": "application/json, text/event-stream",
}


def _introspection_url() -> str:
    base = opik_rest_base(get_settings())
    assert base is not None
    return f"{base}/opik/auth-oauth"


@pytest.fixture(autouse=True)
def _live_introspection(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Undo conftest's no-op stub so the real introspection runs against respx."""
    monkeypatch.setattr(
        "opik_mcp.server.introspect_oauth_token", oauth_identity.introspect_oauth_token
    )
    yield


@pytest.mark.anyio
async def test_expired_oauth_token_gets_401_with_invalid_token_challenge(
    http_client: httpx.AsyncClient,
) -> None:
    """opik-backend says the token is dead → the host gets a real 401 it can act on."""
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_introspection_url()).mock(return_value=httpx.Response(401))
        r = await http_client.post("/mcp", json=INITIALIZE, headers=OAUTH_HEADERS)

    assert r.status_code == 401
    challenge = r.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'error="invalid_token"' in challenge
    assert "resource_metadata=" in challenge
    assert r.json()["error"] == "invalid_token"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [httpx.Response(503), httpx.ConnectError("backend down")],
    ids=["5xx", "connection-error"],
)
async def test_introspection_failure_fails_open(
    http_client: httpx.AsyncClient, outcome: httpx.Response | Exception
) -> None:
    """A backend hiccup must not log out every connected host: only a definite
    401 from introspection is a rejection; anything else forwards as before."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(_introspection_url())
        if isinstance(outcome, Exception):
            route.mock(side_effect=outcome)
        else:
            route.mock(return_value=outcome)
        r = await http_client.post("/mcp", json=INITIALIZE, headers=OAUTH_HEADERS)

    assert r.status_code == 200
    assert "opik-mcp" in r.text


@pytest.mark.anyio
async def test_api_key_bearer_is_never_introspected(http_client: httpx.AsyncClient) -> None:
    """Validation is for OAuth tokens only. API-key bearers keep the legacy
    passthrough: opik-backend's AuthFilter is their single point of enforcement."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(_introspection_url()).mock(return_value=httpx.Response(401))
        r = await http_client.post(
            "/mcp",
            json=INITIALIZE,
            headers={
                "Authorization": "Bearer some-static-api-key",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert r.status_code == 200
    assert not route.called


@pytest.mark.anyio
async def test_handshake_validates_once_and_names_the_workspace(
    http_client: httpx.AsyncClient,
) -> None:
    """The session-creating request pays exactly one introspection round-trip,
    which both validates the token and tells the instructions blob which
    workspace the agent is operating against."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(_introspection_url()).mock(
            return_value=httpx.Response(
                200,
                json={
                    "user_name": "andrei",
                    "workspace_id": "ws-uuid",
                    "workspace_name": "andreicautisanu",
                    "resource": "https://www.comet.com/opik/api/v1/mcp",
                },
            )
        )
        r = await http_client.post(
            "/mcp",
            json=INITIALIZE,
            headers={
                "Authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}live-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert r.status_code == 200
    assert route.call_count == 1
    assert "andreicautisanu" in r.text
