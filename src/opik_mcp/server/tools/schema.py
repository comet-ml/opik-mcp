from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.server.tools.description import described
from opik_mcp.server.tools.hints import READS
from opik_mcp.writes import SCHEMA_TOOL_DESCRIPTION, run_schema
from opik_mcp.writes.schema_tool import SCHEMA_KEYS


def _schema_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {"operation": str(kwargs.get("operation", ""))}


SCHEMA_KEY_ENUM: list[str] = list(SCHEMA_KEYS)


@instrument_tool("schema", props_fn=_schema_props)
async def schema(
    operation: Annotated[
        str,
        Field(
            description=(
                "Write operation whose schema to return, or list.<entity> "
                "(list.trace, list.span, list.thread, list.experiment) for the "
                "filter and sort reference of the list tool."
            ),
            json_schema_extra={"enum": SCHEMA_KEY_ENUM},
        ),
    ],
    ctx: Context[ServerSession, None] | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        await ctx.info(f"schema.called operation={operation}")
    return run_schema(operation=operation)


def register(mcp: FastMCP[object], sentence: str | None = None) -> None:
    mcp.tool(
        description=described(sentence, SCHEMA_TOOL_DESCRIPTION),
        title="Show a write operation's input",
        annotations=READS,
        structured_output=False,
    )(schema)
