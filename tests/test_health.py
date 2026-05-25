import httpx
import pytest
import respx

from opik_mcp.config import get_settings

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


@pytest.fixture
def comet_base() -> str:
    return get_settings().comet_url_override.rstrip("/")


@pytest.mark.anyio
async def test_liveness_returns_ok_without_auth(http_client: httpx.AsyncClient) -> None:
    r = await http_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_mcp_path_still_requires_auth(http_client: httpx.AsyncClient) -> None:
    # Regression: exempting /health* must not have leaked the MCP path.
    r = await http_client.post("/mcp", json=INITIALIZE)
    assert r.status_code == 401


@pytest.mark.anyio
async def test_readiness_ok_when_upstream_responds(
    http_client: httpx.AsyncClient, comet_base: str
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(return_value=httpx.Response(200))
        r = await http_client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_readiness_ok_when_upstream_returns_4xx(
    http_client: httpx.AsyncClient, comet_base: str
) -> None:
    # 4xx means TCP+TLS+HTTP stack is alive — readiness only cares about that.
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(return_value=httpx.Response(404))
        r = await http_client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_readiness_503_on_upstream_5xx(
    http_client: httpx.AsyncClient, comet_base: str
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(return_value=httpx.Response(503))
        r = await http_client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": "upstream_5xx"}


@pytest.mark.anyio
async def test_readiness_503_on_timeout(http_client: httpx.AsyncClient, comet_base: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(side_effect=httpx.TimeoutException("boom"))
        r = await http_client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": "timeout"}


@pytest.mark.anyio
async def test_readiness_503_on_connect_error(
    http_client: httpx.AsyncClient, comet_base: str
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(side_effect=httpx.ConnectError("dns"))
        r = await http_client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": "network_error"}


@pytest.mark.anyio
async def test_readiness_does_not_swallow_config_errors(
    http_client: httpx.AsyncClient, comet_base: str
) -> None:
    # UnsupportedProtocol (and InvalidURL) signal a typo in COMET_URL_OVERRIDE.
    # They must NOT silently bucket as network_error and pin the pod
    # not_ready forever — operators need a loud failure (the ASGI server
    # turns the uncaught exception into a 500) so the config bug surfaces.
    with respx.mock(assert_all_called=False) as mock:
        mock.head(comet_base).mock(side_effect=httpx.UnsupportedProtocol("ftp://"))
        with pytest.raises(httpx.UnsupportedProtocol):
            await http_client.get("/health/ready")
