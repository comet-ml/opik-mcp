"""``sort`` for the ``list`` tool — one field, one direction, validated locally.

opik-backend's ``sorting`` query param takes ``[{field, direction}]``. Three
of its behaviours make local validation necessary:

- only the first field is honoured; extras are silently dropped;
- an unknown field is silently ignored, not rejected — the agent would get an
  unsorted page and no signal;
- on very large workspaces sorting is dropped entirely and the page comes back
  with an empty ``sortable_by`` (the ``list`` tool flags that in its header).

The sortable lists mirror the backend's ``*SortingFactory`` classes and live
with each entity, as ``Vocabulary.sort_fields``. Entries ending in ``.*`` are
dynamic prefixes: ``feedback_scores.accuracy``, ``usage.total_tokens``,
``experiment_scores.<name>``, ``duration.p50``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import Vocabulary
from opik_mcp.read_list.oql import called

SORT_FORM: Final = "<field> [asc|desc]"


class SortError(EntityArgValidationError):
    """The ``sort`` string does not validate.

    Analytics buckets the failure by this class name (``cause_type``); the
    offending string never leaves the process.
    """


def compile_sort(
    vocabulary: Vocabulary, sort: str, *, sortable_types: Sequence[str] = ()
) -> tuple[str, str]:
    """Parse ``"<field> [asc|desc]"`` into ``(field, "ASC"|"DESC")``.

    Direction defaults to ``DESC`` — "slowest / most expensive / most recent
    first" is what a sort on a list is almost always for. ``sortable_types``
    is named in the refusal for a vocabulary that orders by nothing.
    """
    entity_type = vocabulary.name
    if not vocabulary.sort_fields:
        why = vocabulary.unsortable_why
        if why is not None:
            raise SortError(f"sort is not supported for {called(entity_type)!r}: {why}.")
        raise SortError(
            f"sort is not supported for {called(entity_type)!r}. "
            f"Sortable types: {', '.join(sortable_types)}."
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
    if not is_sortable(vocabulary, field):
        raise SortError(
            f"'{field}' is not sortable for {called(entity_type)}. "
            f"Sortable: {', '.join(sortable_names(vocabulary))}."
        )
    return field, direction.upper()


def is_sortable(vocabulary: Vocabulary, field: str) -> bool:
    for allowed in vocabulary.sort_fields:
        if allowed.endswith(".*"):
            prefix = allowed[:-1]  # "feedback_scores."
            if field.startswith(prefix) and len(field) > len(prefix):
                return True
        elif field == allowed:
            return True
    return False


def sort_field_label(sort: str | None, vocabularies: Iterable[Vocabulary]) -> str:
    """The sort field for analytics: a dynamic ``prefix.<name>`` collapses to
    ``prefix.*`` so a user-named score never becomes a label; anything that
    is not a known static or dynamic field collapses to ``""``."""
    if not sort:
        return ""
    field = sort.split()[0]
    for allowed in {a for vocabulary in vocabularies for a in vocabulary.sort_fields}:
        if allowed.endswith(".*"):
            if field.startswith(allowed[:-1]):
                return allowed
        elif field == allowed:
            return field
    return ""


def sortable_names(vocabulary: Vocabulary) -> list[str]:
    """Human form of the sortable list: dynamic prefixes shown as ``x.<name>``."""
    return [f"{f[:-2]}.<name>" if f.endswith(".*") else f for f in vocabulary.sort_fields]


__all__ = [
    "SORT_FORM",
    "SortError",
    "compile_sort",
    "is_sortable",
    "sort_field_label",
    "sortable_names",
]
