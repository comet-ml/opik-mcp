"""Unit tests for OAuth token → workspace-name introspection.

``resolve_oauth_identity`` POSTs the inbound bearer to opik-backend's
``/opik/auth-oauth`` introspection endpoint and pulls ``workspace_name`` out of
the ``ValidatedToken`` response. It is best-effort: any failure resolves to
``None`` so the ``initialize`` handshake never breaks.
"""

import httpx
import pytest
import respx

from opik_mcp.config import Settings
from opik_mcp.oauth_identity import resolve_oauth_identity

AUTH = "Bearer opik_mcp_at_abc123"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"opik_url": "https://opik.test/api"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@respx.mock
async def test_resolves_workspace_name_on_200() -> None:
    route = respx.post("https://opik.test/api/opik/auth-oauth").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_name": "u",
                "workspace_id": "ws-id",
                "workspace_name": "andreicautisanu",
                "resource": "https://opik.test/api/v1/mcp",
            },
        )
    )
    identity = await resolve_oauth_identity(AUTH, _settings())
    assert identity is not None
    assert identity.workspace_name == "andreicautisanu"
    # The whole ValidatedToken is retained now — BI needs the user and the
    # workspace UUID, which this endpoint has always returned and we dropped.
    assert identity.user_name == "u"
    assert identity.workspace_id == "ws-id"
    assert identity.source == "oauth"
    assert route.called
    # Inbound bearer is forwarded verbatim — opik-backend re-validates it.
    assert route.calls.last.request.headers["authorization"] == AUTH


@pytest.mark.anyio
@respx.mock
async def test_returns_none_on_401() -> None:
    respx.post("https://opik.test/api/opik/auth-oauth").mock(return_value=httpx.Response(401))
    assert await resolve_oauth_identity(AUTH, _settings()) is None


@pytest.mark.anyio
async def test_returns_none_on_invalid_url() -> None:
    """A malformed REST base must fail soft, not crash the handshake.

    ``httpx.InvalidURL`` is a direct ``Exception`` subclass (NOT an
    ``httpx.HTTPError``), so it would escape a narrow ``except`` and 500 the
    ``initialize`` request. The whitespace in the host forces ``InvalidURL`` at
    request-build time (before any transport / respx mock is consulted).
    """
    s = _settings(opik_url="http://exa mple.com/api")
    assert await resolve_oauth_identity(AUTH, s) is None


@pytest.mark.anyio
@respx.mock
async def test_returns_none_on_network_error() -> None:
    respx.post("https://opik.test/api/opik/auth-oauth").mock(
        side_effect=httpx.ConnectError("backend unreachable")
    )
    assert await resolve_oauth_identity(AUTH, _settings()) is None


@pytest.mark.anyio
@respx.mock
async def test_returns_none_on_non_json_body() -> None:
    respx.post("https://opik.test/api/opik/auth-oauth").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    assert await resolve_oauth_identity(AUTH, _settings()) is None


@pytest.mark.anyio
@respx.mock
async def test_keeps_a_user_only_response() -> None:
    """A response with a user but no workspace is still worth keeping.

    The old contract threw the whole thing away unless a workspace name was
    present, because the only consumer was the instructions blob. BI can
    attribute a call from the user alone, so only a response carrying neither
    is treated as unresolved.
    """
    respx.post("https://opik.test/api/opik/auth-oauth").mock(
        return_value=httpx.Response(200, json={"user_name": "u"})
    )
    identity = await resolve_oauth_identity(AUTH, _settings())
    assert identity is not None
    assert identity.user_name == "u"
    assert identity.workspace_name is None


@pytest.mark.anyio
@respx.mock
async def test_returns_none_when_the_body_identifies_nobody() -> None:
    respx.post("https://opik.test/api/opik/auth-oauth").mock(
        return_value=httpx.Response(200, json={"resource": "https://opik.test/api/v1/mcp"})
    )
    assert await resolve_oauth_identity(AUTH, _settings()) is None


@pytest.mark.anyio
@respx.mock
async def test_blank_fields_collapse_to_none_not_empty_strings() -> None:
    """An empty string would reach BI as a value; unknown must stay unknown."""
    respx.post("https://opik.test/api/opik/auth-oauth").mock(
        return_value=httpx.Response(
            200, json={"user_name": "u", "workspace_name": "   ", "workspace_id": ""}
        )
    )
    identity = await resolve_oauth_identity(AUTH, _settings())
    assert identity is not None
    assert identity.workspace_name is None
    assert identity.workspace_id is None


@pytest.mark.anyio
@respx.mock
async def test_derives_url_from_comet_url_override() -> None:
    route = respx.post("https://demo.comet.com/opik/api/opik/auth-oauth").mock(
        return_value=httpx.Response(200, json={"workspace_name": "demo-ws"})
    )
    identity = await resolve_oauth_identity(
        AUTH, Settings(opik_url=None, comet_url_override="https://demo.comet.com/")
    )
    assert identity is not None
    assert identity.workspace_name == "demo-ws"
    assert route.called


@pytest.mark.anyio
async def test_returns_none_when_base_unconfigured() -> None:
    # No OPIK_URL and an explicitly empty COMET_URL_OVERRIDE → no base → skip
    # without any network call.
    s = Settings(opik_url=None, comet_url_override="")
    assert await resolve_oauth_identity(AUTH, s) is None
