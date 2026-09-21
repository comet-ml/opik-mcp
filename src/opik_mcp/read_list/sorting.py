"""``sort`` for the ``list`` tool — one field, one direction, validated locally.

opik-backend's ``sorting`` query param takes ``[{field, direction}]``. Three
of its behaviours make local validation necessary:

- only the first field is honoured; extras are silently dropped;
- an unknown field is silently ignored, not rejected — the agent would get an
  unsorted page and no signal;
- on very large workspaces sorting is dropped entirely and the page comes back
  with an empty ``sortable_by`` (the ``list`` tool flags that in its header).

The sortable lists mirror the backend's ``*SortingFactory`` classes. Entries
ending in ``.*`` are dynamic prefixes: ``feedback_scores.accuracy``,
``usage.total_tokens``, ``experiment_scores.<name>``, ``duration.p50``.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.read_list.errors import EntityArgValidationError

_TRACE_SORTABLE: Final = (
    "id",
    "name",
    "input",
    "output",
    "start_time",
    "end_time",
    "duration",
    "ttft",
    "metadata",
    "thread_id",
    "span_count",
    "llm_span_count",
    "usage.*",
    "total_estimated_cost",
    "tags",
    "error_info",
    "created_by",
    "feedback_scores.*",
    "experiment_id",
    "environment",
)
_SPAN_SORTABLE: Final = (
    "id",
    "name",
    "type",
    "trace_id",
    "parent_span_id",
    "input",
    "output",
    "metadata",
    "start_time",
    "end_time",
    "duration",
    "ttft",
    "usage.*",
    "tags",
    "created_at",
    "last_updated_at",
    "model",
    "provider",
    "total_estimated_cost",
    "error_info",
    "created_by",
    "feedback_scores.*",
    "environment",
)
_THREAD_SORTABLE: Final = (
    "id",
    "start_time",
    "end_time",
    "duration",
    "number_of_messages",
    "last_updated_at",
    "created_by",
    "created_at",
    "usage.*",
    "total_estimated_cost",
    "feedback_scores.*",
    "status",
    "tags",
    "environment",
)
_EXPERIMENT_SORTABLE: Final = (
    "id",
    "name",
    "created_at",
    "last_updated_at",
    "created_by",
    "last_updated_by",
    "tags",
    "trace_count",
    "total_estimated_cost",
    "total_estimated_cost_avg",
    "feedback_scores.*",
    "experiment_scores.*",
    "duration.*",
    "pass_rate",
)

_DATASET_ITEM_SORTABLE: Final = (
    "id",
    "created_at",
    "last_updated_at",
    "duration",
    "total_estimated_cost",
    "comments",
    "usage.*",
    "feedback_scores.*",
    "data.*",
    "output.*",
    "input.*",
    "metadata.*",
)
"""Transcribed from ``SortingFactoryDatasets``, which serves the joined
comparison page.

There is no ``status`` and no pass/fail field on it, so "show me the failed
cases first" cannot be a sort — it is a filter on the score the judge wrote.
Naming that here is the point: the backend logs an unsupported sort field and
answers 200 with an unsorted page, so anything missing from this list has to
be refused before the call.
"""

_PROJECT_SORTABLE: Final = (
    "id",
    "name",
    "created_at",
    "last_updated_at",
    "last_updated_trace_at",
)
"""Transcribed from ``SortingFactoryProjects``.

``last_updated_trace_at`` is the one that answers a real question: a
workspace accumulates throwaway projects, and the list arrives ordered by
creation, so "which project is actually live" meant reading fifteen rows and
comparing two date columns by eye.
"""

_DATASET_ITEM_CASE_SORTABLE: Final[tuple[str, ...]] = ()
"""The dataset's own items endpoint orders by nothing at all.

``GET /datasets/{id}/items`` takes ``page``, ``size``, ``version``, ``filters``
and ``truncate`` — there is no ``sorting`` parameter to send, so every sort is
refused here rather than dropped silently. Declared as an empty tuple rather
than left out, because a missing entry would make the entity unsortable *and*
make ``schema("list.dataset_item_case")`` raise on its way to saying so.
"""

#: Why an entity with an empty sortable list has one, in the refusal's voice.
#: The general message ("sortable types are …") reads as our limitation; this
#: says whose it is and what does order the same rows.
UNSORTABLE_WHY: Final[dict[str, str]] = {
    "dataset_item_case": (
        "opik-backend's dataset items endpoint takes no sorting parameter. Only the "
        "comparison orders cases, so a sort needs experiment_ids: "
        "list('dataset_item', experiment_ids=['<uuid>', '<uuid>'], sort='duration desc')"
    ),
}

SORTABLE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "project": _PROJECT_SORTABLE,
    "trace": _TRACE_SORTABLE,
    "span": _SPAN_SORTABLE,
    "thread": _THREAD_SORTABLE,
    "experiment": _EXPERIMENT_SORTABLE,
    "dataset_item": _DATASET_ITEM_SORTABLE,
    "dataset_item_case": _DATASET_ITEM_CASE_SORTABLE,
}
SORTABLE_ENTITIES: Final[tuple[str, ...]] = tuple(
    entity for entity, fields in SORTABLE_FIELDS.items() if fields
)
"""The entities a sort can name. An entity whose backend orders by nothing is
in ``SORTABLE_FIELDS`` with an empty list — so that the reference can publish
"no sorting here" — and out of this one, which is what a refusal lists."""

SORT_FORM: Final = "<field> [asc|desc]"


class SortError(EntityArgValidationError):
    """The ``sort`` string does not validate.

    Analytics buckets the failure by this class name (``cause_type``); the
    offending string never leaves the process.
    """


def compile_sort(entity_type: str, sort: str) -> tuple[str, str]:
    """Parse ``"<field> [asc|desc]"`` into ``(field, "ASC"|"DESC")``.

    Direction defaults to ``DESC`` — "slowest / most expensive / most recent
    first" is what a sort on a list is almost always for.
    """
    allowed = SORTABLE_FIELDS.get(entity_type)
    if not allowed:
        why = UNSORTABLE_WHY.get(entity_type)
        raise SortError(
            f"sort is not supported for {entity_type!r}: {why}."
            if why
            else f"sort is not supported for {entity_type!r}. "
            f"Sortable types: {', '.join(SORTABLE_ENTITIES)}."
        )
    parts = sort.split()
    if not parts or len(parts) > 2:
        raise SortError(f"Invalid sort {sort!r}: expected {SORT_FORM}, e.g. 'duration desc'.")
    field = parts[0]
    direction = parts[1].lower() if len(parts) == 2 else "desc"
    if direction not in ("asc", "desc"):
        raise SortError(
            f"Invalid sort direction {parts[1]!r}: expected {SORT_FORM}, e.g. 'duration desc'."
        )
    if not is_sortable(entity_type, field):
        raise SortError(
            f"'{field}' is not sortable for {entity_type}. "
            f"Sortable: {', '.join(sortable_names(entity_type))}."
        )
    return field, direction.upper()


def is_sortable(entity_type: str, field: str) -> bool:
    for allowed in SORTABLE_FIELDS[entity_type]:
        if allowed.endswith(".*"):
            prefix = allowed[:-1]  # "feedback_scores."
            if field.startswith(prefix) and len(field) > len(prefix):
                return True
        elif field == allowed:
            return True
    return False


def sort_field_label(sort: str | None) -> str:
    """The sort field for analytics: a dynamic ``prefix.<name>`` collapses to
    ``prefix.*`` so a user-named score never becomes a label; anything that
    is not a known static or dynamic field collapses to ``""``."""
    if not sort:
        return ""
    field = sort.split()[0]
    for allowed in {a for fields in SORTABLE_FIELDS.values() for a in fields}:
        if allowed.endswith(".*"):
            if field.startswith(allowed[:-1]):
                return allowed
        elif field == allowed:
            return field
    return ""


def sortable_names(entity_type: str) -> list[str]:
    """Human form of the sortable list: dynamic prefixes shown as ``x.<name>``."""
    return [f"{f[:-2]}.<name>" if f.endswith(".*") else f for f in SORTABLE_FIELDS[entity_type]]


__all__ = [
    "SORTABLE_ENTITIES",
    "SORTABLE_FIELDS",
    "SORT_FORM",
    "UNSORTABLE_WHY",
    "SortError",
    "compile_sort",
    "is_sortable",
    "sort_field_label",
    "sortable_names",
]
