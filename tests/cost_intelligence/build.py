"""An in-process AI Spend server, built the way stdio builds it, and a fake
feature entity for the tests that need a type the feature adds."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from opik_mcp.config import AI_SPEND_FEATURE, get_settings
from opik_mcp.read_list.handler import EntityHandler, Vocabulary
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.server.app.instance import build_server

SPEND_WORKSPACE = "__ai_spend_test__"
FAKE_TYPE = "feature_probe"


async def _fetch(client: object, entity_id: str, **_: object) -> dict[str, object]:
    return {"id": entity_id}


async def _list(client: object, **_: object) -> dict[str, object]:
    return {"content": [], "total": 0}


def add_fake_feature_entity(monkeypatch: pytest.MonkeyPatch) -> EntityHandler:
    """Register a readable, listable entity that only the AI Spend feature turns on."""
    vocabulary = Vocabulary(name=FAKE_TYPE, filter_fields={"name": "string"})
    handler = EntityHandler(
        entity_type=FAKE_TYPE,
        fetch_fn=_fetch,
        list_fn=_list,
        vocabularies=(vocabulary,),
        feature=AI_SPEND_FEATURE,
        description="A test entity behind the AI Spend feature.",
    )
    monkeypatch.setitem(ENTITY_REGISTRY, FAKE_TYPE, handler)
    monkeypatch.setitem(VOCABULARIES, FAKE_TYPE, vocabulary)
    return handler


def build_cost_intelligence_server(monkeypatch: pytest.MonkeyPatch) -> FastMCP[object]:
    monkeypatch.setenv("OPIK_WORKSPACE", SPEND_WORKSPACE)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("OPIK_URL", "https://opik.test/api")
    monkeypatch.setenv("OPIK_API_KEY", "k")
    get_settings.cache_clear()
    return build_server(get_settings())
