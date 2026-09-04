"""A refreshed OAuth bearer must be the one forwarded to opik-backend (OPIK-8252).

After ``BearerAuthMiddleware`` answers ``invalid_token`` 401, the MCP host runs
the ``refresh_token`` grant and re-sends the SAME session's next request with a
NEW bearer. Tool handlers run in the MCP session task, which is forked from the
``initialize`` request — so any ContextVar they read holds the handshake-time
value unless the request-time value is threaded through some other way. If the
outbound client forwards the handshake-time bearer, every call after a refresh
meets the backend's 401 and the connector never recovers, which is exactly the
symptom the 401 was introduced to fix.

Seam: the real ASGI app over ``http_client``; opik-backend mocked with respx at
introspection (always valid) and at the data endpoint, which records which
bearer actually arrived.
"""

from collections.abc import Iterator

import httpx
import pytest
import respx

from opik_mcp import oauth_identity
from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX
from opik_mcp.config import get_settings
from opik_mcp.opik_client import opik_rest_base

PROJECT_ID = "0f1c1a2b-3d4e-4f60-8a9b-0c1d2e3f4a5b"
HANDSHAKE_TOKEN = f"{OAUTH_ACCESS_TOKEN_PREFIX}minted-at-connect"
REFRESHED_TOKEN = f"{OAUTH_ACCESS_TOKEN_PREFIX}minted-by-refresh"

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


@pytest.fixture(autouse=True)
def _live_introspection(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        "opik_mcp.server.introspect_oauth_token", oauth_identity.introspect_oauth_token
    )
    yield


def _headers(token: str, session_id: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    if session_id:
        h["Mcp-Session-Id"] = session_id
    return h


@pytest.mark.anyio
async def test_data_call_forwards_the_bearer_of_the_current_request(
    http_client: httpx.AsyncClient,
) -> None:
    base = opik_rest_base(get_settings())
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={"id": PROJECT_ID, "name": "p"})

    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{base}/opik/auth-oauth").mock(
            return_value=httpx.Response(200, json={"workspace_name": "ws", "user_name": "u"})
        )
        mock.get(f"{base}/v1/private/projects/{PROJECT_ID}").mock(side_effect=record)

        init = await http_client.post("/mcp", json=INITIALIZE, headers=_headers(HANDSHAKE_TOKEN))
        assert init.status_code == 200, init.text
        session = init.headers["mcp-session-id"]
        await http_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_headers(HANDSHAKE_TOKEN, session),
        )
        # The host refreshed: same session, new bearer.
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
            headers=_headers(REFRESHED_TOKEN, session),
        )
        assert r.status_code == 200, r.text

    assert seen, "the data endpoint was never called"
    assert seen[-1] == f"Bearer {REFRESHED_TOKEN}", (
        f"outbound call carried {seen[-1]!r}: the handshake-time bearer, not the refreshed one"
    )


# --- the rebinding hook itself ------------------------------------------------ #


@pytest.mark.anyio
async def test_rebinding_is_a_no_op_without_an_http_request() -> None:
    """stdio: there is no request behind a tool call, so the vars the caller
    set (or left unset) must be exactly what the tool sees."""
    from types import SimpleNamespace

    from mcp.types import CallToolRequest

    from opik_mcp.auth_context import inbound_authorization, inbound_workspace
    from opik_mcp.server import install_request_auth_rebinding

    seen: list[tuple[str | None, str | None]] = []

    async def original(_req: object) -> str:
        seen.append((inbound_authorization.get(), inbound_workspace.get()))
        return "ok"

    fake = SimpleNamespace(
        _mcp_server=SimpleNamespace(request_handlers={CallToolRequest: original})
    )
    install_request_auth_rebinding(fake)  # type: ignore[arg-type]
    wrapped = fake._mcp_server.request_handlers[CallToolRequest]
    assert wrapped is not original

    assert await wrapped(object()) == "ok"
    assert seen == [(None, None)]


@pytest.mark.anyio
async def test_rebinding_uses_the_current_request_and_resets_after() -> None:
    from types import SimpleNamespace

    from mcp.server.lowlevel.server import request_ctx
    from mcp.types import CallToolRequest
    from starlette.requests import Request

    from opik_mcp.auth_context import inbound_authorization, inbound_workspace
    from opik_mcp.server import install_request_auth_rebinding

    seen: list[tuple[str | None, str | None]] = []

    async def original(_req: object) -> str:
        seen.append((inbound_authorization.get(), inbound_workspace.get()))
        return "ok"

    fake = SimpleNamespace(
        _mcp_server=SimpleNamespace(request_handlers={CallToolRequest: original})
    )
    install_request_auth_rebinding(fake)  # type: ignore[arg-type]
    wrapped = fake._mcp_server.request_handlers[CallToolRequest]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"authorization", f"Bearer {REFRESHED_TOKEN}".encode()),
            (b"comet-workspace", b"ws-from-request"),
        ],
    }
    # What the session task inherited from the handshake.
    outer_auth = inbound_authorization.set(f"Bearer {HANDSHAKE_TOKEN}")
    outer_ws = inbound_workspace.set(None)
    ctx_token = request_ctx.set(SimpleNamespace(request=Request(scope)))  # type: ignore[arg-type]
    try:
        assert await wrapped(object()) == "ok"
        # Reset: the session task's view is back to the handshake values.
        assert inbound_authorization.get() == f"Bearer {HANDSHAKE_TOKEN}"
        assert inbound_workspace.get() is None
    finally:
        request_ctx.reset(ctx_token)
        inbound_workspace.reset(outer_ws)
        inbound_authorization.reset(outer_auth)

    assert seen == [(f"Bearer {REFRESHED_TOKEN}", "ws-from-request")]
