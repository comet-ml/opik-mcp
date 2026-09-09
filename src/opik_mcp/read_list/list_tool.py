"""``list`` tool — paginated discovery and search of Opik entities.

Ported from ollie-assist's ``tools/list.py``. Output is a pipe-delimited
table (mirrors ollie's format) — easier for the LLM to scan than nested
JSON and lossless for the columns we care about (id, name, plus a few
entity-specific fields like ``created_at`` / ``dataset_name``).

Project-scoped lists (``trace``, ``span``, ``thread``, ``agent_insights_issue``,
``test_suite_item``, ``prompt_version``) require their parent id via
``project_id`` / ``test_suite_id`` / ``prompt_id`` — enforced via the
registry's ``list_required_kwargs``. Entity-specific kwargs (``status`` for
Diagnostics issues) are forwarded only to the entity that declares them in
``list_optional_kwargs``.

The searchable types (``trace``, ``span``, ``thread``, ``experiment``) also
take ``filters`` — an OQL string compiled by ``oql.py`` into the backend's
filter array. Like the UI's Logs page, trace/span/thread lists add
``source = "sdk"`` unless the caller names ``source`` themselves, so
evaluator / playground / experiment traces don't crowd out application
traffic. Whatever was applied is echoed on the first output line.

``since`` / ``until`` is one vocabulary for every windowed type: an instant
window (``from_time`` / ``to_time``) for trace, span and thread, and a
report-day window (``from_date`` / ``to_date``) for Diagnostics issues, whose
backend aggregates by day.
"""

from __future__ import annotations

import json
import logging
import math
import re
from decimal import ROUND_HALF_UP, Decimal
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
from opik_mcp.read_list.project_scope import (
    project_rows,
    unknown_project_message,
)
from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    LISTABLE_TYPES,
    EntityHandler,
    PageContext,
    resolve_entity_type,
)
from opik_mcp.read_list.sorting import SortError, compile_sort
from opik_mcp.read_list.window import (
    WindowError,
    is_relative,
    parse_instant,
    resolve_window,
    to_minute,
)

logger = logging.getLogger("opik_mcp.read_list.list")

_MAX_SIZE = 100
_TRUNCATE_AT = 60
# Free-text search is an ilike across several columns; on a cold cache the
# backend took 32 s live. Everything else keeps the client's 30 s default.
_SEARCH_TIMEOUT_S = 60.0

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
    status: str | None = None,
    settings: Settings | None = None,
    client: OpikListClient | None = None,
) -> str:
    """List tool entrypoint. See ``server.py`` for the registered tool."""
    entity_type = resolve_entity_type(entity_type)
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
    }
    for key, value in candidates.items():
        if value is not None and key in accepted:
            kw[key] = value

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

    to_time: str | None = None
    if since is not None or until is not None:
        day_windowed = "from_date" in handler.list_optional_kwargs
        if entity_type not in WINDOWED_ENTITIES and not day_windowed:
            windowed = ", ".join((*WINDOWED_ENTITIES, "agent_insights_issue"))
            why = (
                "experiments have no time window on the backend."
                if entity_type == "experiment"
                else f"only {windowed} take a time window."
            )
            unsupported = WindowError(f"since/until are not supported for {entity_type!r}: {why}")
            raise ToolError(str(unsupported)) from unsupported
        try:
            from_time, to_time = resolve_window(since, until)
        except WindowError as err:
            raise ToolError(str(err)) from err
        if day_windowed:
            # Diagnostics aggregates per report day, so the backend takes
            # dates; the instant window is truncated to its UTC days.
            if since is not None and from_time is not None:
                kw["from_date"] = from_time[:10]
                applied.append(f"since: {kw['from_date']}")
            if until is not None and to_time is not None:
                kw["to_date"] = to_time[:10]
                applied.append(f"until: {kw['to_date']}")
        else:
            if since is not None and from_time is not None:
                kw["from_time"] = from_time
                applied.append(f"since: {_window_echo(since, from_time)}")
            if until is not None and to_time is not None:
                kw["to_time"] = to_time
                applied.append(f"until: {_window_echo(until, to_time)}")

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

    resolved_settings = settings or get_settings()
    if client is not None:
        opik = client
    else:
        # Free-text search can take the backend >30 s on a cold cache (seen
        # live: 32 s); give only those calls a longer leash.
        timeout = _SEARCH_TIMEOUT_S if "search" in kw else None
        opik = make_opik_client(resolved_settings, timeout=timeout)

    try:
        page_body = await handler.list_fn(opik, **kw)
    except EntityArgValidationError as e:
        # A list_fn may reject its own arguments (e.g. a project_name that
        # resolves to no or several projects). Surface it as the same typed
        # validation error the tool raises for a missing parent id.
        raise ToolError(str(e)) from e
    except OpikNotFoundError as e:
        # The backend's 404 for a misspelled project names it ("Project name: X
        # not found"); only that case gets the did-you-mean recovery.
        if kw.get("project_name") and kw["project_name"] in str(e):
            raise ToolError(await unknown_project_message(opik, kw["project_name"])) from e
        raise ToolError(f"Failed to list {entity_type}s: {e}") from e
    except (OpikAuthError, OpikValidationError, OpikServerError) as e:
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
    # What the page knows about itself, for an entity whose registry entry has
    # something to add. ``windowed``: Diagnostics issues take a report-day
    # window, so a page under one says nothing about the project outside it.
    page_ctx = PageContext(
        project_id=kw.get("project_id"),
        project_name=kw.get("project_name"),
        empty=not content,
        status=kw.get("status"),
        windowed="from_date" in kw or "to_date" in kw,
        window_end=parse_instant(to_time) if to_time else None,
    )

    if not content:
        # "No agent constraints" = nothing but the default source clause, no
        # window, no search. A sort does not change what matches.
        unconstrained = source_defaulted and len(clauses) == 1 and "search" not in kw
        empty = await _empty_message(
            opik,
            handler,
            name=name,
            from_time=kw.get("from_time"),
            unconstrained=unconstrained,
            settings=resolved_settings,
            page_ctx=page_ctx,
        )
        return f"{header}\n{empty}" if header else empty

    extra = _requested_columns(sort_field, clauses)
    table = _format_table(entity_type, handler, content, total, page, size, name, extra)
    if handler.page_note_fn is not None:
        note = await handler.page_note_fn(opik, resolved_settings, page_ctx)
        if note is not None:
            table = f"{table}\n\n{note}"
    return f"{header}\n{table}" if header else table


_SOURCE_HINT = (
    'Only source = "sdk" rows are listed by default; add source = "experiment", '
    '"evaluator" or "playground" to filters to see the others.'
)


async def _empty_message(
    opik: OpikListClient,
    handler: EntityHandler,
    *,
    name: str | None,
    from_time: str | None,
    unconstrained: bool,
    settings: Settings,
    page_ctx: PageContext,
) -> str:
    """The empty-page reply, with the one hint that explains it when we can.

    Two cases seen live look identical without help: a project holding only
    experiment traces under the ``source = "sdk"`` default, and a window that
    starts after the project's last trace. The first costs nothing to explain;
    the second costs one project read, spent only on an empty windowed page.

    An entity whose empty page has its own ambiguity (a Diagnostics issue
    list: never enabled, off, unscanned, or genuinely clean) explains itself
    through its registry ``page_note_fn``.
    """
    entity_type = handler.entity_type
    project_id, project_name = page_ctx.project_id, page_ctx.project_name
    empty = f"No {entity_type}s matching {name!r} found." if name else f"No {entity_type}s found."
    if handler.page_note_fn is not None:
        note = await handler.page_note_fn(opik, settings, page_ctx)
        return f"{empty} {note}" if note else empty
    if from_time is None:
        return f"{empty} {_SOURCE_HINT}" if unconstrained else empty

    rows = await project_rows(opik, name=project_name)
    match = [
        p
        for p in rows
        if (p.get("name") == project_name if project_name else p.get("id") == project_id)
    ]
    if not match:
        return empty
    last = match[0].get("last_updated_trace_at")
    if not last:
        return f"{empty} This project has no traces yet."
    last_dt, start_dt = parse_instant(str(last)), parse_instant(from_time)
    if last_dt is None or start_dt is None:
        return empty
    if last_dt < start_dt:
        return f"{empty} Last trace in this project: {to_minute(last_dt)}, before your window."
    # Traffic exists inside the window, so the default source is what hid it.
    return f"{empty} {_SOURCE_HINT}" if unconstrained else empty


def _window_echo(raw: str, resolved: str) -> str:
    """``30d (2026-08-09T11:03Z)`` for shorthand, the bound to the minute for ISO."""
    resolved_dt = parse_instant(resolved)
    minute = to_minute(resolved_dt) if resolved_dt is not None else resolved
    if is_relative(raw):
        return f"{raw.strip()} ({minute})"
    return minute


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

    col_header = " | ".join(_COLUMN_LABELS.get(c, c) for c in columns)
    rows: list[str] = []
    for item in content:
        values: list[str] = []
        for col in columns:
            s = _render(col, _cell(item, col))
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


# Header labels that carry the unit the backend leaves implicit. The field
# keeps its backend name in filters/sort (``duration > 5000``); only the
# column heading says ``_ms`` so the agent never mistakes 82.461 for seconds.
_COLUMN_LABELS = {"duration": "duration_ms", "ttft": "ttft_ms"}
_ISO_WITH_FRACTION = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)$")


def _render(col: str, val: Any) -> str:
    """One cell, compact: whole milliseconds, seconds-precision timestamps,
    plain decimals. Every page pays for every character here."""
    if val is None:
        return ""
    if isinstance(val, float) and not math.isfinite(val):
        return ""
    if col in _COLUMN_LABELS and isinstance(val, int | float) and not isinstance(val, bool):
        # Half-up, not banker's: 82.5 ms reads as 83, the way a person rounds.
        return str(Decimal(repr(val)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if isinstance(val, float):
        text = repr(val)
        if "e" in text or "E" in text:
            return format(Decimal(text), "f")
        return text
    if isinstance(val, str):
        m = _ISO_WITH_FRACTION.match(val)
        if m:
            tz = "Z" if m.group(2) in ("Z", "+00:00") else m.group(2)
            return m.group(1) + tz
    return str(val)


__all__ = ["run_list"]
