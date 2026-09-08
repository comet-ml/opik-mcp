"""``list`` tool — paginated discovery and search of Opik entities.

Ported from ollie-assist's ``tools/list.py``. Output is a pipe-delimited
table (mirrors ollie's format) — easier for the LLM to scan than nested
JSON and lossless for the columns we care about (id, name, plus a few
entity-specific fields like ``created_at`` / ``dataset_name``).

Project-scoped lists (``trace``, ``span``, ``thread``, ``test_suite_item``,
``prompt_version``) require their parent id via ``project_id`` /
``test_suite_id`` / ``prompt_id`` — enforced via the registry's
``list_required_kwargs``.

The searchable types (``trace``, ``span``, ``thread``, ``experiment``) also
take ``filters`` — an OQL string compiled by ``oql.py`` into the backend's
filter array. Like the UI's Logs page, trace/span/thread lists add
``source = "sdk"`` unless the caller names ``source`` themselves, so
evaluator / playground / experiment traces don't crowd out application
traffic. Whatever was applied is echoed on the first output line.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
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
from opik_mcp.read_list.oql import (
    SOURCE_DEFAULTED_ENTITIES,
    SUPPORTED_ENTITIES,
    WINDOWED_ENTITIES,
    OQLError,
    compile_filters,
    render_filters,
)
from opik_mcp.read_list.registry import ENTITY_REGISTRY, LISTABLE_TYPES, EntityHandler
from opik_mcp.read_list.sorting import SortError, compile_sort
from opik_mcp.read_list.window import WindowError, resolve_window

logger = logging.getLogger("opik_mcp.read_list.list")

_MAX_SIZE = 100
_TRUNCATE_AT = 60

_SDK_SOURCE_CLAUSE = {"field": "source", "operator": "=", "key": "", "value": "sdk"}


async def run_list(
    entity_type: str,
    *,
    name: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    page: int = 1,
    size: int = 25,
    project_id: str | None = None,
    project_name: str | None = None,
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
    # Only project-scoped lists (trace, span, thread) take project_name;
    # forwarding it to a workspace-wide list_fn (projects/experiments/…) would
    # be an unexpected kwarg. Gate on the same signal the required-check uses.
    if project_name is not None and "project_id" in handler.list_required_kwargs:
        kw["project_name"] = project_name
    if test_suite_id is not None:
        kw["test_suite_id"] = test_suite_id
    if prompt_id is not None:
        kw["prompt_id"] = prompt_id

    for required in handler.list_required_kwargs:
        if kw.get(required) is None:
            # project_name is an accepted alternative to project_id for the
            # project-scoped lists (trace, span, thread) — the client methods
            # take either, so don't force the UUID when a name was given.
            if required == "project_id" and kw.get("project_name"):
                continue
            hint = f"{required} (or project_name)" if required == "project_id" else required
            err = EntityArgValidationError(
                f"list({entity_type!r}) requires {hint}. "
                f"E.g. list({entity_type!r}, {required}='<uuid>', …)."
            )
            raise ToolError(str(err)) from err

    applied: list[str] = []
    clauses: list[dict[str, str]] = []
    source_defaulted = False
    if entity_type in SUPPORTED_ENTITIES or filters:
        try:
            clauses = compile_filters(entity_type, filters or "")
        except OQLError as err:
            raise ToolError(str(err)) from err
        if entity_type in SOURCE_DEFAULTED_ENTITIES and not any(
            c["field"] == "source" for c in clauses
        ):
            clauses.append(dict(_SDK_SOURCE_CLAUSE))
            source_defaulted = True
        if clauses:
            kw["filters"] = json.dumps(clauses, separators=(",", ":"))
            applied.append(f"filters: {render_filters(entity_type, clauses)}")
    if entity_type in WINDOWED_ENTITIES:
        # Bodies never reach the table, so let the backend trim them.
        kw["truncate"] = True
    # The sort label is filled in after the response (the backend may have
    # dropped the sort), but it belongs right after the filters in the header.
    sort_slot = len(applied)

    if since is not None or until is not None:
        if entity_type not in WINDOWED_ENTITIES:
            why = (
                "experiments have no time window on the backend."
                if entity_type == "experiment"
                else f"only {', '.join(WINDOWED_ENTITIES)} take a time window."
            )
            unsupported = WindowError(f"since/until are not supported for {entity_type!r}: {why}")
            raise ToolError(str(unsupported)) from unsupported
        try:
            from_time, to_time = resolve_window(since, until)
        except WindowError as err:
            raise ToolError(str(err)) from err
        if from_time is not None:
            kw["from_time"] = from_time
            applied.append(f"since: {from_time}")
        if to_time is not None:
            kw["to_time"] = to_time
            applied.append(f"until: {to_time}")

    if search is not None and search.strip():
        if entity_type in WINDOWED_ENTITIES:
            kw["search"] = search
            applied.append(f'search: "{search}"')
        else:
            applied.append(f"search ignored (only {', '.join(WINDOWED_ENTITIES)})")

    sort_label: str | None = None
    sort_field: str | None = None
    if sort is not None:
        try:
            sort_field, direction = compile_sort(entity_type, sort)
        except SortError as err:
            raise ToolError(str(err)) from err
        kw["sorting"] = json.dumps(
            [{"field": sort_field, "direction": direction}], separators=(",", ":")
        )
        sort_label = f"sort: {sort_field} {direction.lower()}"

    opik = client if client is not None else make_opik_client(settings or get_settings())

    try:
        page_body = await handler.list_fn(opik, **kw)
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as e:
        raise ToolError(f"Failed to list {entity_type}s: {e}") from e
    except httpx.TimeoutException as e:
        # ``str(httpx.ReadTimeout)`` is often empty, so without this the agent
        # sees an error with no text. Seen live: a free-text search that the
        # backend took >30s to answer on a cold cache.
        raise ToolError(
            f"Opik did not answer in time for list({entity_type}, …). Narrow the query — "
            "a shorter since window, fewer filters, a smaller size, or drop search — and retry."
        ) from e
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach Opik for list({entity_type}, …): {e}") from e

    content_raw = page_body.get("content") or []
    content: list[dict[str, Any]] = [it for it in content_raw if isinstance(it, dict)]
    total_raw = page_body.get("total")
    total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else len(content)

    if sort_label is not None:
        # The backend blanks ``sortable_by`` when it dropped sorting for a large
        # workspace — the only signal that the page is not actually ordered.
        if page_body.get("sortable_by") == []:
            sort_label += " (dropped by the backend for this workspace size; page is unsorted)"
        applied.insert(sort_slot, sort_label)

    header = f"[list: {entity_type} | {' | '.join(applied)}]" if applied else None
    if not content:
        empty = (
            f"No {entity_type}s matching {name!r} found." if name else f"No {entity_type}s found."
        )
        if source_defaulted and len(clauses) == 1 and len(applied) == 1:
            # Seen live: a project holding only experiment traces looks empty
            # under the sdk default. Say so here, where the agent is looking —
            # but only when the default is the sole constraint; with a window
            # or filters of the agent's own, those are the likelier reason.
            empty += (
                ' Only source = "sdk" rows are listed by default; add source = "experiment", '
                '"evaluator" or "playground" to filters to see the others.'
            )
        return f"{header}\n{empty}" if header else empty

    extra = _requested_columns(sort_field, clauses)
    table = _format_table(entity_type, handler, content, total, page, size, name, extra)
    return f"{header}\n{table}" if header else table


# Filter fields that make no sense as a table column: bodies (never shown in a
# list), the error container (error_type carries the useful part) and source
# (it is a scope, not a per-row fact).
_NEVER_COLUMNS = frozenset({"input", "output", "input_json", "output_json", "error_info", "source"})


def _requested_columns(sort_field: str | None, clauses: list[dict[str, str]]) -> list[str]:
    """Columns the request names — the sort field first, then filter fields in
    order of first mention. Nested references keep their ``field.key`` form."""
    out: list[str] = []
    if sort_field is not None:
        out.append(sort_field)
    for c in clauses:
        col = f"{c['field']}.{c['key']}" if c.get("key") else c["field"]
        if c["field"] not in _NEVER_COLUMNS and col not in out:
            out.append(col)
    return out


def _format_table(
    entity_type: str,
    handler: EntityHandler,
    content: list[dict[str, Any]],
    total: int,
    page: int,
    size: int,
    name: str | None,
    extra_columns: list[str] | None = None,
) -> str:
    """Pipe-delimited table — mirrors ollie's ``_format_table``.

    ``extra_columns`` are the fields the request sorted or filtered on; they
    are appended after the entity's default columns (deduplicated) so the
    table shows why each row is present and in what order.
    """
    base = ("id", "name") if handler.list_has_name else ("id",)
    columns: tuple[str, ...] = (*base, *handler.list_extra_fields)
    for col in extra_columns or ():
        if col not in columns:
            columns = (*columns, col)
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
            val = _cell(item, col)
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


def _cell(item: dict[str, Any], col: str) -> Any:
    """Resolve one column of one record.

    ``error_type`` is derived from the error container when the record does
    not carry it flat: the backend's list payload has ``error_info.exception_type``.
    A feedback-score list (``[{name, value}, …]``) renders as ``name=value`` pairs.
    A dotted column (``feedback_scores.accuracy``, ``usage.total_tokens``,
    ``metadata.environment``) resolves into the nested value: a dict by key, a
    list of named entries by ``name``. Anything missing renders empty.
    """
    if col in item:
        val = item[col]
        if isinstance(val, list) and val and all(isinstance(s, dict) for s in val):
            return _score_summary(val)
        return val
    if col == "error_type":
        info = item.get("error_info")
        if isinstance(info, dict):
            return info.get("exception_type")
    if "." in col:
        top, _, key = col.partition(".")
        container = item.get(top)
        if isinstance(container, dict):
            return container.get(key)
        if isinstance(container, list):
            for entry in container:
                if isinstance(entry, dict) and entry.get("name") == key:
                    return entry.get("value")
    return None


def _score_summary(scores: list[dict[str, Any]]) -> str:
    return ", ".join(f"{s.get('name')}={s.get('value')}" for s in scores if "name" in s)


__all__ = ["run_list"]
