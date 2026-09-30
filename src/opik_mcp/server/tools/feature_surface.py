"""Extend the advertised tools with what a workspace's features add.

The tool functions are written once, for the default surface. A feature only
adds: entity types, their ``schema`` keys and one sentence per description.
Call-time gating (``read_list.visibility``) is what refuses; this only changes
what the host is told.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from opik_mcp.features.toggles import FeatureToggles
from opik_mcp.read_list.visibility import list_schema_keys, listable_types, readable_types


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


def _set_entity_types(mcp: FastMCP[object], tool: str, types: tuple[str, ...]) -> None:
    """The advertised enum is the visible view, not the base enum plus a delta: the
    tool signature already advertises the no-feature view, so one source decides it."""
    prop, _ = _enum(mcp, tool, "entity_type")
    visible = sorted(types)
    prop["enum"] = visible
    prop["description"] = f"One of: {', '.join(visible)}."


def extend_advertised_schemas(mcp: FastMCP[object], toggles: FeatureToggles) -> None:
    """Idempotent; ``register_tools`` calls it once per server."""
    # No feature on means the advertised surface is left exactly as registered.
    if not toggles:
        return
    _set_entity_types(mcp, "read", readable_types(toggles))
    _set_entity_types(mcp, "list", listable_types(toggles))
    # ``schema`` also advertises every write operation, so this one extends.
    _, keys = _enum(mcp, "schema", "operation")
    keys.extend(key for key in list_schema_keys(toggles) if key not in keys)
    for tool, sentence in toggles.tool_sentences:
        registered = mcp._tool_manager.get_tool(tool)
        if registered is None:
            raise RuntimeError(f"{tool} is not registered; register_tools must add it first.")
        if not registered.description.startswith(sentence):
            registered.description = f"{sentence} {registered.description}"
