"""``list`` tool — paginated discovery of Opik entities.

Ported from ollie-assist's ``tools/list.py``. Output is a pipe-delimited
table (mirrors ollie's format) — easier for the LLM to scan than nested
JSON and lossless for the columns we care about (id, name, plus a few
entity-specific fields like ``created_at`` / ``dataset_name``).

Project-scoped lists (``trace``, ``test_suite_item``, ``prompt_version``)
require their parent id via ``project_id`` / ``test_suite_id`` /
``prompt_id`` — enforced via the registry's ``list_required_kwargs``.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
    make_opik_client,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.registry import ENTITY_REGISTRY, LISTABLE_TYPES, EntityHandler

logger = logging.getLogger("opik_mcp.read_list.list")

_MAX_SIZE = 100
_TRUNCATE_AT = 60


async def run_list(
    entity_type: str,
    *,
    name: str | None = None,
    page: int = 1,
    size: int = 25,
    project_id: str | None = None,
    test_suite_id: str | None = None,
    prompt_id: str | None = None,
    settings: Settings | None = None,
    client: OpikListClient | None = None,
) -> str:
    """List tool entrypoint. See ``server.py`` for the registered tool."""
    handler = ENTITY_REGISTRY.get(entity_type)
    if handler is None or handler.list_fn is None:
        valid = ", ".join(sorted(LISTABLE_TYPES))
        err = EntityArgValidationError(f"Cannot list {entity_type!r}. Listable types: {valid}")
        raise ToolError(str(err)) from err

    size = max(1, min(size, _MAX_SIZE))
    page = max(1, page)

    kw: dict[str, Any] = {"page": page, "size": size}
    if name:
        kw["name"] = name
    if project_id is not None:
        kw["project_id"] = project_id
    if test_suite_id is not None:
        kw["test_suite_id"] = test_suite_id
    if prompt_id is not None:
        kw["prompt_id"] = prompt_id

    for required in handler.list_required_kwargs:
        if kw.get(required) is None:
            err = EntityArgValidationError(
                f"list({entity_type!r}) requires {required}. "
                f"E.g. list({entity_type!r}, {required}='<uuid>', …)."
            )
            raise ToolError(str(err)) from err

    opik = client if client is not None else make_opik_client(settings or get_settings())

    try:
        page_body = await handler.list_fn(opik, **kw)
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as e:
        raise ToolError(f"Failed to list {entity_type}s: {e}") from e

    content_raw = page_body.get("content") or []
    content: list[dict[str, Any]] = [it for it in content_raw if isinstance(it, dict)]
    total_raw = page_body.get("total")
    total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else len(content)

    if not content:
        if name:
            return f"No {entity_type}s matching {name!r} found."
        return f"No {entity_type}s found."

    return _format_table(entity_type, handler, content, total, page, size, name)


def _format_table(
    entity_type: str,
    handler: EntityHandler,
    content: list[dict[str, Any]],
    total: int,
    page: int,
    size: int,
    name: str | None,
) -> str:
    """Pipe-delimited table — mirrors ollie's ``_format_table``."""
    columns: tuple[str, ...] = ("id", "name", *handler.list_extra_fields)
    count = len(content)
    if name:
        header = (
            f"Found {total} {entity_type}s matching {name!r} "
            f"(page {page}, showing {count} of {total}):"
        )
    else:
        header = f"Found {total} {entity_type}s (page {page}, showing {count} of {total}):"

    col_header = " | ".join(columns)
    rows: list[str] = []
    for item in content:
        values: list[str] = []
        for col in columns:
            val = item.get(col)
            s = "" if val is None else str(val)
            if len(s) > _TRUNCATE_AT:
                s = s[: _TRUNCATE_AT - 3] + "..."
            values.append(s)
        rows.append(" | ".join(values))

    lines = [header, "", col_header, *rows]
    if page * size < total:
        lines.append("")
        lines.append(f"Use page={page + 1} for next {size} results.")
    return "\n".join(lines)


__all__ = ["run_list"]
