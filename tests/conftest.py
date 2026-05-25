from collections.abc import AsyncIterator, Generator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport


@pytest.fixture(autouse=True)
def _reset_analytics_wrappers_state() -> Generator[None]:
    from opik_mcp.analytics.wrappers import _reset_seen_sessions_for_tests

    _reset_seen_sessions_for_tests()
    yield
    _reset_seen_sessions_for_tests()


# Session-scoped HTTP client over the real ASGI app. Shared across test
# modules because the underlying FastMCP `StreamableHTTPSessionManager` is a
# process-level singleton that may only be `.run()`'d once — letting each
# module build its own app raises RuntimeError on the second module.
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    from opik_mcp.server import build_app

    app = build_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
            yield c
