"""Smoke-tests that ``read`` and ``list`` are registered on the FastMCP server.

Entity resources were removed entirely (ADR 0004 D1) — entities are read through
tools. The one resource that remains is the MCP App document, which is UI, not an
entity mirror; pin that so the old "no resources" rule can't quietly come back as
"resources for entities".
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.apps import UI_URI
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
async def test_only_the_app_resource_is_advertised() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.list_resources()
    assert [str(r.uri) for r in result.resources] == [UI_URI]


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
    # thread is advertised in both the read enum and as a read param surface
    assert "thread" in et["enum"]
    assert "project_id" in read_tool.inputSchema["properties"]


@pytest.mark.anyio
async def test_list_tool_schema_advertises_thread() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    list_tool = next(t for t in tools.tools if t.name == "list")
    assert "thread" in list_tool.inputSchema["properties"]["entity_type"]["enum"]
