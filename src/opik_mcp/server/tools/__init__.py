"""The MCP tools. ``register_tools`` sets the order ``tools/list`` returns them in."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.config import Settings
from opik_mcp.server.tools import list as list_tool
from opik_mcp.server.tools import read, read_skill, schema, write
from opik_mcp.server.tools.feature_surface import extend_advertised_schemas


def register_tools(mcp: FastMCP[object], settings: Settings) -> None:
    # tests/conformance/test_tool_inventory.py pins this order.
    read.register(mcp)
    list_tool.register(mcp)
    write.register(mcp)
    schema.register(mcp)
    read_skill.register(mcp)
    extend_advertised_schemas(mcp, settings)
