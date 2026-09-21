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
    ENUM_VALUES,
    FILTERABLE_FIELDS,
    GRAMMAR_LINE,
    KEY_ALLOWED_TYPES,
    KEY_REQUIRED_TYPES,
    MILLISECOND_FIELDS,
    OPERATORS_BY_TYPE,
    PARAM_FIELDS,
    SOURCE_DEFAULTED_ENTITIES,
    SUPPORTED_ENTITIES,
    WINDOWED_ENTITIES,
)
from opik_mcp.read_list.registry import ENTITY_REGISTRY
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
    "dataset_item": (
        "feedback_scores.correctness < 0.5",
        'data.question contains "refund" AND output contains "sorry"',
    ),
}


#: What an entity's filters cannot be used without. Only the compared items
#: have such a condition: every one of their filter fields reads the runs,
#: which exist only when the call names the experiments to compare.
FILTER_REQUIREMENTS: Final[dict[str, str]] = {
    "dataset_item": "experiment_ids: filters, sort and search apply to the runs",
}


#: Per-field caveats, keyed by entity then field. For a field whose name
#: promises more than it matches: the reference is where a caller looks
#: before writing the filter, so it is where the gap has to be stated. A
#: refusal cannot carry it — the filter is accepted and answers 200.
FIELD_NOTES: Final[dict[str, dict[str, str]]] = {
    "experiment": {
        "prompt_ids": (
            "matches prompt ids, not prompt version ids: the backend compares against the "
            "experiment's prompt ids, so this narrows to a prompt and not to one version of "
            "it. No prompt-version filter exists on the backend."
        ),
    },
}


def list_reference(entity_type: str) -> dict[str, Any]:
    """The ``schema("list.<entity>")`` payload. ``entity_type`` must be supported."""
    handler = ENTITY_REGISTRY.get(entity_type)
    if handler is not None and handler.reference_fn is not None:
        # The entity documents itself: a metric's reference is its catalog of
        # metrics, intervals and limits, not a table of OQL fields. It lives
        # beside the data it describes.
        return handler.reference_fn()
    fields: dict[str, dict[str, Any]] = {}
    for name, ftype in FILTERABLE_FIELDS[entity_type].items():
        # A field the backend takes as a query parameter accepts less than its
        # type does — one value cannot carry a negation, one id cannot carry a
        # set. The reference has to state the accepted set for the same reason
        # it states a closed enum's values: what the compiler refuses must be
        # discoverable here rather than by being rejected.
        param = PARAM_FIELDS.get(entity_type, {}).get(name)
        operators = list(param.operators) if param is not None else list(OPERATORS_BY_TYPE[ftype])
        spec: dict[str, Any] = {"type": ftype, "operators": operators}
        if param is not None and param.value_form == "uuid":
            spec["format"] = "UUID"
        if ftype in KEY_REQUIRED_TYPES:
            spec["key"] = "required"
        elif ftype in KEY_ALLOWED_TYPES:
            spec["key"] = "optional"
        if name in MILLISECOND_FIELDS:
            spec["unit"] = "milliseconds"
        if ftype == "date_time":
            spec["format"] = 'ISO-8601 instant with timezone, e.g. "2026-09-08T10:00:00Z"'
        note = FIELD_NOTES.get(entity_type, {}).get(name)
        if note is not None:
            spec["note"] = note
        values = ENUM_VALUES.get(entity_type, {}).get(name)
        if values is not None:
            # The compiler refuses anything else, so the accepted set has to be
            # discoverable here rather than by being rejected.
            spec["values"] = list(values)
        fields[name] = spec

    filters: dict[str, Any] = {
        "grammar": GRAMMAR_LINE,
        "fields": fields,
        "examples": list(FILTER_EXAMPLES[entity_type]),
    }
    if entity_type in SOURCE_DEFAULTED_ENTITIES:
        filters["default"] = 'source = "sdk" unless you name source'
    requires = FILTER_REQUIREMENTS.get(entity_type)
    if requires is not None:
        filters["requires"] = requires

    return {
        "operation": f"list.{entity_type}",
        "entity_type": entity_type,
        "filters": filters,
        "sort": {"form": SORT_FORM, "fields": sortable_names(entity_type)},
        "window": entity_type in WINDOWED_ENTITIES,
        "search": entity_type in WINDOWED_ENTITIES,
    }


__all__ = [
    "FIELD_NOTES",
    "FILTER_EXAMPLES",
    "FILTER_REQUIREMENTS",
    "LIST_SCHEMA_KEYS",
    "list_reference",
]
