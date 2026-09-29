"""Extend the advertised tools with what a workspace's features add.

The tool functions are written once, for the default surface. A feature only
adds: entity types, their ``schema`` keys and one sentence per description.
Call-time gating (``read_list.visibility``) is what refuses; this only changes
what the host is told.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.cost_intelligence.descriptions import (
    LIST_SENTENCE,
    READ_SENTENCE,
)
from opik_mcp.read_list.visibility import added_listable, added_readable, added_schema_keys

_SENTENCES = {
    "read": READ_SENTENCE,
    "list": LIST_SENTENCE,
}


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{where} is not an object in the advertised schema.")
    return value


def _parameters(mcp: FastMCP[object], tool: str) -> dict[str, object]:
    # FastMCP has no public way to edit an advertised schema; this relies on
    # `Tool.parameters` under the `mcp<2` pin, and the conformance test fails if it moves.
    registered = mcp._tool_manager.get_tool(tool)
    if registered is None:
        raise RuntimeError(f"{tool} is not registered; register_tools must add it first.")
    return registered.parameters


def _enum(mcp: FastMCP[object], tool: str, name: str) -> tuple[dict[str, object], list[object]]:
    properties = _mapping(_parameters(mcp, tool)["properties"], f"{tool}.properties")
    prop = _mapping(properties[name], f"{tool}.{name}")
    values = prop.get("enum")
    if not isinstance(values, list):
        raise RuntimeError(f"{tool}.{name} has no enum in the advertised schema.")
    return prop, values


def _merge_entity_types(mcp: FastMCP[object], tool: str, added: list[str]) -> None:
    prop, values = _enum(mcp, tool, "entity_type")
    merged = sorted({*map(str, values), *added})
    prop["enum"] = merged
    prop["description"] = f"One of: {', '.join(merged)}."


def extend_advertised_schemas(mcp: FastMCP[object], features: frozenset[str]) -> None:
    """Idempotent; ``register_tools`` calls it once per server."""
    if not features:
        return
    _merge_entity_types(mcp, "read", added_readable(features))
    _merge_entity_types(mcp, "list", added_listable(features))
    _, keys = _enum(mcp, "schema", "operation")
    keys.extend(key for key in added_schema_keys(features) if key not in keys)
    for tool, sentence in _SENTENCES.items():
        registered = mcp._tool_manager.get_tool(tool)
        if registered is None:
            raise RuntimeError(f"{tool} is not registered; register_tools must add it first.")
        if not registered.description.startswith(sentence):
            registered.description = f"{sentence} {registered.description}"
