"""Unit tests for ``BearerAuthMiddleware`` (OAuth passthrough).

The middleware is exercised here by driving it directly with stub call_next
+ manually constructed Starlette ``Request`` objects, asserting the
ContextVar capture/reset behavior the integration suite can't observe.
"""

from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opik_mcp.auth_context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    inbound_authorization,
    inbound_workspace,
)
from opik_mcp.server import BearerAuthMiddleware


def _make_request(headers: dict[str, str], path: str = "/mcp") -> Request:
    """Build a minimal ASGI scope for the middleware under test."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "http_version": "1.1",
        "extensions": {},
    }
    return Request(scope)


def _build_middleware(
    *,
    resource_metadata_url: str | None = "https://opik.host/.well-known/oauth-protected-resource",
) -> BearerAuthMiddleware:
    return BearerAuthMiddleware(
        app=None,  # type: ignore[arg-type]  # we never call call_next via the ASGI app
        resource_metadata_url=resource_metadata_url,
    )


@pytest.mark.anyio
async def test_passthrough_accepts_any_well_formed_bearer() -> None:
    """The middleware's whole point: accept any Bearer, forward verbatim.
    opik-backend's AuthFilter validates the token; opik-mcp is a thin pipe.
    """
    mw = _build_middleware()
    request = _make_request({"authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc123"})

    captured: dict[str, str | None] = {}

    async def call_next(_r: Request) -> Response:
        captured["auth"] = inbound_authorization.get()
        captured["workspace"] = inbound_workspace.get()
        return JSONResponse({"ok": True})

    resp = await mw.dispatch(request, call_next)

    assert resp.status_code == 200
    # Bearer captured exactly as inbound so outbound forwarding preserves it.
    assert captured["auth"] == f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc123"
    # No workspace header on this request → ContextVar reads as None.
    assert captured["workspace"] is None
    # ContextVar is reset after the request returns — no leakage to the
    # next request handled by the same worker.
    assert inbound_authorization.get() is None
    assert inbound_workspace.get() is None


@pytest.mark.anyio
async def test_passthrough_captures_comet_workspace_header() -> None:
    mw = _build_middleware()
    request = _make_request(
        {
            "authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc",
            "comet-workspace": "my-team",
        }
    )

    captured: dict[str, str | None] = {}

    async def call_next(_r: Request) -> Response:
        captured["workspace"] = inbound_workspace.get()
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert captured["workspace"] == "my-team"


@pytest.mark.anyio
async def test_resolves_workspace_on_session_creating_oauth_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``initialize`` handshake (no Mcp-Session-Id) on an OAuth bearer
    introspects the workspace name and exposes it via the ContextVar the
    instructions blob reads — then resets it after the request."""
    from opik_mcp.auth_context import resolved_workspace_name
    from opik_mcp.session_identity import ResolvedIdentity

    async def fake_resolve(_auth: str, _settings: object) -> ResolvedIdentity:
        return ResolvedIdentity(
            user_name="u",
            workspace_name="andreicautisanu",
            workspace_id="ws-id",
        )

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", fake_resolve)
    mw = _build_middleware()
    request = _make_request({"authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc"})

    captured: dict[str, str | None] = {}

    async def call_next(_r: Request) -> Response:
        captured["resolved"] = resolved_workspace_name.get()
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert captured["resolved"] == "andreicautisanu"
    # Reset after the request — no leakage to the next session.
    assert resolved_workspace_name.get() is None


@pytest.mark.anyio
async def test_skips_resolution_when_session_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requests carrying an Mcp-Session-Id are tool calls, not the handshake —
    they must not pay the introspection round-trip."""
    from opik_mcp.auth_context import resolved_workspace_name

    calls: list[str] = []

    async def spy_resolve(auth: str, _settings: object) -> None:
        calls.append(auth)
        return None

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", spy_resolve)
    mw = _build_middleware()
    request = _make_request(
        {
            "authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc",
            "mcp-session-id": "session-123",
        }
    )

    captured: dict[str, str | None] = {}

    async def call_next(_r: Request) -> Response:
        captured["resolved"] = resolved_workspace_name.get()
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert calls == []
    assert captured["resolved"] is None


@pytest.mark.anyio
async def test_skips_resolution_for_api_key_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only OAuth-prefixed bearers carry a token-bound workspace to resolve;
    an API-key-shaped bearer keeps the legacy header/settings workspace path."""
    calls: list[str] = []

    async def spy_resolve(auth: str, _settings: object) -> None:
        calls.append(auth)
        return None

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", spy_resolve)
    mw = _build_middleware()
    request = _make_request({"authorization": "Bearer some-static-api-key"})

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert calls == []


@pytest.mark.anyio
async def test_missing_authorization_returns_401_with_www_authenticate() -> None:
    """The 401 must point hosts at the protected-resource metadata so they
    can bootstrap the OAuth dance — without it, hosts have no path forward.
    """
    mw = _build_middleware(
        resource_metadata_url="https://opik.host/.well-known/oauth-protected-resource",
    )
    request = _make_request({})

    async def call_next(_r: Request) -> Response:
        raise AssertionError("should not reach call_next")

    resp = await mw.dispatch(request, call_next)

    assert resp.status_code == 401
    www_auth = resp.headers.get("www-authenticate", "")
    assert 'realm="opik-mcp"' in www_auth
    assert 'resource_metadata="https://opik.host/.well-known/oauth-protected-resource"' in www_auth


@pytest.mark.anyio
async def test_rejects_malformed_authorization() -> None:
    """Non-Bearer schemes are rejected up front — keeps the
    WWW-Authenticate hint consistent with the host's next attempt.
    """
    mw = _build_middleware()
    request = _make_request({"authorization": "Basic dXNlcjpwYXNz"})

    async def call_next(_r: Request) -> Response:
        raise AssertionError("should not reach call_next")

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_rejects_bearer_with_empty_token() -> None:
    """``Bearer`` with no (or whitespace-only) token is rejected locally
    with the WWW-Authenticate hint rather than forwarded for an opaque
    upstream 401.
    """
    mw = _build_middleware()
    for value in ("Bearer ", "Bearer    "):
        request = _make_request({"authorization": value})

        async def call_next(_r: Request) -> Response:
            raise AssertionError("should not reach call_next")

        resp = await mw.dispatch(request, call_next)
        assert resp.status_code == 401, f"authorization={value!r}"


@pytest.mark.anyio
async def test_health_paths_bypass_auth() -> None:
    """Liveness/readiness probes have no credentials by design."""
    mw = _build_middleware()
    for path in ("/health", "/health/ready"):
        request = _make_request({}, path=path)

        async def call_next(_r: Request) -> Response:
            return JSONResponse({"status": "ok"})

        resp = await mw.dispatch(request, call_next)
        assert resp.status_code == 200, f"path={path}"


@pytest.mark.anyio
async def test_protected_resource_metadata_bypasses_auth() -> None:
    """Discovery doc is the bootstrap entry point — must be reachable
    pre-credentials.
    """
    mw = _build_middleware()
    request = _make_request({}, path="/.well-known/oauth-protected-resource")

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"authorization_servers": ["x"]})

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_unauthorized_without_resource_metadata_url() -> None:
    """When the resource-metadata URL is unset, ``WWW-Authenticate`` is
    omitted entirely (vs. an empty value, which some host parsers reject).
    """
    mw = _build_middleware(resource_metadata_url=None)
    request = _make_request({})

    async def call_next(_r: Request) -> Response:
        raise AssertionError("should not reach call_next")

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 401
    # Starlette ``MutableHeaders`` is case-insensitive — direct ``in`` is enough.
    assert "www-authenticate" not in resp.headers


@pytest.mark.anyio
async def test_handshake_stores_the_resolved_identity_against_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handshake is the only request that introspects; the answer must outlive it.

    The instructions blob reads the workspace out of a ContextVar that this
    middleware resets on the way out, and the analytics layer builds events in
    the MCP session task, which never runs inside this request. Keeping the
    identity in a credential-keyed store is what makes it readable from both.
    """
    from opik_mcp.session_identity import ResolvedIdentity, lookup_identity

    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}handshake-abc"
    resolved = ResolvedIdentity(
        user_name="awkoy",
        workspace_name="awkoy-v2",
        workspace_id="ws-uuid-1",
    )

    async def _resolve(*_a: object, **_k: object) -> ResolvedIdentity:
        return resolved

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", _resolve)

    mw = _build_middleware()
    request = _make_request({"authorization": f"Bearer {token}"})

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200

    # The ContextVars are gone the moment the request returns...
    assert inbound_authorization.get() is None
    # ...but the identity is still reachable by the credential it belongs to,
    # which is what a later tool call in this session has to work with.
    assert lookup_identity(token) == resolved


@pytest.mark.anyio
async def test_tool_call_requests_do_not_reintrospect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the session-creating request introspects. Every later request carries
    an ``Mcp-Session-Id`` and must not pay a round-trip for an answer we hold."""
    calls: list[str] = []

    async def _resolve(*_a: object, **_k: object) -> None:
        calls.append("resolved")
        return None

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", _resolve)

    mw = _build_middleware()
    request = _make_request(
        {
            "authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc",
            "mcp-session-id": "session-1",
        }
    )

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert calls == []


@pytest.mark.anyio
async def test_failed_introspection_leaves_the_handshake_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry and display niceties must never cost a session."""
    from opik_mcp.session_identity import lookup_identity

    async def _resolve(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("opik_mcp.server.resolve_oauth_identity", _resolve)

    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}unresolvable"
    mw = _build_middleware()
    request = _make_request({"authorization": f"Bearer {token}"})

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200
    assert lookup_identity(token) is None
