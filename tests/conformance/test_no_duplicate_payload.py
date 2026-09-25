"""OPIK-8500 — no tool ships the same answer twice.

FastMCP infers an output schema from a tool's return annotation unless told
not to. Every tool here returns ``str`` or ``dict``, so every tool advertised
one, and the spec then obliges the server to send ``structuredContent``
alongside the text on every call. For the three string tools that second copy
is ``{"result": "<the whole answer again>"}`` — the answer twice, over the
wire, on every call.

Measured over the in-memory transport before this was turned off:
``read_skill('opik')`` sent 8,962 characters of text and 9,439 of structured
content carrying the same string; ``read_skill('opik-instrument')`` 11,115
and 11,497. Invisible in a host, which renders the text block and keeps the
structured copy for programmatic use — so the cost was paid in tokens and
never seen.

The writes error envelope already declines ``structuredContent``, citing
uneven host support, so success and failure disagreed about shape. Turning it
off everywhere settles that the same way the error path already had.
"""

from __future__ import annotations

import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from opik_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_no_tool_advertises_an_output_schema() -> None:
    """A ``{"result": string}`` schema tells a host nothing it did not know
    from the text block, and declaring it is what obliges the duplicate."""
    offenders = [t.name for t in await mcp.list_tools() if t.outputSchema is not None]
    assert not offenders, (
        f"src/opik_mcp/server.py: {', '.join(offenders)} declare an outputSchema, so "
        "every call sends the answer twice. Register each tool with "
        "@mcp.tool(..., structured_output=False): one copy of each answer "
        "(AGENTS.md invariants, OPIK-8500)."
    )


# One successful call per advertised tool. The backend is a single catch-all
# route: what it returns does not matter here, only that the call succeeds and
# how many copies of the answer come back.
_TRACE_ID = "00000000-0000-0000-0000-00000000abcd"
ONE_CALL_PER_TOOL: dict[str, dict[str, object]] = {
    "read": {"entity_type": "trace", "id": _TRACE_ID},
    "list": {"entity_type": "project"},
    "write": {"operation": "trace.update", "data": {"id": _TRACE_ID, "tags_to_add": ["probe"]}},
    "schema": {"operation": "trace.create"},
    "read_skill": {"skill_name": "opik"},
}


@pytest.mark.anyio
async def test_the_wire_probe_calls_every_advertised_tool() -> None:
    advertised = {t.name for t in await mcp.list_tools()}
    assert set(ONE_CALL_PER_TOOL) == advertised, (
        "tests/conformance/test_no_duplicate_payload.py: ONE_CALL_PER_TOOL holds one call "
        f"per advertised tool; missing={sorted(advertised - set(ONE_CALL_PER_TOOL))} "
        f"stale={sorted(set(ONE_CALL_PER_TOOL) - advertised)}."
    )


@pytest.mark.anyio
@pytest.mark.parametrize(("tool", "arguments"), ONE_CALL_PER_TOOL.items())
async def test_a_call_returns_one_copy_of_its_answer(
    monkeypatch: pytest.MonkeyPatch, tool: str, arguments: dict[str, object]
) -> None:
    """The half a schema test cannot see: what actually goes over the wire."""
    monkeypatch.setenv("OPIK_URL", "https://opik.test/api")
    monkeypatch.setenv("OPIK_API_KEY", "k")
    monkeypatch.setenv("OPIK_WORKSPACE", "ws")
    with respx.mock(assert_all_called=False) as backend:
        backend.route(host="opik.test").respond(200, json={"content": [], "total": 0})
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            result = await client.call_tool(tool, arguments)
    text = [block.text for block in result.content if isinstance(block, TextContent)]
    assert not result.isError, f"{tool}({arguments}) failed, so it proves nothing: {text}"
    assert len(result.content) == 1, (
        f"{tool} answered in {len(result.content)} content blocks; one copy of each answer."
    )
    assert result.structuredContent is None, (
        f"{tool} sent structuredContent, a second copy of its answer. Register it in "
        "src/opik_mcp/server.py with structured_output=False (AGENTS.md invariants)."
    )
