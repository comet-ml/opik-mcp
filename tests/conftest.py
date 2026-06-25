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
    from opik_mcp.analytics.boot_props import LIFECYCLE_SENTINEL
    from opik_mcp.analytics.wrappers import (
        _reset_seen_sessions_for_tests,
        _reset_seen_tools_listed_for_tests,
    )

    reset_analytics_for_tests()
    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()
    # main() sets this sentinel so the build_app() lifespan skips its own emit.
    # Clear it between tests or a test that calls main() leaves the build_app()
    # lifespan (e.g. the session http_client fixture) permanently muted.
    os.environ.pop(LIFECYCLE_SENTINEL, None)
    yield
    reset_analytics_for_tests()
    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()
    os.environ.pop(LIFECYCLE_SENTINEL, None)


@pytest.fixture(autouse=True)
def _disable_workspace_introspection() -> Generator[None]:
    """Stub OAuth workspace introspection to a no-op by default.

    The ``initialize`` handshake resolves the authorized workspace name by
    POSTing to opik-backend's ``/opik/auth-oauth`` (``server.resolve_workspace_name``).
    Left live, every test that sends a session-less OAuth bearer to ``/mcp`` would
    fire a real network call — slow, flaky, and able to land in another test's
    ``@respx.mock`` window. Disabled by default (mirrors the analytics default
    above); tests that exercise resolution ``monkeypatch.setattr`` this, and the
    ``resolve_workspace_name`` unit tests call the real function directly.

    Uses a standalone ``pytest.MonkeyPatch()`` rather than the ``monkeypatch``
    *fixture* on purpose: depending on that fixture from an autouse fixture pulls
    it into the autouse setup phase ahead of ``_reset_analytics_wrappers_state``,
    inverting teardown order so its ``reset_analytics_for_tests()`` runs while a
    test still has ``get_analytics`` patched to a non-lru_cache stub (AttributeError
    on ``cache_info``). A self-owned instance keeps fixture ordering untouched.
    """

    async def _none(*_args: object, **_kwargs: object) -> None:
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr("opik_mcp.server.resolve_workspace_name", _none)
    try:
        yield
    finally:
        mp.undo()


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
