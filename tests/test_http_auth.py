from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport

from opik_mcp.server import build_app

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


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = build_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
            yield c


@pytest.mark.anyio
async def test_no_auth_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", json=INITIALIZE)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.anyio
async def test_wrong_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", json=INITIALIZE, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_correct_token_initializes(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            "Authorization": "Bearer dev-token-123",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    assert "opik-mcp" in r.text
