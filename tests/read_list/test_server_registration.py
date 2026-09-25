"""Smoke-tests that ``read`` and ``list`` are registered on the FastMCP server.

Opik **entity** resources were removed entirely (ADR 0004 D1) in favour of the
``read`` / ``list`` tools — verify the server exposes the tools instead, and that
no entity has crept back onto the resource surface. Skill files do live there
(OPIK-7472): static, cacheable documents are what resources are for, and they
are not entities. ``tests/conformance/test_skill_resources.py`` owns that
contract; this module only pins that the two surfaces stay separate.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp
from opik_mcp.skills_catalog import SKILLS_URI_PREFIX
from opik_mcp.skills_resources import install_skill_resources

install_skill_resources(mcp)


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
async def test_no_entity_resources_advertised() -> None:
    """Every advertised resource is a skill file. An Opik entity reappearing here
    would mean two ways to read a trace, which ADR 0004 D1 removed on purpose."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.list_resources()
    non_skill = [
        str(r.uri) for r in result.resources if not str(r.uri).startswith(SKILLS_URI_PREFIX)
    ]
    assert non_skill == [], f"non-skill resources advertised: {non_skill}"


@pytest.mark.anyio
async def test_installing_skill_resources_twice_does_not_duplicate_the_listing() -> None:
    """Each installed handler delegates to the one it replaced, so a second
    install would chain onto the first and advertise every skill twice. Both
    startup paths call the installer, and so do several test modules."""
    install_skill_resources(mcp)
    install_skill_resources(mcp)
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.list_resources()
    uris = [str(r.uri) for r in result.resources]
    assert len(uris) == len(set(uris)), "duplicate resources — the installer is not idempotent"


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
