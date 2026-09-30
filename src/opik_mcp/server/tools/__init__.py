"""The MCP tools. ``register_tools`` sets the order ``tools/list`` returns them in."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.features.toggles import FeatureToggles
from opik_mcp.server.tools import list as list_tool
from opik_mcp.server.tools import read, read_skill, schema, write
from opik_mcp.server.tools.feature_surface import extend_advertised_schemas


def register_tools(mcp: FastMCP[object], toggles: FeatureToggles) -> None:
    # A feature's sentence goes on at registration, through FastMCP's own
    # ``description`` argument; only the advertised enums need patching after.
    sentence = dict(toggles.tool_sentences)
    # tests/conformance/test_tool_inventory.py pins this order.
    read.register(mcp, sentence.get("read"))
    list_tool.register(mcp, sentence.get("list"))
    write.register(mcp, sentence.get("write"))
    schema.register(mcp, sentence.get("schema"))
    read_skill.register(mcp, sentence.get("read_skill"))
    extend_advertised_schemas(mcp, toggles)
