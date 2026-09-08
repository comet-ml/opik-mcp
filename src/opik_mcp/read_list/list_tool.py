"""``list`` tool — paginated discovery of Opik entities.

Ported from ollie-assist's ``tools/list.py``. Output is a pipe-delimited
table (mirrors ollie's format) — easier for the LLM to scan than nested
JSON and lossless for the columns we care about (id, name, plus a few
entity-specific fields like ``created_at`` / ``dataset_name``).

Project-scoped lists (``trace``, ``thread``, ``agent_insights_issue``,
``test_suite_item``, ``prompt_version``) require their parent id via
``project_id`` / ``test_suite_id`` / ``prompt_id`` — enforced via the
registry's ``list_required_kwargs``. Entity-specific filters (``status``,
``from_date``, ``to_date`` for Diagnostics issues) are forwarded only to the
entity that declares them in ``list_optional_kwargs``.
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
    project_name: str | None = None,
    test_suite_id: str | None = None,
    prompt_id: str | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
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
    # Entity-specific kwargs are forwarded only when the registry entry declares
    # them (required or optional). A parent id meant for another entity, or
    # project scope on a workspace-wide list, would otherwise reach the client
    # as an unexpected kwarg. ``project_name`` rides along with ``project_id``:
    # every project-scoped client method accepts either.
    accepted = set(handler.list_required_kwargs) | set(handler.list_optional_kwargs)
    if "project_id" in accepted:
        accepted.add("project_name")
    candidates: dict[str, Any] = {
        "project_id": project_id,
        "project_name": project_name,
        "test_suite_id": test_suite_id,
        "prompt_id": prompt_id,
        "status": status,
        "from_date": from_date,
        "to_date": to_date,
    }
    for key, value in candidates.items():
        if value is not None and key in accepted:
            kw[key] = value

    for required in handler.list_required_kwargs:
        if kw.get(required) is None:
            # project_name is an accepted alternative to project_id for the
            # project-scoped lists (trace, thread) — the client methods take
            # either, so don't force the UUID when a name was given.
            if required == "project_id" and kw.get("project_name"):
                continue
            hint = f"{required} (or project_name)" if required == "project_id" else required
            err = EntityArgValidationError(
                f"list({entity_type!r}) requires {hint}. "
                f"E.g. list({entity_type!r}, {required}='<uuid>', …)."
            )
            raise ToolError(str(err)) from err

    opik = client if client is not None else make_opik_client(settings or get_settings())

    try:
        page_body = await handler.list_fn(opik, **kw)
    except EntityArgValidationError as e:
        # A list_fn may reject its own arguments (e.g. a project_name that
        # resolves to no or several projects). Surface it as the same typed
        # validation error the tool raises for a missing parent id.
        raise ToolError(str(e)) from e
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
