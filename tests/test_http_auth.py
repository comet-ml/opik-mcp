"""Integration tests for inbound auth on the HTTP transport.

opik-mcp performs no local credential validation — any well-formed
``Authorization: Bearer …`` is accepted and forwarded verbatim to
opik-backend, which is the single point of auth enforcement. The middleware
only rejects requests that carry no usable bearer at all (missing header or
a non-Bearer scheme), returning 401 so MCP hosts bootstrap the OAuth dance.
"""

import httpx
import pytest

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
            "Authorization": "Bearer opik_mcp_at_anything",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    assert "opik-mcp" in r.text
