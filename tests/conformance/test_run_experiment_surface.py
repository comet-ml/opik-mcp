"""Conformance test — `run_experiment` advertise check.

Pins the public MCP contract: `tools/list` over a real session MUST
include `run_experiment`. Drift here means a client's schema cache or
codegen would not see the tool.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_experiment_tool_advertised() -> None:
    """tools/list over the wire MUST include run_experiment."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert "run_experiment" in names, f"run_experiment not advertised; got {names}"


@pytest.mark.anyio
async def test_run_experiment_description_mentions_async_semantics() -> None:
    """Description MUST teach the caller that the tool is fire-and-return.

    Without this, an LLM seeing the result's experiment_ids may wait for
    completion or call the tool repeatedly. The description should point
    at `read` for progress instead.
    """
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    run_tool = next(t for t in tools.tools if t.name == "run_experiment")
    desc = (run_tool.description or "").lower()
    assert "read" in desc, "description must teach the caller to use read() for status"
    assert "fire-and-return" in desc or "does not wait" in desc, (
        "description must signal that the tool does not block on completion"
    )
