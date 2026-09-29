"""Narrow the advertised input schemas to what one mode shows.

The tool functions are written once, for the default surface. A mode advertises
less: fewer entity types, no arguments that only hidden types take, no text that
names a hidden entity. Call-time gating (``read_list.visibility``) is what
refuses; this only changes what the host is told.
"""

from __future__ import annotations

import re
from typing import Final

from mcp.server.fastmcp import FastMCP

from opik_mcp.cost_intelligence import Mode
from opik_mcp.cost_intelligence.descriptions import ARGUMENT_TEXT
from opik_mcp.read_list.registry import ENTITY_REGISTRY
from opik_mcp.read_list.visibility import (
    MODE_HIDDEN_LIST_ARGS,
    list_schema_keys,
    listable_types,
    readable_types,
)
from opik_mcp.skills_catalog import skill_names, visible_skill_names

_DROPPED_ARGUMENTS: Final[dict[str, tuple[str, ...]]] = {
    "read": ("project_id", "project_name"),
    "list": (*MODE_HIDDEN_LIST_ARGS, "project_id", "project_name"),
}
# Words that name what no mode shows, beyond the entity and skill names.
_HIDDEN_WORDS: Final = ("write", "Diagnostics", "issues?", "test_suite")


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


def _hidden_pattern(mode: Mode) -> re.Pattern[str]:
    shown = {*readable_types(mode), *listable_types(mode)}
    hidden_skills = set(skill_names()) - set(visible_skill_names(mode))
    names = [*(t for t in ENTITY_REGISTRY if t not in shown), *hidden_skills]
    alternatives = "|".join((*map(re.escape, names), *_HIDDEN_WORDS))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def narrow_advertised_schemas(mcp: FastMCP[object], mode: Mode) -> None:
    hidden = _hidden_pattern(mode)
    enums = {
        ("read", "entity_type"): tuple(sorted(readable_types(mode))),
        ("list", "entity_type"): tuple(sorted(listable_types(mode))),
        ("schema", "operation"): list_schema_keys(mode),
    }
    for tool in ("read", "list", "schema", "read_skill"):
        parameters = _parameters(mcp, tool)
        properties = _mapping(parameters["properties"], f"{tool}.properties")
        for name in _DROPPED_ARGUMENTS.get(tool, ()):
            if name not in properties:
                raise RuntimeError(f"{tool}.{name} is not in the tool schema; drop list is stale.")
            del properties[name]
        required = parameters.get("required")
        if isinstance(required, list):
            parameters["required"] = [name for name in required if name in properties]
        for name, raw in properties.items():
            prop = _mapping(raw, f"{tool}.{name}")
            allowed = enums.get((tool, name))
            if allowed is not None:
                prop["enum"] = list(allowed)
                if name == "entity_type":
                    prop["description"] = f"One of: {', '.join(allowed)}."
                    continue
            if allowed is not None or hidden.search(str(prop.get("description", ""))):
                replacement = ARGUMENT_TEXT.get((tool, name))
                if replacement is None:
                    raise RuntimeError(
                        f"{tool}.{name} names something {mode} hides and has no entry in "
                        "cost_intelligence/descriptions.py ARGUMENT_TEXT."
                    )
                prop["description"] = replacement
