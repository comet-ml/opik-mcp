"""What the four automation entities share: reading backend JSON without
guessing its shape, paging an array the backend returns whole, and the
sentence for a record that has no page in the UI yet."""

from __future__ import annotations

import json
from collections.abc import Mapping

from opik_mcp.config import Settings

Row = Mapping[str, object]


def rows_of(value: object) -> list[dict[str, object]]:
    """The objects in a JSON array; anything else contributes nothing."""
    if not isinstance(value, list):
        return []
    return [{str(key): item for key, item in row.items()} for row in value if isinstance(row, dict)]


def child(row: Row, key: str) -> dict[str, object]:
    value = row.get(key)
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def text(row: Row, key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value)


def optional_text(row: Row, key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def count(row: Row, key: str) -> int | None:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def positive_int(row: Row, key: str, default: int) -> int:
    value = row.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def page_of(items: list[dict[str, object]], kw: Row) -> dict[str, object]:
    """The page envelope every listing renders, cut here from an array the
    backend returns whole: the table's own footer promises a next page, so
    the slice has to be real."""
    page = positive_int(kw, "page", 1)
    size = positive_int(kw, "size", 10)
    start = (page - 1) * size
    window = items[start : start + size]
    return {"content": window, "page": page, "size": len(window), "total": len(items)}


def no_page(_settings: Settings, _data: dict[str, object]) -> dict[str, str]:
    """Automations have no page in the Opik UI yet, and a missing link has to say so."""
    return {
        "url_absent": (
            "Automations have no page in the Opik UI yet; this record is reachable "
            "through read and list only."
        )
    }


__all__ = [
    "Row",
    "child",
    "compact",
    "count",
    "no_page",
    "optional_text",
    "page_of",
    "positive_int",
    "rows_of",
    "text",
]
