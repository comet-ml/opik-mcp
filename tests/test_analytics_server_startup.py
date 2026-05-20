import pytest

from opik_mcp import __main__ as main_mod
from opik_mcp.analytics import EVENT_SERVER_STARTED


class _RecorderClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))

    def close(self) -> None:
        pass


def test_main_emits_server_started_then_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecorderClient()
    monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)

    run_calls: list[str] = []

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            run_calls.append(transport)

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    from opik_mcp.config import get_settings

    get_settings.cache_clear()

    main_mod.main()

    assert run_calls == ["stdio"]
    event_types = [e[0] for e in recorder.events]
    assert EVENT_SERVER_STARTED in event_types
    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["transport"] == "stdio"
    assert started["analytics_enabled"] == "true"
    assert started["has_workspace"] in {"true", "false"}
    assert started["has_api_key"] in {"true", "false"}
    assert started["has_default_project"] in {"true", "false"}
