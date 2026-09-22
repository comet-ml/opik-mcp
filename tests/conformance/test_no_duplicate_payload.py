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
        "these tools declare an outputSchema, so every call sends the answer "
        f"twice: {', '.join(offenders)}"
    )


@pytest.mark.anyio
async def test_a_call_returns_one_copy_of_its_answer() -> None:
    """The half a schema test cannot see: what actually goes over the wire."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        result = await client.call_tool("schema", {"operation": "trace.create"})
    assert result.content, "the text block is the answer"
    assert result.structuredContent is None, "and there is no second copy of it"
