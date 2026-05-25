"""End-to-end: install_tools_listed_emitter replaces the FastMCP handler
and the wrapper emits opik_mcp_tools_listed once per session."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ListToolsRequest

from opik_mcp.analytics import EVENT_TOOLS_LISTED, transport_probe
from opik_mcp.analytics.wrappers import (
    _reset_seen_tools_listed_for_tests,
    install_tools_listed_emitter,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    return r


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    transport_probe.reset_for_tests()
    _reset_seen_tools_listed_for_tests()
    yield
    transport_probe.reset_for_tests()
    _reset_seen_tools_listed_for_tests()


@pytest.mark.anyio
async def test_emitter_wraps_existing_handler(recorder: _Recorder) -> None:
    mcp = FastMCP("test")

    @mcp.tool()
    def hello() -> str:
        return "hi"

    lowlevel = mcp._mcp_server

    # Replace the original handler with a stub that returns a sentinel object.
    # This lets us assert exact identity (`is`) on the wrapper's return — the
    # weaker `hasattr(result, "root")` check accepted any object with that
    # attribute, including a transformed/rebuilt one.
    sentinel = object()

    async def _stub(_req: object) -> object:
        return sentinel

    lowlevel.request_handlers[ListToolsRequest] = _stub  # type: ignore[index]

    install_tools_listed_emitter(mcp)
    assert lowlevel.request_handlers[ListToolsRequest] is not _stub

    req = ListToolsRequest(method="tools/list")
    result = await lowlevel.request_handlers[ListToolsRequest](req)
    assert result is sentinel, "wrapper must return the original handler's value unchanged"

    # The sentinel has no `.root.tools`, so _maybe_emit_tools_listed walked the
    # defensive fallback path. The emit itself MUST still fire — tools_listed
    # counts the listing, not the contents.
    assert any(et == EVENT_TOOLS_LISTED for et, _ in recorder.events)
    assert transport_probe.first_rpc_received() is True


@pytest.mark.anyio
async def test_tools_listed_dedups_per_session(recorder: _Recorder) -> None:
    """Same session: 2 list_tools calls → 1 tools_listed event."""
    mcp = FastMCP("test")

    @mcp.tool()
    def hello() -> str:
        return "hi"

    install_tools_listed_emitter(mcp)
    handler = mcp._mcp_server.request_handlers[ListToolsRequest]

    req = ListToolsRequest(method="tools/list")
    await handler(req)
    await handler(req)
    tools_listed = [e for e in recorder.events if e[0] == EVENT_TOOLS_LISTED]
    # No session context in this test: falls back to process-wide one-shot.
    assert len(tools_listed) == 1


@pytest.mark.anyio
async def test_tools_listed_props_shape(recorder: _Recorder) -> None:
    mcp = FastMCP("test")

    @mcp.tool()
    def hello() -> str:
        return "hi"

    install_tools_listed_emitter(mcp)
    handler = mcp._mcp_server.request_handlers[ListToolsRequest]
    await handler(ListToolsRequest(method="tools/list"))

    _et, props = next(e for e in recorder.events if e[0] == EVENT_TOOLS_LISTED)
    assert "tool_count_bucket" in props
    assert props["tool_count_bucket"] in {"0", "1-10", "11-100", "101-1000", ">1000"}
