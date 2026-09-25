"""The hosted server's bearer path, as a host meets it over Streamable HTTP.

WHAT THIS COVERS THAT NOTHING ELSE DOES. ``tests/identity/test_http_auth.py`` and
``tests/identity/test_oauth_token_validation.py`` drive the ASGI app in-process with
introspection stubbed as a function. Here the server is the process the
Docker image runs, introspection is an HTTP call to the stub, and every
assertion is on what crossed a socket: the status and challenge a host keys
its refresh on, and the ``Authorization`` header the backend received.

The failure this file exists for is the one OPIK-8252 fixed: an expired OAuth
token that came back as a tool error inside HTTP 200, so the host never
refreshed and the connector died about an hour after connecting. The last
test walks that whole sequence: a live session, the token dying, the tool
error that tells the model to retry, and the 401 the retry meets.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX, OAUTH_TOKEN_EXPIRED_HINT
from tests.hermetic.servers import (
    WORKSPACE,
    HttpServer,
    http_session,
    post_initialize,
    result_json,
    result_text,
    stub_with_http_server,
)
from tests.hermetic.stub_backend import PROJECT_NAME, StubBackend

pytestmark = pytest.mark.hermetic

_INTROSPECT = "/opik/auth-oauth"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def module_server() -> Iterator[tuple[StubBackend, HttpServer]]:
    # Long enough that no test outlives a cached validation on a slow runner:
    # the tests count introspection calls and rely on the cache between them.
    with stub_with_http_server(OPIK_MCP_OAUTH_VALIDATION_CACHE_TTL_S="600") as running:
        yield running


@pytest.fixture
def backend(module_server: tuple[StubBackend, HttpServer]) -> StubBackend:
    stub, _ = module_server
    stub.reset()
    return stub


@pytest.fixture
def server(module_server: tuple[StubBackend, HttpServer], backend: StubBackend) -> HttpServer:
    return module_server[1]


def _oauth_token(name: str) -> str:
    """A token per test: the server caches a validation per token, and one
    test's cached answer must not decide the next test's."""
    return f"{OAUTH_ACCESS_TOKEN_PREFIX}{name}"


def _read_project() -> tuple[str, dict[str, object]]:
    return "read", {"entity_type": "project", "id": PROJECT_NAME}


@pytest.mark.anyio
async def test_a_request_with_no_bearer_is_challenged_and_reaches_nothing(
    server: HttpServer, backend: StubBackend
) -> None:
    response = await post_initialize(server, headers={})

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert "resource_metadata=" in response.headers.get("www-authenticate", "")
    assert backend.requests == []


@pytest.mark.anyio
async def test_an_api_key_bearer_is_forwarded_as_sent_with_its_workspace(
    server: HttpServer, backend: StubBackend
) -> None:
    """The key is not checked here: opik-backend is its one point of
    enforcement, so no introspection call is made for it."""
    async with http_session(server, bearer="caller-api-key") as session:
        result = await session.call_tool(*_read_project())
    assert not result.isError, result_text(result)

    assert not backend.called(_INTROSPECT)
    assert backend.requests, "the read reached no backend"
    for request in backend.requests:
        assert request.headers.get("authorization") == "Bearer caller-api-key", request.path
        assert request.headers.get("comet-workspace") == WORKSPACE, request.path


@pytest.mark.anyio
async def test_a_rejected_api_key_is_a_tool_error_not_a_refresh(
    server: HttpServer, backend: StubBackend
) -> None:
    """A 401 for an API key keeps the session: there is nothing for a host to
    refresh, so answering ``invalid_token`` would only disconnect it."""
    backend.dead_bearers.add("revoked-api-key")
    async with http_session(server, bearer="revoked-api-key") as session:
        result = await session.call_tool(*_read_project())
    assert result.isError
    assert backend.requests, "the read never reached the backend"
    assert "401" in result_text(result), result_text(result)
    assert OAUTH_TOKEN_EXPIRED_HINT not in result_text(result)

    again = await post_initialize(server, headers={"Authorization": "Bearer revoked-api-key"})
    assert again.status_code == 200


@pytest.mark.anyio
async def test_a_live_oauth_token_is_checked_once_and_forwarded(
    server: HttpServer, backend: StubBackend
) -> None:
    token = _oauth_token("live")
    async with http_session(server, bearer=token, workspace=None) as session:
        read = await session.call_tool(*_read_project())
        written = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "refund-cases"}}
        )
    assert not read.isError, result_text(read)
    assert not written.isError, result_json(written)

    # Validated on the first request and served from the cache after it.
    (introspection,) = backend.sent(_INTROSPECT)
    assert introspection.headers.get("authorization") == f"Bearer {token}"
    (write,) = backend.writes()
    assert write.headers.get("authorization") == f"Bearer {token}"


@pytest.mark.anyio
async def test_an_expired_oauth_token_gets_the_401_a_host_refreshes_on(
    server: HttpServer, backend: StubBackend
) -> None:
    token = _oauth_token("expired-at-connect")
    backend.dead_bearers.add(token)

    response = await post_initialize(server, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert 'error="invalid_token"' in response.headers.get("www-authenticate", "")
    # Refused after introspection, before anything reached a data route.
    assert [r.path for r in backend.requests] == [_INTROSPECT]


@pytest.mark.anyio
async def test_a_token_that_expires_mid_session_leads_the_host_to_refresh(
    server: HttpServer, backend: StubBackend
) -> None:
    """The sequence OPIK-8252 broke. The validation is cached, so the call
    after the token dies still reaches the backend and gets its 401. That
    answer has to tell the model to retry and drop the cached validation, so
    the retry is refused with the ``invalid_token`` a host refreshes on."""
    token = _oauth_token("expires-mid-session")
    async with http_session(server, bearer=token, workspace=None) as session:
        before = await session.call_tool(*_read_project())
        assert not before.isError, result_text(before)

        backend.dead_bearers.add(token)
        after = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "refund-cases"}}
        )
    assert after.isError
    envelope = result_json(after)
    backend_error = envelope["backend_error"]
    assert isinstance(backend_error, dict)
    assert backend_error["status"] == 401
    assert OAUTH_TOKEN_EXPIRED_HINT in str(envelope["message"])

    retry = await post_initialize(server, headers={"Authorization": f"Bearer {token}"})
    assert retry.status_code == 401
    assert retry.json()["error"] == "invalid_token"


@pytest.mark.anyio
async def test_the_callers_credential_wins_over_the_servers_own() -> None:
    """A server that also holds a key and a workspace, as a misconfigured
    deployment might, still forwards the caller's. Falling back to the
    environment would run one user's call in another user's workspace."""
    with stub_with_http_server(
        OPIK_API_KEY="server-own-key", OPIK_WORKSPACE="server-own-workspace"
    ) as (backend, server):
        async with http_session(server, bearer="caller-api-key") as session:
            result = await session.call_tool(*_read_project())
    assert not result.isError, result_text(result)

    assert backend.requests, "the read reached no backend"
    for request in backend.requests:
        assert request.headers.get("authorization") == "Bearer caller-api-key", request.path
        assert request.headers.get("comet-workspace") == WORKSPACE, request.path
