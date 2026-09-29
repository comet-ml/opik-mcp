"""An in-process cost intelligence server, built the way stdio builds it."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from opik_mcp.config import get_settings
from opik_mcp.server.app.instance import build_server

SPEND_WORKSPACE = "__ai_spend_test__"


def build_cost_intelligence_server(monkeypatch: pytest.MonkeyPatch) -> FastMCP[object]:
    monkeypatch.setenv("OPIK_WORKSPACE", SPEND_WORKSPACE)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("OPIK_URL", "https://opik.test/api")
    monkeypatch.setenv("OPIK_API_KEY", "k")
    get_settings.cache_clear()
    return build_server(get_settings())
