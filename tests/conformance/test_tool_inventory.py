"""Conformance — the exact-six-tools rule.

`tools/list` is part of the public MCP contract. Adding a tool is a
major-version change (every host caches the list); removing one breaks
any agent that has a prompt pinned to it. We pin the set here so an
accidental `@mcp.tool` either ships intentionally with a snapshot
update, or fails CI.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.apps import APP_MIME_TYPE, UI_URI
from opik_mcp.server import mcp

EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "list",
        "write",
        "schema",
        "ask_ollie",
        "run_experiment",
        # `review` is read-with-a-UI: a deliberate seventh tool rather than
        # `_meta.ui` on the generic `read`, which would have opened a panel for
        # every entity — including the ones with no purpose-built view.
        "review",
    }
)

#: Tools that exist for the MCP App iframe, not for the planner. They carry
#: ``_meta.ui.visibility = ["app"]``, so an Apps-aware host keeps them out of the
#: model's tool list — the six-tool rule above is about what the model sees, and
#: these must never appear without that marker.
EXPECTED_APP_TOOLS: frozenset[str] = frozenset({"app_data"})


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
    model_facing = advertised - EXPECTED_APP_TOOLS
    assert model_facing == EXPECTED_TOOLS, (
        f"tool surface drift: advertised={sorted(model_facing)} expected={sorted(EXPECTED_TOOLS)}"
    )
    assert advertised - EXPECTED_TOOLS == EXPECTED_APP_TOOLS, (
        "app-facing tool drift: "
        f"advertised={sorted(advertised - EXPECTED_TOOLS)} expected={sorted(EXPECTED_APP_TOOLS)}"
    )


@pytest.mark.anyio
async def test_app_tools_are_hidden_from_the_model() -> None:
    """An app-facing tool without the visibility marker would land in every
    planner's context — exactly the tool-count creep ADR 0004 D1 guards against."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    for tool in tools.tools:
        ui = (tool.meta or {}).get("ui") or {}
        if tool.name in EXPECTED_APP_TOOLS:
            assert ui.get("visibility") == ["app"], f"{tool.name} is not marked app-only"
        else:
            assert "visibility" not in ui, f"{tool.name} should be visible to the model"


@pytest.mark.anyio
async def test_review_points_at_the_app_and_read_does_not() -> None:
    """`review` owns the panel; `read` stays a pure data call. If the reference
    moves, hosts silently stop rendering — or start rendering everywhere."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
    review_tool = next(t for t in tools.tools if t.name == "review")
    assert ((review_tool.meta or {}).get("ui") or {}).get("resourceUri") == UI_URI
    read_tool = next(t for t in tools.tools if t.name == "read")
    assert "ui" not in (read_tool.meta or {}), "read must stay text-only"
    app = next(r for r in resources.resources if str(r.uri) == UI_URI)
    assert app.mimeType == APP_MIME_TYPE


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
