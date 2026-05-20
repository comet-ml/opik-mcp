import httpx
import pytest
import respx

from opik_mcp.comet_client import (
    CometAuthError,
    CometClient,
    CometProtocolError,
    OllieNotEnabledError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_discover_pod_happy_path() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        route = mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(
                200,
                json={"computeURL": "https://pod.test", "enabled": True},
                headers={"Set-Cookie": "PPAUTH=abc123; Path=/; HttpOnly"},
            )
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        result = await client.discover_pod("workspace1")

    assert result.compute_url == "https://pod.test"
    assert result.ppauth == "abc123"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "key"
    assert sent.headers["comet-workspace"] == "workspace1"


@pytest.mark.anyio
async def test_discover_pod_401_raises_auth_error() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        with pytest.raises(CometAuthError):
            await client.discover_pod("ws")


@pytest.mark.anyio
async def test_discover_pod_enabled_false_raises() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(200, json={"computeURL": "", "enabled": False})
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        with pytest.raises(OllieNotEnabledError):
            await client.discover_pod("ws")


@pytest.mark.anyio
async def test_discover_pod_missing_ppauth_raises() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(
                200,
                json={"computeURL": "https://pod.test", "enabled": True},
            )
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        with pytest.raises(CometProtocolError, match="PPAUTH"):
            await client.discover_pod("ws")


@pytest.mark.anyio
async def test_discover_pod_parses_samesite_secure_cookie() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(
                200,
                json={"computeURL": "https://pod.test", "enabled": True},
                headers={
                    "Set-Cookie": "PPAUTH=tok; Path=/; Secure; SameSite=Strict; HttpOnly",
                },
            )
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        result = await client.discover_pod("ws")
    assert result.ppauth == "tok"


@pytest.mark.anyio
async def test_discover_pod_trims_trailing_slash() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(
                200,
                json={"computeURL": "https://pod.test/", "enabled": True},
                headers={"Set-Cookie": "PPAUTH=zzz; Path=/"},
            )
        )
        client = CometClient(base_url="https://comet.test/", api_key="key")
        result = await client.discover_pod("ws")
    assert result.compute_url == "https://pod.test"


@pytest.mark.anyio
async def test_discover_pod_strips_panel_url_suffix() -> None:
    with respx.mock(base_url="https://comet.test") as mock:
        mock.get("/api/opik/ollie/compute-api-key").mock(
            return_value=httpx.Response(
                200,
                json={
                    "computeURL": "https://comet.test/pp/ollie-abc-123/api/get-python-panel-url",
                    "enabled": True,
                },
                headers={"Set-Cookie": "PPAUTH=tok; Path=/"},
            )
        )
        client = CometClient(base_url="https://comet.test", api_key="key")
        result = await client.discover_pod("ws")
    assert result.compute_url == "https://comet.test/pp/ollie-abc-123"
