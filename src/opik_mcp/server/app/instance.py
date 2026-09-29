from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.config import Settings, get_settings
from opik_mcp.instructions import render_instructions
from opik_mcp.server.tools import register_tools


def build_server(settings: Settings) -> FastMCP[object]:
    # ``instructions`` (ADR 0004 D6) is FastMCP's surface for the MCP
    # InitializeResult.instructions field — hosts that support it inject the
    # blob as system-prompt context once per session. Rendered eagerly so the
    # server logs any settings issues at startup rather than mid-call.
    server = FastMCP("opik-mcp", instructions=render_instructions(settings))
    register_tools(server, settings)
    return server


mcp = build_server(get_settings())
