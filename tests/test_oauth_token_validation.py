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


# --- tool errors on an upstream 401 ------------------------------------------ #

PROJECT_ID = "0f1c1a2b-3d4e-4f60-8a9b-0c1d2e3f4a5b"


def _valid_introspection() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "user_name": "andrei",
            "workspace_id": "ws-uuid",
            "workspace_name": "andreicautisanu",
            "resource": "https://www.comet.com/opik/api/v1/mcp",
        },
    )


def _project_url() -> str:
    base = opik_rest_base(get_settings())
    return f"{base}/v1/private/projects/{PROJECT_ID}"


def _jsonrpc_result(r: httpx.Response) -> dict[str, object]:
    """The JSON-RPC result out of either a JSON or an SSE-framed response."""
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        payloads = [
            line[len("data:") :].strip() for line in r.text.splitlines() if line.startswith("data:")
        ]
        assert payloads, r.text
        body = httpx.Response(200, content=payloads[-1]).json()
    else:
        body = r.json()
    assert "result" in body, body
    result: dict[str, object] = body["result"]
    return result


async def _read_project(http_client: httpx.AsyncClient, authorization: str) -> dict[str, object]:
    """Run ``read(project, PROJECT_ID)`` over a fresh MCP session as the host would."""
    headers = {"Authorization": authorization, "Accept": "application/json, text/event-stream"}
    init = await http_client.post("/mcp", json=INITIALIZE, headers=headers)
    assert init.status_code == 200, init.text
    session = {**headers, "Mcp-Session-Id": init.headers["mcp-session-id"]}
    await http_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=session,
    )
    r = await http_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {"entity_type": "project", "id": PROJECT_ID},
            },
        },
        headers=session,
    )
    assert r.status_code == 200, r.text
    return _jsonrpc_result(r)


def _error_text(result: dict[str, object]) -> str:
    assert result.get("isError") is True, result
    content = result["content"]
    assert isinstance(content, list)
    return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))


@pytest.mark.anyio
async def test_upstream_401_in_oauth_mode_says_token_expired_retry(
    http_client: httpx.AsyncClient,
) -> None:
    """A token that dies inside the validation window still reaches opik-backend
    once. The tool error must tell the model the *token* expired and to retry —
    not to check API keys, and not to tell the user to reconnect — because the
    retry is what runs into the 401 that triggers the host's refresh."""
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_introspection_url()).mock(return_value=_valid_introspection())
        mock.get(_project_url()).mock(return_value=httpx.Response(401))
        result = await _read_project(
            http_client, f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}dies-mid-window"
        )

    text = _error_text(result)
    assert "access token" in text.lower()
    assert "expired" in text.lower()
    assert "retry" in text.lower()
    assert "OPIK_API_KEY" not in text
    assert "permission denied" not in text.lower()


@pytest.mark.anyio
async def test_upstream_401_in_api_key_mode_keeps_the_api_key_wording(
    http_client: httpx.AsyncClient,
) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(_project_url()).mock(return_value=httpx.Response(401))
        result = await _read_project(http_client, "Bearer some-static-api-key")

    text = _error_text(result)
    assert "OPIK_API_KEY" in text


@pytest.mark.anyio
async def test_upstream_403_keeps_the_permission_wording(http_client: httpx.AsyncClient) -> None:
    """403 really is about workspace access; only the 401 wording changed."""
    with respx.mock(assert_all_called=True) as mock:
        mock.post(_introspection_url()).mock(return_value=_valid_introspection())
        mock.get(_project_url()).mock(return_value=httpx.Response(403))
        result = await _read_project(http_client, f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}no-access")

    text = _error_text(result)
    assert "permission denied" in text.lower()
    assert "expired" not in text.lower()
