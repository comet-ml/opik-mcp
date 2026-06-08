"""End-to-end: server_shutdown fires on clean exit with handshake flags."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from opik_mcp import __main__ as main_mod
from opik_mcp.analytics import (
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    EVENT_STARTUP_ERROR,
    transport_probe,
)


@contextlib.asynccontextmanager
async def _noop_inner(app: Any) -> AsyncIterator[None]:
    """Stand-in for FastMCP's session_manager.run() — never touches the
    process-wide StreamableHTTPSessionManager singleton (which may only run once),
    so these tests must NOT call build_app()."""
    yield


def _install_server_recorder(monkeypatch: pytest.MonkeyPatch) -> _RecorderClient:
    """Redirect the analytics calls the build_app() lifespan makes (it uses the
    module-level track_event / get_analytics in opik_mcp.server)."""
    r = _RecorderClient()
    monkeypatch.setattr("opik_mcp.server.track_event", lambda et, p: r.track_event(et, p))
    monkeypatch.setattr("opik_mcp.server.get_analytics", lambda: r)
    return r


class _RecorderClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []
        self.flush_calls: list[float] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))

    def flush(self, deadline_s: float = 2.0) -> None:
        self.flush_calls.append(deadline_s)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    from opik_mcp.config import get_settings

    get_settings.cache_clear()
    transport_probe.reset_for_tests()
    yield
    get_settings.cache_clear()
    transport_probe.reset_for_tests()


def _install_recorder(monkeypatch: pytest.MonkeyPatch) -> _RecorderClient:
    r = _RecorderClient()
    monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.__main__.get_analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.__main__._build_fallback_analytics_client", lambda: r)
    return r


def test_clean_exit_emits_server_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            return None

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    main_mod.main()

    event_types = [e[0] for e in recorder.events]
    assert EVENT_SERVER_STARTED in event_types
    assert EVENT_SERVER_SHUTDOWN in event_types

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["reason"] == "clean_exit"
    assert props["lifespan_seconds_bucket"] in {"<5s", "5-60s", "1-10m", "10-60m", "1-24h", ">24h"}
    assert props["first_rpc_received"] == "false"
    assert props["session_reached"] == "false"


def test_shutdown_reflects_first_rpc_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            transport_probe.mark_first_rpc()
            transport_probe.mark_session_reached()

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["first_rpc_received"] == "true"
    assert props["session_reached"] == "true"


def test_keyboard_interrupt_emits_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_recorder(monkeypatch)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(KeyboardInterrupt):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["reason"] == "keyboard_interrupt"
    # KI is user-initiated, not a crash — startup_error must not fire.
    assert EVENT_STARTUP_ERROR not in [et for et, _ in recorder.events]


def test_transport_crash_emits_shutdown_with_reason_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install_recorder(monkeypatch)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise OSError("address already in use")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(OSError):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["reason"] == "transport_error"


# Boot props that __main__ spreads explicitly into server_started's properties
# dict (so they appear in the recorder, which bypasses _build_event). Note:
# installation_type is NOT here — it comes from _build_event's common block,
# which the recorder bypasses; its on-every-event presence is covered by
# tests/test_analytics_client_build_event.py::test_installation_type_in_common_block.
_BOOT_PROP_KEYS = (
    "oauth_configured",
    "resource_uri_scheme",
    "dns_rebinding_protection",
    "allowed_hosts_is_default",
    "auth_mode",
)


def test_server_started_carries_lifecycle_source_main_and_boot_props(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import get_args

    from opik_mcp.analytics.events import AuthMode, ResourceUriScheme

    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            return None

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    main_mod.main()

    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["lifecycle_source"] == "main"
    for key in _BOOT_PROP_KEYS:
        assert key in started, f"server_started missing boot prop {key!r}"
    assert started["auth_mode"] in get_args(AuthMode)
    assert started["resource_uri_scheme"] in get_args(ResourceUriScheme)


def test_server_shutdown_carries_lifecycle_source_main(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            return None

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["lifecycle_source"] == "main"


def test_server_started_pure_oauth_reports_auth_mode_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure-OAuth deployment (AS configured, no static key): server_started must
    report auth_mode='oauth' from collect_boot_props — NOT the contextvar
    fallback 'none' (there is no request in flight at boot)."""
    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            return None

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://as.example.com")

    main_mod.main()

    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["auth_mode"] == "oauth"


def test_transport_crash_startup_error_carries_oauth_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install_recorder(monkeypatch)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise OSError("address already in use")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(OSError):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    # oauth_configured is passed explicitly (recorder bypasses common);
    # installation_type rides on the common block (see _build_event tests).
    assert props["oauth_configured"] in {"true", "false"}


def test_sys_exit_emits_shutdown_with_reason_sys_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.exit() inside the transport path must still record shutdown.

    The insecure-token guard in __main__._run_transport calls sys.exit(1); BI
    needs the matching shutdown event to close the start/stop funnel — without
    this arm, sys.exit traffic would look like a "missing shutdown" anomaly.
    """
    recorder = _install_recorder(monkeypatch)

    class _ExitingMcp:
        def run(self, *, transport: str) -> None:
            raise SystemExit(1)

    monkeypatch.setattr("opik_mcp.server.mcp", _ExitingMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(SystemExit):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert props["reason"] == "sys_exit"
    # SystemExit is a deliberate exit, not a crash — startup_error must not fire.
    assert EVENT_STARTUP_ERROR not in [et for et, _ in recorder.events]


# --- build_app() composed lifespan (GAP#1: hosted --factory boot) --------- #


def test_lifespan_emits_started_and_shutdown_when_not_owned_by_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from opik_mcp import server
    from opik_mcp.analytics.boot_props import LIFECYCLE_SENTINEL
    from opik_mcp.config import get_settings

    monkeypatch.delenv(LIFECYCLE_SENTINEL, raising=False)
    recorder = _install_server_recorder(monkeypatch)
    settings = get_settings()

    composed = server._make_composed_lifespan(_noop_inner, settings, {})

    async def _drive() -> None:
        async with composed(None):
            pass

    asyncio.run(_drive())

    ets = [et for et, _ in recorder.events]
    assert EVENT_SERVER_STARTED in ets
    assert EVENT_SERVER_SHUTDOWN in ets
    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["lifecycle_source"] == "lifespan"
    shut = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert shut["lifecycle_source"] == "lifespan"
    assert shut["reason"] == "clean_exit"
    # flush must be drained off the event loop on shutdown.
    assert recorder.flush_calls


def test_lifespan_skips_emit_when_owned_by_main(monkeypatch: pytest.MonkeyPatch) -> None:

    from opik_mcp import server
    from opik_mcp.analytics.boot_props import LIFECYCLE_SENTINEL
    from opik_mcp.config import get_settings

    monkeypatch.setenv(LIFECYCLE_SENTINEL, "1")
    recorder = _install_server_recorder(monkeypatch)
    settings = get_settings()

    composed = server._make_composed_lifespan(_noop_inner, settings, {})

    async def _drive() -> None:
        async with composed(None):
            pass

    asyncio.run(_drive())

    ets = [et for et, _ in recorder.events]
    assert EVENT_SERVER_STARTED not in ets
    assert EVENT_SERVER_SHUTDOWN not in ets


def test_lifespan_shutdown_reason_transport_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from opik_mcp import server
    from opik_mcp.analytics.boot_props import LIFECYCLE_SENTINEL
    from opik_mcp.config import get_settings

    monkeypatch.delenv(LIFECYCLE_SENTINEL, raising=False)
    recorder = _install_server_recorder(monkeypatch)
    settings = get_settings()

    composed = server._make_composed_lifespan(_noop_inner, settings, {})

    async def _drive() -> None:
        async with composed(None):
            raise RuntimeError("simulated transport crash")

    with pytest.raises(RuntimeError):
        asyncio.run(_drive())

    shut = next(p for et, p in recorder.events if et == EVENT_SERVER_SHUTDOWN)
    assert shut["reason"] == "transport_error"
    assert shut["lifecycle_source"] == "lifespan"


def test_lifespan_started_pure_oauth_reports_auth_mode_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from opik_mcp import server
    from opik_mcp.analytics.boot_props import LIFECYCLE_SENTINEL
    from opik_mcp.config import get_settings

    monkeypatch.delenv(LIFECYCLE_SENTINEL, raising=False)
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://as.example.com")
    recorder = _install_server_recorder(monkeypatch)
    settings = get_settings()

    composed = server._make_composed_lifespan(_noop_inner, settings, {})

    async def _drive() -> None:
        async with composed(None):
            pass

    asyncio.run(_drive())

    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["auth_mode"] == "oauth"
