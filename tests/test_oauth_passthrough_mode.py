"""Unit tests for ``BearerAuthMiddleware`` (OAuth passthrough).

The middleware is exercised here by driving it directly with stub call_next
+ manually constructed Starlette ``Request`` objects, asserting the
ContextVar capture/reset behavior the integration suite can't observe.
Introspection is stubbed at ``server.introspect_oauth_token``; the HTTP-level
contract (real resolver against a mocked backend) lives in
``test_oauth_token_validation.py``.
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
from opik_mcp.credential_identity import (
    credential_digest,
    lookup_session_digest,
    reset_identities_for_tests,
)
from opik_mcp.oauth_identity import Introspection
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
    from opik_mcp.credential_identity import ResolvedIdentity

    async def fake_resolve(_auth: str, _settings: object) -> Introspection:
        return Introspection(
            status="valid",
            identity=ResolvedIdentity(
                user_name="u",
                workspace_name="andreicautisanu",
                workspace_id="ws-id",
            ),
        )

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", fake_resolve)
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
async def test_validates_requests_that_already_carry_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every request with an OAuth bearer is validated, tool calls included
    (OPIK-8252): the access token can expire mid-session, and the 401 that
    tells the host to refresh has to come from the request that hit the dead
    token. The identity is not re-published on those requests; the blob only
    reads it on the handshake."""
    from opik_mcp.auth_context import resolved_workspace_name

    calls: list[str] = []

    async def spy_resolve(auth: str, _settings: object) -> Introspection:
        calls.append(auth)
        return Introspection(status="valid")

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", spy_resolve)
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

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200
    assert calls == [f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc"]
    assert captured["resolved"] is None


@pytest.mark.anyio
async def test_skips_resolution_for_api_key_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only OAuth-prefixed bearers carry a token-bound workspace to resolve;
    an API-key-shaped bearer keeps the legacy header/settings workspace path."""
    calls: list[str] = []

    async def spy_resolve(auth: str, _settings: object) -> Introspection:
        calls.append(auth)
        return Introspection(status="valid")

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", spy_resolve)
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
    from opik_mcp.credential_identity import ResolvedIdentity, lookup_identity

    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}handshake-abc"
    resolved = ResolvedIdentity(
        user_name="awkoy",
        workspace_name="awkoy-v2",
        workspace_id="ws-uuid-1",
    )

    async def _resolve(*_a: object, **_k: object) -> Introspection:
        return Introspection(status="valid", identity=resolved)

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", _resolve)

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
async def test_tool_call_on_a_dead_token_is_answered_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token that expires mid-session dies on a tool call, not on a handshake.
    That request must get the ``invalid_token`` 401 (OPIK-8252) — it is the only
    signal the host has to run the ``refresh_token`` grant — and nothing may be
    forwarded on the dead credential."""
    forwarded: list[str] = []

    async def _resolve(*_a: object, **_k: object) -> Introspection:
        return Introspection(status="invalid")

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", _resolve)

    mw = _build_middleware()
    request = _make_request(
        {
            "authorization": f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc",
            "mcp-session-id": "session-1",
        }
    )

    async def call_next(_r: Request) -> Response:
        forwarded.append("called")
        return JSONResponse({"ok": True})

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 401
    assert forwarded == []
    assert 'error="invalid_token"' in resp.headers["WWW-Authenticate"]
    # Nothing leaks past a rejected request.
    assert inbound_authorization.get() is None


@pytest.mark.anyio
async def test_failed_introspection_leaves_the_handshake_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend hiccup (network, 5xx: ``unknown``) must never cost a session —
    only a definite ``invalid`` answer is a rejection."""
    from opik_mcp.credential_identity import lookup_identity

    async def _resolve(*_a: object, **_k: object) -> Introspection:
        return Introspection(status="unknown")

    monkeypatch.setattr("opik_mcp.server.introspect_oauth_token", _resolve)

    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}unresolvable"
    mw = _build_middleware()
    request = _make_request({"authorization": f"Bearer {token}"})

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200
    assert lookup_identity(token) is None


# --- pairing the minted session id to the credential ----------------------- #
#
# The session id exists only on the RESPONSE to `initialize`, and the task that
# builds events is forked from that request before the id exists. So the
# middleware records the pairing on the way out; without it the session field is
# inert no matter what the request headers say.

MINTED_SESSION = "MINTED-SESSION-ID-9f14ab"


@pytest.mark.anyio
async def test_the_minted_session_id_is_paired_with_the_credential() -> None:
    """The handshake response is the only place both values are in scope."""
    reset_identities_for_tests()
    mw = _build_middleware()
    auth = f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}handshake"
    request = _make_request({"authorization": auth})

    async def call_next(_r: Request) -> Response:
        # No Mcp-Session-Id inbound: this is the session-minting request. The
        # transport puts the new id on the response.
        return JSONResponse({"ok": True}, headers={"mcp-session-id": MINTED_SESSION})

    await mw.dispatch(request, call_next)

    # Stored hashed, and reachable by the credential the session task holds.
    assert lookup_session_digest(auth) == credential_digest(MINTED_SESSION)


@pytest.mark.anyio
async def test_a_response_without_a_session_id_pairs_nothing() -> None:
    """Not every request mints a session; absence must stay absence."""
    reset_identities_for_tests()
    mw = _build_middleware()
    auth = f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}nosession"
    request = _make_request({"authorization": auth})

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True})

    await mw.dispatch(request, call_next)
    assert lookup_session_digest(auth) is None


@pytest.mark.anyio
async def test_a_request_already_carrying_a_session_does_not_repair_it() -> None:
    """Only the minting request pairs.

    A tool call carries the session id too, but re-pairing there would let a
    rotated credential mint a second entry for the same session — and the entry
    keyed to the HANDSHAKE credential is the only one the session task can read.
    """
    reset_identities_for_tests()
    mw = _build_middleware()
    rotated = f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}rotated"
    request = _make_request(
        {"authorization": rotated, "mcp-session-id": MINTED_SESSION},
    )

    async def call_next(_r: Request) -> Response:
        return JSONResponse({"ok": True}, headers={"mcp-session-id": MINTED_SESSION})

    await mw.dispatch(request, call_next)
    assert lookup_session_digest(rotated) is None
