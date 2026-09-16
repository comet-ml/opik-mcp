"""How a column name finds its value in a record.

``ListProjection`` promises that "a dotted name resolves into a nested
container the way a filter field does". Two callers depend on that promise
and have to agree about it: the table, which renders the value, and an
entity's projection, which decides whether the page has one to render. When
they disagreed the failure was silent — a column judged empty and dropped
while the renderer would have found it, or the reverse.
"""

from __future__ import annotations

from typing import Any


def resolve(item: dict[str, Any], column: str) -> Any:
    """The value a column names, or ``None``.

    A flat key wins outright. A dotted name reads one level in: a dict by
    key, a list of named entries (``[{name, value}, …]``) by name — the shape
    feedback scores arrive in, so ``feedback_scores.accuracy`` is the score
    called accuracy rather than a key that does not exist.
    """
    if column in item:
        return item[column]
    if "." not in column:
        return None
    top, _, key = column.partition(".")
    container = item.get(top)
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, list):
        for entry in container:
            if isinstance(entry, dict) and entry.get("name") == key:
                return entry.get("value")
    return None


def has_value(item: dict[str, Any], column: str) -> bool:
    """Does this record fill the column? Empty strings count as unfilled —
    a column of blanks is a column nobody asked for."""
    value = resolve(item, column)
    return value is not None and value != ""


__all__ = ["has_value", "resolve"]
