"""End-to-end: server_shutdown fires on clean exit with handshake flags."""

from __future__ import annotations

import time

import pytest

from opik_mcp import __main__ as main_mod
from opik_mcp.analytics import (
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    transport_probe,
)


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
def _reset() -> None:
    from opik_mcp.config import get_settings
    get_settings.cache_clear()
    transport_probe.reset_for_tests()
    yield
    transport_probe.reset_for_tests()


def _install_recorder(monkeypatch) -> _RecorderClient:
    r = _RecorderClient()
    monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.__main__.get_analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.__main__._build_fallback_analytics_client", lambda: r)
    return r


def test_clean_exit_emits_server_shutdown(monkeypatch) -> None:
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


def test_shutdown_reflects_first_rpc_when_flag_set(monkeypatch) -> None:
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
