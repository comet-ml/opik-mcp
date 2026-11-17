"""Conformance for ``send_feedback``: what a server with analytics on advertises.

The default test process has analytics off, so ``opik_mcp.server.mcp`` shows the
five-tool surface. Production defaults to analytics on, which adds this tool;
this file pins that surface, and the budget measures it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import Tool

from tests.asserts import assert_answer_equals
from tests.conformance.test_tool_annotations import DESCRIPTION_LIMIT
from tests.conformance.test_tool_inventory import (
    EXPECTED_TOOL_ORDER,
    SURFACE_BUDGET_BYTES,
    longest_instructions,
    surface_report,
)
from tests.feedback.build import build_feedback_server

SNAPSHOT = Path(__file__).parent / "snapshots" / "send_feedback.json"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server() -> FastMCP[object]:
    return build_feedback_server()


async def _tools(server: FastMCP[object]) -> list[Tool]:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await session.initialize()
        return (await session.list_tools()).tools


@pytest.mark.anyio
async def test_feedback_adds_one_tool_at_the_end(server: FastMCP[object]) -> None:
    sent = [tool.name for tool in await _tools(server)]

    assert sent == [*EXPECTED_TOOL_ORDER, "send_feedback"], (
        f"tools/list with analytics on sent {sent}. extra_tools in "
        "src/opik_mcp/features/contributions.py adds send_feedback after the five."
    )


@pytest.mark.anyio
async def test_the_surface_with_feedback_stays_within_budget(server: FastMCP[object]) -> None:
    total, report = surface_report(
        (
            tool.name,
            tool.description or "",
            tool.inputSchema,
            {
                "title": tool.title,
                "annotations": tool.annotations.model_dump(exclude_none=True)
                if tool.annotations
                else None,
            },
        )
        for tool in await _tools(server)
    )

    assert total <= SURFACE_BUDGET_BYTES, (
        f"the surface with send_feedback is {total} bytes, over the "
        f"{SURFACE_BUDGET_BYTES}-byte budget in tests/conformance/test_tool_inventory.py.\n"
        f"{report}"
    )


@pytest.mark.anyio
async def test_the_hints_say_it_sends_data_out_and_changes_nothing(
    server: FastMCP[object],
) -> None:
    [tool] = [tool for tool in await _tools(server) if tool.name == "send_feedback"]
    hints = tool.annotations

    assert hints is not None
    assert hints.title == tool.title
    assert (
        hints.readOnlyHint,
        hints.destructiveHint,
        hints.idempotentHint,
        hints.openWorldHint,
    ) == (False, False, False, True)
    assert len(tool.description or "") <= DESCRIPTION_LIMIT


@pytest.mark.anyio
async def test_the_input_schema_matches_its_snapshot(
    server: FastMCP[object], tmp_path: Path
) -> None:
    [tool] = [tool for tool in await _tools(server) if tool.name == "send_feedback"]

    if os.getenv("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(json.dumps(tool.inputSchema, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"snapshot updated: {SNAPSHOT.name}")
    assert_answer_equals(
        tool.inputSchema,
        json.loads(SNAPSHOT.read_text()),
        artefact=tmp_path / "send_feedback.schema.diff",
        hint=(
            "send_feedback's inputSchema differs from its snapshot. If the change is "
            "intentional, rerun with UPDATE_SNAPSHOTS=1 and commit the snapshot."
        ),
    )


def test_the_instructions_name_the_tool_when_it_is_on() -> None:
    """A host that defers tools/list learns the tool's name only from this text."""
    assert "send_feedback" in longest_instructions()
