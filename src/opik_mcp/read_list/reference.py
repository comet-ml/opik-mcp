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
    GRAMMAR_LINE,
    KEY_ALLOWED_TYPES,
    KEY_REQUIRED_TYPES,
    MILLISECOND_FIELDS,
    OPERATORS_BY_TYPE,
)
from opik_mcp.read_list.registry import ENTITY_REGISTRY, VOCABULARIES
from opik_mcp.read_list.sorting import SORT_FORM, sortable_names

LIST_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    # Every vocabulary, not only every entity type: a dataset item filtered
    # under its dataset and the same item filtered with runs attached are two
    # field tables, and each has to be answerable on its own.
    *(f"list.{v.name}" for v in VOCABULARIES.values() if v.filter_fields),
    # An entity whose reference is not a field table (a metric is a time
    # series over one) answers its own, and that reference is what keeps its
    # catalog out of the tool description.
    *(f"list.{t}" for t, h in ENTITY_REGISTRY.items() if h.reference_fn is not None),
)


def list_reference(entity_type: str) -> dict[str, Any]:
    """The ``schema("list.<entity>")`` payload. ``entity_type`` must be supported."""
    handler = ENTITY_REGISTRY.get(entity_type)
    if handler is not None and handler.reference_fn is not None:
        # The entity documents itself: a metric's reference is its catalog of
        # metrics, intervals and limits, not a table of OQL fields. It lives
        # beside the data it describes.
        return handler.reference_fn()
    vocabulary = VOCABULARIES[entity_type]
    fields: dict[str, dict[str, Any]] = {}
    for name, ftype in vocabulary.filter_fields.items():
        # A field the backend takes as a query parameter accepts less than its
        # type does — one value cannot carry a negation, one id cannot carry a
        # set. The reference has to state the accepted set for the same reason
        # it states a closed enum's values: what the compiler refuses must be
        # discoverable here rather than by being rejected.
        param = vocabulary.param_fields.get(name)
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
        note = vocabulary.field_notes.get(name)
        if note is not None:
            spec["note"] = note
        values = vocabulary.enum_values.get(name)
        if values is not None:
            # The compiler refuses anything else, so the accepted set has to be
            # discoverable here rather than by being rejected.
            spec["values"] = list(values)
        fields[name] = spec

    filters: dict[str, Any] = {
        "grammar": GRAMMAR_LINE,
        "fields": fields,
        "examples": list(vocabulary.filter_examples),
    }
    if vocabulary.is_source_defaulted:
        filters["default"] = 'source = "sdk" unless you name source'
    requires = vocabulary.filter_requirement
    if requires is not None:
        filters["requires"] = requires

    pointer = vocabulary.vocabulary_pointer
    if pointer is not None:
        filters["see_also"] = pointer.format(
            **{
                other.name: ", ".join(other.filter_fields)
                for other in VOCABULARIES.values()
                if other.vocabulary_pointer is not None
            }
        )

    sort: dict[str, Any] = {"form": SORT_FORM, "fields": sortable_names(vocabulary)}
    why = vocabulary.unsortable_why
    if why is not None:
        # An empty field list reads as "not implemented yet". The endpoint has
        # no sorting parameter at all, and the caller is better off knowing
        # that before they page through a dataset looking for one.
        sort["why"] = why

    is_windowed = ENTITY_REGISTRY[vocabulary.entity_type].is_windowed
    return {
        "operation": f"list.{entity_type}",
        # What the caller types, which is not this key when the key is one of
        # an entity's two vocabularies.
        "entity_type": vocabulary.entity_type,
        "filters": filters,
        "sort": sort,
        "window": is_windowed,
        "search": is_windowed,
    }


__all__ = [
    "LIST_SCHEMA_KEYS",
    "list_reference",
]
