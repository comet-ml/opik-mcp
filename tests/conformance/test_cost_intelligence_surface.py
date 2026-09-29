"""Conformance for the AI Spend workspace: what its server advertises.

The default surface is pinned by the other files in this directory. This one pins
what the AI Spend feature may change on it: only additions, on a server built
in-process the way stdio builds it, with a fake entity behind the feature.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import Tool

from opik_mcp.config import get_settings
from opik_mcp.cost_intelligence.feature import (
    INSTRUCTIONS_PARAGRAPH,
    LIST_SENTENCE,
    READ_SENTENCE,
)
from opik_mcp.instructions import render_instructions
from opik_mcp.read_list.registry import LISTABLE_TYPES, READABLE_TYPES
from opik_mcp.server import mcp as default_mcp
from tests.conformance.test_tool_annotations import DESCRIPTION_LIMIT
from tests.conformance.test_tool_inventory import (
    EXPECTED_TOOL_ORDER,
    INSTRUCTIONS_BUDGET_BYTES,
    SURFACE_BUDGET_BYTES,
    longest_instructions,
    surface_report,
)
from tests.cost_intelligence.build import (
    FAKE_TYPE,
    SPEND_WORKSPACE,
    add_fake_feature_entity,
    build_cost_intelligence_server,
)
from tests.factories import make_settings

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
SENTENCES = {"read": READ_SENTENCE, "list": LIST_SENTENCE}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FastMCP[object]:
    add_fake_feature_entity(monkeypatch)
    return build_cost_intelligence_server(monkeypatch)


async def _tools(server: FastMCP[object]) -> dict[str, Tool]:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await session.initialize()
        return {tool.name: tool for tool in (await session.list_tools()).tools}


def _snapshot(tool: str) -> dict[str, dict[str, dict[str, object]]]:
    return cast(
        "dict[str, dict[str, dict[str, object]]]",
        json.loads((SNAPSHOT_DIR / f"{tool}.json").read_text("utf-8")),
    )


def _advertised(
    tools: dict[str, Tool],
) -> list[tuple[str, str, dict[str, object], dict[str, object]]]:
    return [
        (
            t.name,
            t.description or "",
            t.inputSchema,
            {
                "title": t.title,
                "annotations": t.annotations.model_dump(exclude_none=True)
                if t.annotations
                else None,
            },
        )
        for t in tools.values()
    ]


@pytest.mark.anyio
async def test_the_workspace_advertises_the_five_default_tools_in_the_default_order(
    server: FastMCP[object],
) -> None:
    tools = await _tools(server)
    assert list(tools) == list(EXPECTED_TOOL_ORDER), (
        f"AI Spend tools: {list(tools)}. Nothing is hidden or added by tool: "
        "register_tools in src/opik_mcp/server/tools/__init__.py registers the default five."
    )


@pytest.mark.anyio
async def test_the_entity_enums_are_the_default_plus_the_features_types(
    server: FastMCP[object],
) -> None:
    tools = await _tools(server)
    read = tools["read"].inputSchema["properties"]["entity_type"]
    listed = tools["list"].inputSchema["properties"]["entity_type"]
    assert read["enum"] == sorted({*READABLE_TYPES, FAKE_TYPE})
    assert listed["enum"] == sorted({*LISTABLE_TYPES, FAKE_TYPE})
    assert read["description"] == f"One of: {', '.join(read['enum'])}."
    assert listed["description"] == f"One of: {', '.join(listed['enum'])}."


@pytest.mark.anyio
async def test_the_schema_operation_enum_is_the_default_plus_the_features_list_keys(
    server: FastMCP[object],
) -> None:
    keys = (await _tools(server))["schema"].inputSchema["properties"]["operation"]["enum"]
    default = cast("list[str]", _snapshot("schema")["properties"]["operation"]["enum"])
    assert keys == [*default, f"list.{FAKE_TYPE}"]


@pytest.mark.anyio
@pytest.mark.parametrize("tool", EXPECTED_TOOL_ORDER)
async def test_every_other_argument_is_what_the_default_snapshot_says(
    server: FastMCP[object], tool: str
) -> None:
    actual = json.loads(json.dumps((await _tools(server))[tool].inputSchema))
    expected = _snapshot(tool)
    for schema in (actual, expected):
        properties = schema["properties"]
        properties.get("entity_type", {}).pop("enum", None)
        properties.get("entity_type", {}).pop("description", None)
        properties.get("operation", {}).pop("enum", None)
    assert actual == expected, (
        f"{tool}'s AI Spend input schema differs from the default snapshot beyond the added "
        "names. The feature may only add: see src/opik_mcp/server/tools/feature_surface.py."
    )


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(SENTENCES))
async def test_the_added_sentence_leads_the_tool_description(
    server: FastMCP[object], tool: str
) -> None:
    default = (await _tools(default_mcp))[tool].description
    text = (await _tools(server))[tool].description or ""
    assert text == f"{SENTENCES[tool]} {default}"


@pytest.mark.anyio
async def test_the_other_tool_descriptions_are_the_default(server: FastMCP[object]) -> None:
    default = await _tools(default_mcp)
    tools = await _tools(server)
    for name in ("write", "schema", "read_skill"):
        assert tools[name].description == default[name].description


@pytest.mark.anyio
async def test_the_list_description_still_fits_the_host_limit(server: FastMCP[object]) -> None:
    text = (await _tools(server))["list"].description or ""
    assert len(text) <= DESCRIPTION_LIMIT, (
        f"list is {len(text)} characters in an AI Spend workspace, over the host's "
        f"{DESCRIPTION_LIMIT}. Shorten LIST_SENTENCE in "
        "src/opik_mcp/cost_intelligence/feature.py."
    )


@pytest.mark.anyio
async def test_the_surface_stays_within_the_budget(server: FastMCP[object]) -> None:
    total, report = surface_report(_advertised(await _tools(server)))
    assert total <= SURFACE_BUDGET_BYTES, (
        f"AI Spend surface is {total} bytes, over {SURFACE_BUDGET_BYTES}.\n{report}"
    )


def spend_instructions() -> str:
    settings = make_settings(
        comet_workspace="__ai_spend_" + "w" * 29,
        opik_mcp_transport="stdio",
        opik_url="https://www.comet.com/opik/api",
    )
    return render_instructions(settings, user_email="u" * 40 + "@example.com")


def test_the_spend_instructions_add_only_the_paragraph_and_stay_within_budget() -> None:
    text = spend_instructions().encode()
    added = len(f"\n{INSTRUCTIONS_PARAGRAPH}\n".encode())
    assert len(text) == len(longest_instructions().encode()) + added
    assert len(text) <= INSTRUCTIONS_BUDGET_BYTES


@pytest.mark.anyio
async def test_the_handshake_carries_the_paragraph(server: FastMCP[object]) -> None:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        result = await session.initialize()
    assert INSTRUCTIONS_PARAGRAPH in (result.instructions or "")
    assert SPEND_WORKSPACE in (result.instructions or "")


def test_fastmcp_still_lets_the_feature_extend_an_advertised_schema(
    server: FastMCP[object],
) -> None:
    """The feature edits `_tool_manager.get_tool(name).parameters`, which is private.
    If a FastMCP upgrade moves it, fix src/opik_mcp/server/tools/feature_surface.py."""
    tool = server._tool_manager.get_tool("list")
    assert tool is not None, "FastMCP's ToolManager.get_tool is gone"
    assert isinstance(tool.parameters, dict), "FastMCP's Tool.parameters is gone"
    assert "entity_type" in tool.parameters["properties"]
    assert get_settings().comet_workspace == SPEND_WORKSPACE


def test_a_missing_enum_fails_loudly(server: FastMCP[object]) -> None:
    from opik_mcp.server.tools.feature_surface import extend_advertised_schemas

    tool = server._tool_manager.get_tool("read")
    assert tool is not None
    del tool.parameters["properties"]["entity_type"]["enum"]
    with pytest.raises(RuntimeError, match=r"read\.entity_type has no enum"):
        extend_advertised_schemas(server, get_settings())


@pytest.mark.anyio
async def test_extending_the_surface_twice_changes_nothing(server: FastMCP[object]) -> None:
    from opik_mcp.server.tools.feature_surface import extend_advertised_schemas

    once = _advertised(await _tools(server))
    extend_advertised_schemas(server, get_settings())
    assert _advertised(await _tools(server)) == once, (
        "a second extend_advertised_schemas call changed the surface: keep it idempotent "
        "in src/opik_mcp/server/tools/feature_surface.py."
    )
