"""Conformance — the exact-six-tools rule (docs/host-conformance.md §1).

`tools/list` is part of the public MCP contract. Adding a tool is a
major-version change (every host caches the list); removing one breaks
any agent that has a prompt pinned to it. We pin the set here so an
accidental `@mcp.tool` either ships intentionally with a snapshot
update, or fails CI.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp

EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "list",
        "write",
        "schema",
        "ask_ollie",
        "run_experiment",
    }
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tools_list_advertises_exactly_the_phase_one_surface() -> None:
    """An accidental `@mcp.tool` would silently expand the public surface;
    a typo on a tool name would silently rename one. Pin both."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    advertised = {t.name for t in tools.tools}
    assert advertised == EXPECTED_TOOLS, (
        f"tool surface drift: advertised={sorted(advertised)} expected={sorted(EXPECTED_TOOLS)}"
    )


@pytest.mark.anyio
async def test_every_tool_has_nonempty_description() -> None:
    """Some strict hosts (Cursor, MCP Inspector strict mode) reject tools
    with no description. A regression that ships an undocumented tool
    would silently disable it on those hosts."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    missing = [t.name for t in tools.tools if not (t.description or "").strip()]
    assert not missing, f"tools missing descriptions: {missing}"
