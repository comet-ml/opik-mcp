"""Conformance for the cost intelligence mode: what its server advertises.

The default surface is pinned by the other files in this directory. This one pins
the second surface, built in-process the way the stdio server builds it.
Snapshots: `UPDATE_SNAPSHOTS=1 uv run pytest tests/conformance/test_cost_intelligence_surface.py`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent, Tool

from opik_mcp.config import Settings, get_settings
from opik_mcp.cost_intelligence.descriptions import GUIDE_NAME
from opik_mcp.instructions import render_instructions
from opik_mcp.read_list.visibility import list_schema_keys
from tests.asserts import assert_answer_equals
from tests.conformance.test_tool_inventory import (
    INSTRUCTIONS_BUDGET_BYTES,
    SURFACE_BUDGET_BYTES,
    surface_report,
)
from tests.cost_intelligence.build import SPEND_WORKSPACE, build_cost_intelligence_server
from tests.factories import make_settings

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "cost_intelligence"
EXPECTED_TOOL_ORDER = ("read", "list", "schema", "read_skill")
EXPECTED_READABLE = (
    "project",
    "span",
    "thread",
    "trace",
)
EXPECTED_LISTABLE = (
    "project",
    "project_metric",
    "span",
    "thread",
    "trace",
)
DROPPED = {
    "read": {"project_id", "project_name"},
    "list": {"dataset_id", "experiment_ids", "prompt_id", "status", "project_id", "project_name"},
}
HIDDEN_WORDS = (
    "dataset",
    "experiment",
    "prompt",
    "agent_insights_issue",
    "test_suite",
    "Diagnostics",
    "write",
    "opik-instrument",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FastMCP[object]:
    return build_cost_intelligence_server(monkeypatch)


async def _tools(server: FastMCP[object]) -> dict[str, Tool]:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await session.initialize()
        return {tool.name: tool for tool in (await session.list_tools()).tools}


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
async def test_the_mode_advertises_exactly_read_list_schema_and_read_skill(
    server: FastMCP[object],
) -> None:
    tools = await _tools(server)
    assert list(tools) == list(EXPECTED_TOOL_ORDER), (
        f"cost intelligence tools: {list(tools)}. Registration is register_tools in "
        "src/opik_mcp/server/tools/__init__.py; this mode has no write."
    )


@pytest.mark.anyio
async def test_the_entity_enums_are_the_modes_visible_types(server: FastMCP[object]) -> None:
    tools = await _tools(server)
    assert tools["read"].inputSchema["properties"]["entity_type"]["enum"] == list(EXPECTED_READABLE)
    assert tools["list"].inputSchema["properties"]["entity_type"]["enum"] == list(EXPECTED_LISTABLE)


@pytest.mark.anyio
async def test_the_schema_tool_offers_only_visible_list_keys(server: FastMCP[object]) -> None:
    tools = await _tools(server)
    keys = tools["schema"].inputSchema["properties"]["operation"]["enum"]
    assert keys == list(list_schema_keys("cost_intelligence"))
    assert keys
    assert all(key.startswith("list.") for key in keys), keys
    assert not any(hidden in key for key in keys for hidden in ("dataset", "experiment", "prompt"))


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(DROPPED))
async def test_arguments_only_hidden_types_take_are_not_advertised(
    server: FastMCP[object], tool: str
) -> None:
    schema = (await _tools(server))[tool].inputSchema
    present = DROPPED[tool] & set(schema["properties"])
    assert not present, f"{tool} still advertises {sorted(present)}"
    assert set(schema.get("required", [])) <= set(schema["properties"])


@pytest.mark.anyio
async def test_no_advertised_text_names_a_hidden_entity_skill_or_write(
    server: FastMCP[object],
) -> None:
    for name, tool in (await _tools(server)).items():
        text = json.dumps([tool.description, tool.inputSchema])
        leaked = [word for word in HIDDEN_WORDS if word.lower() in text.lower()]
        assert not leaked, f"{name} advertises text naming {leaked}"


@pytest.mark.anyio
async def test_the_mode_surface_stays_within_the_budget(server: FastMCP[object]) -> None:
    total, report = surface_report(_advertised(await _tools(server)))
    assert total <= SURFACE_BUDGET_BYTES, (
        f"cost intelligence surface is {total} bytes, over {SURFACE_BUDGET_BYTES}.\n{report}"
    )


def _mode_settings() -> Settings:
    return make_settings(
        comet_workspace="__ai_spend_" + "w" * 29,
        opik_mcp_transport="stdio",
        opik_url="https://www.comet.com/opik/api",
    )


def test_the_modes_instructions_stay_within_budget_and_name_every_tool() -> None:
    text = render_instructions(_mode_settings(), user_email="u" * 40 + "@example.com")
    assert len(text.encode()) <= INSTRUCTIONS_BUDGET_BYTES
    unnamed = [t for t in EXPECTED_TOOL_ORDER if not re.search(rf"(?<!\w){t}(?!\w)", text)]
    assert not unnamed, f"the instructions never name {unnamed}"


@pytest.mark.anyio
async def test_the_handshake_carries_the_modes_instructions(server: FastMCP[object]) -> None:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        result = await session.initialize()
    assert GUIDE_NAME in (result.instructions or "")
    assert SPEND_WORKSPACE in (result.instructions or "")


@pytest.mark.anyio
async def test_every_tool_has_a_title_and_read_only_hints(server: FastMCP[object]) -> None:
    for name, tool in (await _tools(server)).items():
        hints = tool.annotations
        assert tool.title, name
        assert hints is not None, name
        assert hints.readOnlyHint, name
        assert not hints.destructiveHint, name
        assert hints.idempotentHint is not None, name
        assert hints.openWorldHint is not None, name


@pytest.mark.anyio
async def test_every_description_arrives_whole(server: FastMCP[object]) -> None:
    for name, tool in (await _tools(server)).items():
        assert len(tool.description or "") <= 2_048, name


_PROJECT_ID = "00000000-0000-0000-0000-0000000000aa"


def _backend(request: httpx.Request) -> httpx.Response:
    """A workspace with one `claude-code` project and a trace inside it."""
    if request.url.path.endswith("/traces/00000000-0000-0000-0000-00000000abcd"):
        return httpx.Response(
            200,
            json={"id": "00000000-0000-0000-0000-00000000abcd", "project_id": _PROJECT_ID},
        )
    project = {"id": _PROJECT_ID, "name": "claude-code"}
    return httpx.Response(200, json={"content": [project], "total": 1})


ONE_CALL_PER_TOOL: dict[str, dict[str, object]] = {
    "read": {"entity_type": "trace", "id": "00000000-0000-0000-0000-00000000abcd"},
    "list": {"entity_type": "project"},
    "schema": {"operation": "list.trace"},
    "read_skill": {"skill_name": GUIDE_NAME},
}


@pytest.mark.anyio
async def test_the_wire_probe_calls_every_advertised_tool(server: FastMCP[object]) -> None:
    assert set(ONE_CALL_PER_TOOL) == set(await _tools(server))


@pytest.mark.anyio
@pytest.mark.parametrize(("tool", "arguments"), ONE_CALL_PER_TOOL.items())
async def test_a_call_returns_one_copy_of_its_answer(
    server: FastMCP[object], tool: str, arguments: dict[str, object]
) -> None:
    with respx.mock(assert_all_called=False) as backend:
        backend.route(host="opik.test").mock(side_effect=_backend)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            result = await client.call_tool(tool, arguments)
    text = [b.text for b in result.content if isinstance(b, TextContent)]
    assert not result.isError, f"{tool}({arguments}) failed: {text}"
    assert len(result.content) == 1
    assert result.structuredContent is None


@pytest.mark.anyio
@pytest.mark.parametrize("tool", EXPECTED_TOOL_ORDER)
async def test_the_modes_input_schema_matches_its_snapshot(
    server: FastMCP[object], tool: str, tmp_path: Path
) -> None:
    actual = (await _tools(server))[tool].inputSchema
    path = SNAPSHOT_DIR / f"{tool}.json"
    canonical = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if os.getenv("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical, encoding="utf-8")
        pytest.skip(f"snapshot updated: {path.name}")
    if not path.exists():
        pytest.fail(f"missing snapshot {path}; run with UPDATE_SNAPSHOTS=1 to create it.")
    assert_answer_equals(
        actual,
        json.loads(path.read_text(encoding="utf-8")),
        artefact=tmp_path / f"{tool}.schema.diff",
        hint=f"{tool}'s cost intelligence inputSchema differs from {path.name}; rerun with "
        "UPDATE_SNAPSHOTS=1 if intended.",
    )


def test_fastmcp_still_lets_the_mode_narrow_an_advertised_schema(
    server: FastMCP[object],
) -> None:
    """The mode edits `_tool_manager.get_tool(name).parameters`, which is private.
    If a FastMCP upgrade moves it, fix src/opik_mcp/server/tools/mode_surface.py."""
    tool = server._tool_manager.get_tool("list")
    assert tool is not None, "FastMCP's ToolManager.get_tool is gone"
    assert isinstance(tool.parameters, dict), "FastMCP's Tool.parameters is gone"
    assert "entity_type" in tool.parameters["properties"]
    assert get_settings().comet_workspace == SPEND_WORKSPACE


def test_a_dropped_argument_missing_from_the_real_schema_fails_loudly(
    server: FastMCP[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from opik_mcp.cost_intelligence import COST_INTELLIGENCE_MODE
    from opik_mcp.server.tools import mode_surface

    monkeypatch.setitem(mode_surface._DROPPED_ARGUMENTS, "read", ("no_such_argument",))
    with pytest.raises(RuntimeError, match=r"read\.no_such_argument is not in the tool schema"):
        mode_surface.narrow_advertised_schemas(server, COST_INTELLIGENCE_MODE)
