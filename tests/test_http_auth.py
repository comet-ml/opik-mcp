"""Integration tests for inbound auth on the HTTP transport.

opik-mcp performs no local credential validation — any well-formed
``Authorization: Bearer …`` is accepted and forwarded verbatim to
opik-backend, which is the single point of auth enforcement. The middleware
only rejects requests that carry no usable bearer at all (missing header or
a non-Bearer scheme), returning 401 so MCP hosts bootstrap the OAuth dance.
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
async def test_any_bearer_initializes(http_client: httpx.AsyncClient) -> None:
    """No local validation: opik-mcp accepts the bearer and forwards it.

    ``initialize`` makes no outbound opik-backend call, so it succeeds
    regardless of whether the token would later be accepted upstream.
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

    from opik_mcp.session_identity import ResolvedIdentity, lookup_identity

    async def fake_resolve(_auth: str, _settings: object) -> ResolvedIdentity:
        return ResolvedIdentity(
            user_name="andrei",
            workspace_name="andreicautisanu",
            workspace_id="ws-uuid-e2e",
        )

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", fake_resolve)
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
