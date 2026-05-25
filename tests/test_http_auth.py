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
async def test_wrong_token_returns_401(http_client: httpx.AsyncClient) -> None:
    r = await http_client.post("/mcp", json=INITIALIZE, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_correct_token_initializes(http_client: httpx.AsyncClient) -> None:
    r = await http_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            "Authorization": "Bearer dev-token-123",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    assert "opik-mcp" in r.text
