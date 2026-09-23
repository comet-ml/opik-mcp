# What hosts read besides the schema: a title, the behaviour hints, and a
# description short enough to arrive whole. Claude Code cuts each tool
# description, and the server instructions, at 2,048 characters, silently; the
# Connectors Directory requires a title and the read-only or destructive hint.

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import Tool

from opik_mcp.server import mcp
from tests.conformance.test_tool_inventory import longest_instructions

DESCRIPTION_LIMIT = 2_048
READ_ONLY = frozenset({"read", "list", "schema", "read_skill"})

# Over the limit today, so a host drops their tail. Strict: the test starts
# failing once one fits, which is the cue to take it off this list.
OVER_THE_LIMIT = frozenset({"read", "write", "read_skill"})
_OVER = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="over 2,048 characters today. Passing now? Remove it from OVER_THE_LIMIT.",
)
TOOLS = [
    pytest.param(name, marks=_OVER) if name in OVER_THE_LIMIT else name
    for name in ("read", "list", "write", "schema", "read_skill")
]


async def _tools() -> dict[str, Tool]:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        return {tool.name: tool for tool in (await session.list_tools()).tools}


@pytest.mark.anyio
async def test_every_tool_has_a_title_and_all_hints() -> None:
    for name, tool in (await _tools()).items():
        assert tool.title, f"{name} has no title"
        hints = tool.annotations
        assert hints is not None, f"{name} has no annotations"
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert getattr(hints, hint) is not None, f"{name} leaves {hint} to the host default"


@pytest.mark.anyio
async def test_the_hints_match_what_each_tool_does() -> None:
    tools = await _tools()
    for name in READ_ONLY:
        hints = tools[name].annotations
        assert hints, name
        assert hints.readOnlyHint, name
        assert not hints.destructiveHint, name
    write = tools["write"].annotations
    assert write
    assert not write.readOnlyHint
    assert write.destructiveHint


@pytest.mark.anyio
@pytest.mark.parametrize("name", TOOLS)
async def test_the_description_arrives_whole(name: str) -> None:
    description = (await _tools())[name].description or ""
    assert len(description) <= DESCRIPTION_LIMIT, (
        f"{name}'s description is {len(description)} characters; a host cuts it at "
        f"{DESCRIPTION_LIMIT}. Move detail into schema() or a reference."
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="instructions over 2,048 characters today. Passing now? Remove this marker.",
)
def test_the_instructions_arrive_whole() -> None:
    assert len(longest_instructions()) <= DESCRIPTION_LIMIT
