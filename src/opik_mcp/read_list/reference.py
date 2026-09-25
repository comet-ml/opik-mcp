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
    VOCABULARY_MODES,
    WINDOWED_ENTITIES,
)
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.read_list.sorting import SORT_FORM, sortable_names

LIST_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    # Every vocabulary, not only every entity type: a dataset item filtered
    # under its dataset and the same item filtered with runs attached are two
    # field tables, and each has to be answerable on its own.
    *(f"list.{e}" for e in FILTERABLE_FIELDS),
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
    "dataset_item_case": (
        'data.question contains "install"',
        'trace_id = "<trace-uuid>"',
    ),
}


#: What an entity's filters cannot be used without. Only the compared items
#: have such a condition: every one of their filter fields reads the runs,
#: which exist only when the call names the experiments to compare.
FILTER_REQUIREMENTS: Final[dict[str, str]] = {
    "dataset_item": "experiment_ids: these filters, the sort and search apply to the runs",
}


#: The other field table of an entity that has two, named from each. An agent
#: reaches for ``schema("list.dataset_item")`` whichever of the two calls it
#: means, and the fields it finds there are the ones the other call refuses —
#: so each reference says where the rest of them are.
VOCABULARY_POINTERS: Final[dict[str, str]] = {
    "dataset_item": (
        "without experiment_ids the same list is the dataset's own cases, filtered on the "
        "case itself ({dataset_item_case}) — operators in "
        'schema("list.dataset_item_case")'
    ),
    "dataset_item_case": (
        "with experiment_ids the same list is those experiments' runs case by case, filtered "
        'on the runs ({dataset_item}) — operators in schema("list.dataset_item")'
    ),
}
"""``{vocabulary}`` is filled in with that vocabulary's field names, so the
pointer cannot promise a field the table does not have. The names are worth
the bytes: an agent asks for one of these two references and has to learn from
it that the other call exists *and* what it would be able to ask there."""


#: Per-field caveats, keyed by entity then field. For a field whose name
#: promises more than it matches: the reference is where a caller looks
#: before writing the filter, so it is where the gap has to be stated. A
#: refusal cannot carry it — the filter is accepted and answers 200.
FIELD_NOTES: Final[dict[str, dict[str, str]]] = {
    "dataset_item_case": {
        "full_data": (
            "the whole payload as one string, matched case-insensitively: a full scan of the "
            "dataset, with no index behind it. It is the free-text search this endpoint does "
            "not have; a key you can name (data.<key>) is the cheaper question."
        ),
        "source": (
            "how the case was created: manual, trace, span or sdk. Use contains — it is the "
            "only operator that answers. The column is a ClickHouse Enum8 while "
            "opik-backend declares the filter field as a string, so = and starts_with "
            "compile to lower(source), which ClickHouse cannot apply to an enum and which "
            "fails the request with a 500; contains compiles to ilike, which converts. "
            "Measured against two unrelated datasets, on a valid value as well as an "
            "unknown one. Fixed backend-side by typing the field as the enum it is, the way "
            "TraceField.SOURCE already is — at which point = works and contains stops."
        ),
        "data": (
            "the case's own keys, one per column of the dataset — data.question, "
            "data.expected_output. No comparisons: the column is a ClickHouse Map, and "
            "opik-backend's operator map has no > or < for that type (it answers 400). The "
            "comparison's data.<key> does take them — there the key becomes a field the "
            "backend types as a string."
        ),
    },
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

    pointer = VOCABULARY_POINTERS.get(entity_type)
    if pointer is not None:
        filters["see_also"] = pointer.format(
            **{name: ", ".join(FILTERABLE_FIELDS[name]) for name in VOCABULARY_POINTERS}
        )

    vocabulary = VOCABULARIES[entity_type]
    sort: dict[str, Any] = {"form": SORT_FORM, "fields": sortable_names(vocabulary)}
    why = vocabulary.unsortable_why
    if why is not None:
        # An empty field list reads as "not implemented yet". The endpoint has
        # no sorting parameter at all, and the caller is better off knowing
        # that before they page through a dataset looking for one.
        sort["why"] = why

    return {
        "operation": f"list.{entity_type}",
        # What the caller types, which is not this key when the key is one of
        # an entity's two vocabularies.
        "entity_type": VOCABULARY_MODES.get(entity_type, entity_type),
        "filters": filters,
        "sort": sort,
        "window": entity_type in WINDOWED_ENTITIES,
        "search": entity_type in WINDOWED_ENTITIES,
    }


__all__ = [
    "FIELD_NOTES",
    "FILTER_EXAMPLES",
    "FILTER_REQUIREMENTS",
    "LIST_SCHEMA_KEYS",
    "VOCABULARY_POINTERS",
    "list_reference",
]
