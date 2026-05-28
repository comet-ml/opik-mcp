"""End-to-end: install_tools_listed_emitter replaces the FastMCP handler
and the wrapper emits opik_mcp_tools_listed once per session."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import request_ctx
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

    lowlevel.request_handlers[ListToolsRequest] = _stub  # type: ignore[assignment]

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
    # Session-context block: stamped on every per-call event so BI can
    # segment tools_listed on the same dimensions as tool_called /
    # ask_ollie_completed without joining back to session_initialized.
    # No session in this test path → host falls back to defaults but the
    # env keys are always present.
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"
    for key in ("is_ci", "is_container", "launch_method", "install_id_freshly_generated"):
        assert key in props, f"call_context_props key {key!r} missing from tools_listed"


@pytest.mark.anyio
async def test_tools_listed_stamps_known_host_from_request_ctx(recorder: _Recorder) -> None:
    """When the lowlevel ``request_ctx`` carries a real session with a known
    ``clientInfo`` (Claude Code, Cursor, …), the wrapper must surface the
    bucketed host on the event so dashboards can split tools_listed by host.

    This is the hot path in production: every tools/list RPC runs inside a
    request context with ``ctx.session`` populated. The no-session test
    above exercises the defensive fallback; this one pins the contract for
    the real shape so a regression that drops the ContextVar read would
    silently collapse every host into ``"other"``.
    """
    mcp = FastMCP("test")

    @mcp.tool()
    def hello() -> str:
        return "hi"

    install_tools_listed_emitter(mcp)
    handler = mcp._mcp_server.request_handlers[ListToolsRequest]

    client_info = SimpleNamespace(name="claude-code", version="1.2.3")
    params = SimpleNamespace(
        clientInfo=client_info, protocolVersion="2025-06-01", capabilities=None
    )
    session = SimpleNamespace(client_params=params)
    ctx = SimpleNamespace(session=session)

    token = request_ctx.set(ctx)  # type: ignore[arg-type]
    try:
        await handler(ListToolsRequest(method="tools/list"))
    finally:
        request_ctx.reset(token)

    _et, props = next(e for e in recorder.events if e[0] == EVENT_TOOLS_LISTED)
    assert props["mcp_host"] == "claude-code"
    assert props["host_llm_family"] == "anthropic"
    # Env-cohort keys still present so the schema doesn't drift between paths.
    for key in ("is_ci", "is_container", "launch_method", "install_id_freshly_generated"):
        assert key in props
