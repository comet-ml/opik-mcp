"""``schema("list.<entity>")`` — the filter / sort reference for one entity.

The ``list`` tool's description carries two lines of grammar and two
examples so it stays cheap on every turn. This is the long form an agent
asks for on purpose: every filterable field with its type and valid
operators, the sortable fields, whether a time window and free-text search
exist, and the same two examples. It is generated from the tables the
validator uses (``oql.py``, ``sorting.py``), so it cannot say one thing
while ``list`` accepts another.
"""

from __future__ import annotations

from typing import Any, Final

from opik_mcp.read_list.oql import (
    FILTERABLE_FIELDS,
    GRAMMAR_LINE,
    KEYED_TYPES,
    MILLISECOND_FIELDS,
    OPERATORS_BY_TYPE,
    SOURCE_DEFAULTED_ENTITIES,
    SUPPORTED_ENTITIES,
    WINDOWED_ENTITIES,
)
from opik_mcp.read_list.sorting import SORT_FORM, sortable_names

LIST_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    *(f"list.{e}" for e in SUPPORTED_ENTITIES),
    # Not an OQL entity of its own — a time series over one of them — but it
    # has a reference of its own to answer, and it is the reference that keeps
    # the metric table out of the tool description.
    "list.project_metric",
)

FILTER_EXAMPLES: Final[dict[str, tuple[str, str]]] = {
    "trace": (
        "error_info is_not_empty AND duration > 5000",
        'feedback_scores.accuracy < 0.5 AND start_time >= "2026-09-08T00:00:00Z"',
    ),
    "span": (
        'type = "llm" AND usage.total_tokens > 10000',
        'name = "search_docs" AND error_info is_not_empty',
    ),
    "thread": (
        'status = "active" AND number_of_messages > 20',
        "feedback_scores.helpfulness < 0.5 AND duration > 60000",
    ),
    "experiment": (
        'dataset_id = "<dataset-uuid>" AND tags contains "baseline"',
        'metadata.model = "gpt-4o" AND feedback_scores.accuracy >= 0.8',
    ),
}


def list_reference(entity_type: str) -> dict[str, Any]:
    """The ``schema("list.<entity>")`` payload. ``entity_type`` must be supported."""
    if entity_type == "project_metric":
        # Its own tables (metrics, intervals, limits) rather than OQL fields;
        # the filter fields are those of whichever entity the metric is about.
        from opik_mcp.read_list.project_metrics import reference

        return reference()
    fields: dict[str, dict[str, Any]] = {}
    for name, ftype in FILTERABLE_FIELDS[entity_type].items():
        spec: dict[str, Any] = {"type": ftype, "operators": list(OPERATORS_BY_TYPE[ftype])}
        if ftype in KEYED_TYPES:
            spec["key"] = "required"
        if name in MILLISECOND_FIELDS:
            spec["unit"] = "milliseconds"
        if ftype == "date_time":
            spec["format"] = 'ISO-8601 instant with timezone, e.g. "2026-09-08T10:00:00Z"'
        fields[name] = spec

    filters: dict[str, Any] = {
        "grammar": GRAMMAR_LINE,
        "fields": fields,
        "examples": list(FILTER_EXAMPLES[entity_type]),
    }
    if entity_type in SOURCE_DEFAULTED_ENTITIES:
        filters["default"] = 'source = "sdk" unless you name source'

    return {
        "operation": f"list.{entity_type}",
        "entity_type": entity_type,
        "filters": filters,
        "sort": {"form": SORT_FORM, "fields": sortable_names(entity_type)},
        "window": entity_type in WINDOWED_ENTITIES,
        "search": entity_type in WINDOWED_ENTITIES,
    }


__all__ = ["FILTER_EXAMPLES", "LIST_SCHEMA_KEYS", "list_reference"]
