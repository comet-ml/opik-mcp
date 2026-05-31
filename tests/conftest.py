import os
from collections.abc import AsyncIterator, Generator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport

# Analytics is disabled by default for the whole test process. Tests that
# exercise analytics opt in explicitly (monkeypatch / respx mocks); leaving it
# on would let the process-wide singleton spawn a daemon worker that phones
# home to stats.comet.com over the real network. Beyond being slow and flaky,
# an in-flight POST from one test can land in a *later* test's ``@respx.mock``
# window and corrupt ``route.calls.last`` assertions. ``setdefault`` so an
# explicit override in the environment still wins.
os.environ.setdefault("OPIK_MCP_ANALYTICS_ENABLED", "false")

# Run the integration suite in dev-token mode so existing tests that assert
# "Bearer wrong" returns 401 continue to pass. The FastMCP
# ``StreamableHTTPSessionManager`` is a process-level singleton so the
# session-scoped ``http_client`` fixture below builds the app exactly once;
# OAuth-passthrough behavior is exercised by unit tests against the
# middleware directly (see ``test_oauth_passthrough_mode.py``). ``setdefault``
# so individual tests / CI jobs can flip to OAuth-passthrough at session start.
os.environ.setdefault("OPIK_MCP_DEV_TOKEN_ENABLED", "true")


@pytest.fixture(autouse=True)
def _reset_analytics_wrappers_state() -> Generator[None]:
    """Reset every process-wide analytics flag between tests.

    Each test file used to reset only the globals it touched, leaving a
    cross-file pollution foot-gun: a new test that calls (e.g.)
    ``_maybe_emit_tools_listed`` without its own autouse fixture would
    inherit ``_tools_listed_fired_processwide=True`` from a previous file
    and silently no-op. Centralising the reset here keeps every test
    independent regardless of which globals it ends up touching.

    Also drops the process-wide ``get_analytics()`` singleton between tests so
    its emit worker (a daemon thread posting over httpx) never survives a test
    boundary. Analytics is disabled by default for the whole test process (see
    the ``OPIK_MCP_ANALYTICS_ENABLED`` default set at the top of this module),
    so the singleton built here never spawns a worker or phones home — but a
    test that explicitly enables analytics still gets a clean singleton each
    time rather than inheriting another test's live worker, whose in-flight
    POST could otherwise land in a *later* test's ``@respx.mock`` window and
    break assertions keyed on ``route.calls.last``.
    """
    from opik_mcp.analytics import reset_analytics_for_tests, transport_probe
    from opik_mcp.analytics.wrappers import (
        _reset_seen_sessions_for_tests,
        _reset_seen_tools_listed_for_tests,
    )

    reset_analytics_for_tests()
    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()
    yield
    reset_analytics_for_tests()
    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()


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
