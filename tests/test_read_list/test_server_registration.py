"""Smoke-tests that ``read`` and ``list`` are registered on the FastMCP server.

Resources have been removed entirely (ADR 0004 D1) — verify the server
exposes the tools instead.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_read_and_list_tools_listed() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert "read" in names
    assert "list" in names


@pytest.mark.anyio
async def test_no_resources_advertised() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.list_resources()
    # Resources surface is now empty by design — see ADR 0004 D1.
    assert result.resources == []


@pytest.mark.anyio
async def test_read_tool_schema_advertises_entity_types() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    read_tool = next(t for t in tools.tools if t.name == "read")
    et = read_tool.inputSchema["properties"]["entity_type"]
    # Description should enumerate the readable types
    assert "trace" in et["description"]
    assert "project" in et["description"]
