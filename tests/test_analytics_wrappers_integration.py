from collections.abc import Generator

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.analytics import EVENT_TOOL_CALLED
from opik_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[str, dict[str, str]]]]:
    events: list[tuple[str, dict[str, str]]] = []

    class _R:
        def track_event(self, et: str, props: dict[str, str]) -> None:
            events.append((et, props))

    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: _R())
    yield events


@pytest.mark.anyio
async def test_hello_emits_tool_called(
    recorder: list[tuple[str, dict[str, str]]],
) -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        await client.call_tool("hello", {"name": "world"})

    tool_events = [(et, p) for et, p in recorder if et == EVENT_TOOL_CALLED]
    assert len(tool_events) == 1
    _, props = tool_events[0]
    assert props["tool_name"] == "hello"
    assert props["success"] == "true"
    assert props["name_was_default"] == "true"
