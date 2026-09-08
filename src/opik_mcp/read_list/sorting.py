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

SORTABLE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "trace": _TRACE_SORTABLE,
    "span": _SPAN_SORTABLE,
    "thread": _THREAD_SORTABLE,
    "experiment": _EXPERIMENT_SORTABLE,
}
SORTABLE_ENTITIES: Final[tuple[str, ...]] = tuple(SORTABLE_FIELDS)

SORT_FORM: Final = "<field> [asc|desc]"


class SortError(EntityArgValidationError):
    """The ``sort`` string does not validate (kind ``bad_sort`` in analytics)."""

    kind: str = "bad_sort"


def compile_sort(entity_type: str, sort: str) -> tuple[str, str]:
    """Parse ``"<field> [asc|desc]"`` into ``(field, "ASC"|"DESC")``.

    Direction defaults to ``DESC`` — "slowest / most expensive / most recent
    first" is what a sort on a list is almost always for.
    """
    if entity_type not in SORTABLE_FIELDS:
        raise SortError(
            f"sort is not supported for {entity_type!r}. "
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


def sortable_names(entity_type: str) -> list[str]:
    """Human form of the sortable list: dynamic prefixes shown as ``x.<name>``."""
    return [f"{f[:-2]}.<name>" if f.endswith(".*") else f for f in SORTABLE_FIELDS[entity_type]]


__all__ = [
    "SORTABLE_ENTITIES",
    "SORTABLE_FIELDS",
    "SORT_FORM",
    "SortError",
    "compile_sort",
    "is_sortable",
    "sortable_names",
]
