"""The MCP server: ``mcp`` with its tools registered, and ``build_app`` for HTTP.

``opik_mcp.server:build_app`` is the uvicorn factory path.
"""

from __future__ import annotations

from opik_mcp.server.app.factory import build_app
from opik_mcp.server.app.instance import mcp

__all__ = ["build_app", "mcp"]
