"""Integration tests for inbound auth on the HTTP transport.

Two bearer shapes, two contracts. An ``opik_mcp_at_``-prefixed OAuth token is
validated against opik-backend on every request and rejected with an
``invalid_token`` 401 when dead (OPIK-8252; see
``test_oauth_token_validation.py`` for that contract). Any other well-formed
``Authorization: Bearer …`` is an API key: accepted and forwarded verbatim to
opik-backend, which is its single point of enforcement. Requests that carry no
usable bearer at all (missing header or a non-Bearer scheme) get a 401 so MCP
hosts bootstrap the OAuth dance.
"""

import httpx
import pytest

from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX

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


@pytest.mark.anyio
async def test_no_auth_returns_401(http_client: httpx.AsyncClient) -> None:
    r = await http_client.post("/mcp", json=INITIALIZE)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.anyio
async def test_non_bearer_scheme_returns_401(http_client: httpx.AsyncClient) -> None:
    r = await http_client.post(
        "/mcp", json=INITIALIZE, headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert r.status_code == 401


@pytest.mark.anyio
async def test_api_key_bearer_initializes(http_client: httpx.AsyncClient) -> None:
    """API keys are not validated locally: opik-mcp accepts the bearer and
    forwards it. ``initialize`` makes no outbound opik-backend call, so it
    succeeds regardless of whether the key would later be accepted upstream.
    (The OAuth-shaped bearer below is still accepted here only because conftest
    stubs introspection to ``unknown`` — the fail-open outcome.)
    """
    r = await http_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            "Authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}anything",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    assert "opik-mcp" in r.text


@pytest.mark.anyio
async def test_initialize_names_oauth_workspace(
    http_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (OPIK-7033): the per-session instructions blob in the
    ``initialize`` result names the OAuth-authorized workspace, not "default".

    Drives the whole path — middleware introspection → ContextVar → per-session
    ``create_initialization_options`` re-render — over the real ASGI app, with
    only the backend introspection call stubbed.
    """

    from opik_mcp.credential_identity import ResolvedIdentity, lookup_identity
    from opik_mcp.oauth_identity import Introspection

    async def fake_resolve(_auth: str, _settings: object) -> Introspection:
        return Introspection(
            status="valid",
            identity=ResolvedIdentity(
                user_name="andrei",
                workspace_name="andreicautisanu",
                workspace_id="ws-uuid-e2e",
            ),
        )

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", fake_resolve)
    r = await http_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            "Authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}tok",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    # The identity resolved during this handshake outlives the request that
    # resolved it: the ContextVars are reset on the way out, and the analytics
    # layer builds events in the MCP session task, not in this request.
    stored = lookup_identity(f"{OAUTH_ACCESS_TOKEN_PREFIX}tok")
    assert stored is not None
    assert stored.user_name == "andrei"
    assert stored.workspace_id == "ws-uuid-e2e"
    # The blob is a JSON string inside the JSON-RPC result, so its inner quotes
    # are backslash-escaped in the raw SSE body.
    assert 'workspace \\"andreicautisanu\\"' in r.text
    assert 'workspace \\"default\\"' not in r.text
