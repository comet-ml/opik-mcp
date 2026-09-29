"""The MCP tools. ``register_tools`` sets the order ``tools/list`` returns them in."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import DEFAULT_MODE, mode_of
from opik_mcp.server.tools import list as list_tool
from opik_mcp.server.tools import read, read_skill, schema, write
from opik_mcp.server.tools.mode_surface import narrow_advertised_schemas


def register_tools(mcp: FastMCP[object], settings: Settings) -> None:
    mode = mode_of(settings)
    # tests/conformance/test_tool_inventory.py pins this order.
    read.register(mcp, mode)
    list_tool.register(mcp, mode)
    if mode == DEFAULT_MODE:
        write.register(mcp)
    schema.register(mcp, mode)
    read_skill.register(mcp, mode)
    if mode != DEFAULT_MODE:
        narrow_advertised_schemas(mcp, mode)
