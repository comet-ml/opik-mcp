"""``list`` tool — paginated discovery and search of Opik entities.

Ported from ollie-assist's ``tools/list.py``. Output is a pipe-delimited
table (mirrors ollie's format) — easier for the LLM to scan than nested
JSON and lossless for the columns we care about (id, name, plus a few
entity-specific fields like ``created_at`` / ``dataset_name``). An entity
whose records have no fixed fields (``dataset_item``, whose payload is a
user-shaped ``data`` map) chooses its columns from the page instead, through
the registry's ``list_projection_fn``. Whatever the table cuts — a long value,
a column it had no room for — it says so under the rows.

Every page also names the fields its records carry, and ``fields=[…]`` takes
any of them: the columns are then the caller's, in their order, uncut, with the
id that opens the next level kept whether or not it was asked for. See
``projection``, which owns the three rules that keep that from becoming a
silent cut.

Project-scoped lists (``trace``, ``span``, ``thread``, ``agent_insights_issue``,
``dataset_item``, ``prompt_version``) require their parent id via
``project_id`` / ``dataset_id`` / ``prompt_id`` — enforced via the
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
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
    client_for_call,
)
from opik_mcp.read_list.columns import has_value, one_line
from opik_mcp.read_list.columns import resolve as resolve_column
from opik_mcp.read_list.decorations import page_note_of
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler, ListFn, PageContext, RunFn, Vocabulary
from opik_mcp.read_list.oql import (
    FILTERABLE_FIELDS,
    NAME_SEARCHABLE_ENTITIES,
    PARENT_ID_FIELDS,
    SDK_SOURCE_CLAUSE,
    SOURCE_DEFAULTED_ENTITIES,
    SOURCE_VALUES,
    WINDOWED_ENTITIES,
    OQLError,
    called,
    compile_filters,
    operand_values,
    render_filters,
    split_param_clauses,
)
from opik_mcp.read_list.paging import DEFAULT_PAGE_SIZE, clamp_size
from opik_mcp.read_list.project_scope import (
    project_rows,
    remember_resolved_project,
    unknown_project_message,
)
from opik_mcp.read_list.projection import check as check_fields
from opik_mcp.read_list.projection import (
    covers,
    fields_line,
    leaves,
    marker,
    normalise,
    row_fields,
)
from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    LISTABLE_TYPES,
    SORTABLE_TYPES,
    VOCABULARIES,
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

_TRUNCATE_AT = 60
#: The cell cap for a projected page: none. Written as a number rather than as
#: ``None`` so the one comparison in the renderer stays a comparison — a field
#: the caller named is returned whole, and a page of them is as long as the
#: caller asked for, the same bargain ``read`` makes (see ``size.py``).
_UNCUT = 10**9

#: What the last ``list`` call learned about its own page, for the analytics
#: props the tool wrapper attaches after the call returns. A ContextVar and
#: not a module global because two calls can be in flight on one server, and
#: each must read its own page. Set fresh at the top of every call.
_PAGE_FACTS: ContextVar[dict[str, str] | None] = ContextVar("list_page_facts", default=None)


def page_facts() -> dict[str, str]:
    """``empty`` and ``source_defaulted`` for the call that just returned.

    Whether a page was empty, and whether the ``sdk`` default was on it, is
    what tells "the default hid the traces" apart from "there were none" in a
    dashboard — a question the arguments alone cannot answer. Empty before
    any call, and after a call that never reached a page (a runner, or a
    refusal).
    """
    return dict(_PAGE_FACTS.get() or {})


# Free-text search is an ilike across several columns; on a cold cache the
# backend took 32 s live. Everything else keeps the client's 30 s default.
_SEARCH_TIMEOUT_S = 60.0

#: The entities whose backend takes a window as whole UTC days, named in the
#: refusal beside the instant-windowed ones.
_DAY_WINDOWED_TYPES: tuple[str, ...] = tuple(
    entity_type
    for entity_type, handler in ENTITY_REGISTRY.items()
    if "from_date" in handler.list_optional_kwargs
)


@contextmanager
def _as_tool_error(what: str, *, on_timeout: str) -> Iterator[None]:
    """Turn a failed call into the one sentence the agent will read.

    The two paths through this tool — a collection page and a metric series —
    map the same six failures, and had drifted into two copies of the mapping
    that differed only in their nouns. A third path would have made three.

    A ``ToolError`` raised inside passes through untouched, which is what lets
    a caller handle one case its own way (the 404 that names a misspelled
    project) and leave the rest here.
    """
    try:
        yield
    except ToolError:
        raise
    except (EntityArgValidationError, OQLError) as err:
        # Already written for the agent, with the valid values in it.
        raise ToolError(str(err)) from err
    except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as err:
        raise ToolError(f"Failed to {what}: {err}") from err
    except httpx.TimeoutException as err:
        # ``str(httpx.ReadTimeout)`` is often empty, so without this the agent
        # sees an error with no text at all.
        raise ToolError(on_timeout) from err
    except httpx.HTTPError as err:
        raise ToolError(f"Could not reach Opik to {what}: {err}") from err


async def _run_whole(
    handler: EntityHandler,
    entity_type: str,
    *,
    settings: Settings | None,
    client: OpikListClient | None,
    **tool_args: Any,
) -> str:
    """Own the connection, then hand the whole call to the entity's runner.

    Same lifecycle as the collection path: the answer may be one backend call,
    but resolving a project name is another, and both ride one connection.

    The words an upstream failure becomes come from the handler, because there
    is more than one runner now and they are not doing the same thing: a
    comparison that opik-backend refuses used to report that it had failed to
    *chart* a dataset_item, and to suggest widening an interval it has not
    got.
    """
    run = cast("RunFn", handler.run_fn)
    resolved_settings = settings or get_settings()
    async with client_for_call(resolved_settings, client) as opik:
        with _as_tool_error(
            f"{handler.run_verb} {entity_type}",
            on_timeout=handler.run_timeout_hint
            or f"Opik did not answer in time for list({entity_type!r}, …). Retry with a smaller "
            "page (size=…).",
        ):
            answer = await run(cast("OpikReadClient", opik), **tool_args)
        page_note = page_note_of(handler)
        if page_note is None:
            return answer
        # A runner's answer is not a collection, but it is still a page someone
        # may want to open — a metric is a chart on the Dashboards page. The
        # note hook was reachable only from the collection path, so an entity
        # that answers whole could declare one and never have it called.
        note = await page_note(
            opik,
            resolved_settings,
            PageContext(
                project_id=tool_args.get("project_id"),
                project_name=tool_args.get("project_name"),
            ),
        )
        return f"{answer}\n\n{note}" if note else answer


def _whole_call(handler: EntityHandler, tool_args: dict[str, Any]) -> bool:
    """Does this call belong to the entity's runner, or to the collection path?

    An entity with no ``run_when_kwargs`` answers every call through its
    runner. One that declares them answers two different questions, and the
    arguments say which was asked.
    """
    if not handler.run_when_kwargs:
        return True
    return any(tool_args.get(arg) is not None for arg in handler.run_when_kwargs)


async def run_list(
    entity_type: str,
    *,
    name: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    fields: list[str] | None = None,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    project_id: str | None = None,
    project_name: str | None = None,
    dataset_id: str | None = None,
    prompt_id: str | None = None,
    experiment_ids: list[str] | None = None,
    status: str | None = None,
    metric_type: str | None = None,
    interval: str | None = None,
    breakdown: str | None = None,
    series: str | None = None,
    settings: Settings | None = None,
    client: OpikListClient | None = None,
) -> str:
    """List tool entrypoint. See ``server.py`` for the registered tool."""
    _PAGE_FACTS.set({})
    # Cleared per call for the same reason the page facts are: a project a
    # previous listing resolved is not a fact about this one, and a link
    # built from it would point into the wrong project — the exact failure
    # this whole feature exists to stop.
    remember_resolved_project(None)
    entity_type = resolve_entity_type(entity_type)
    handler = ENTITY_REGISTRY.get(entity_type)
    tool_args: dict[str, Any] = {
        "name": name,
        "filters": filters,
        "sort": sort,
        "since": since,
        "until": until,
        "search": search,
        "fields": fields,
        "project_id": project_id,
        "project_name": project_name,
        "dataset_id": dataset_id,
        "prompt_id": prompt_id,
        "experiment_ids": experiment_ids,
        "status": status,
        "metric_type": metric_type,
        "interval": interval,
        "breakdown": breakdown,
        "series": series,
    }
    if handler is not None and handler.run_fn is not None and _whole_call(handler, tool_args):
        # The entity answers on its own, because what it answers is not a
        # collection: a time series' rows are buckets, and a comparison's are
        # cases with several runs on each. Both need the tool's arguments and
        # none of its table. ``page``/``size`` carry non-None defaults, so only
        # a value the caller actually chose is passed on; the defaults reaching
        # a runner cannot be told from absence and are harmless.
        return await _run_whole(
            handler,
            entity_type,
            settings=settings,
            client=client,
            page=page if page != 1 else None,
            size=size if size != DEFAULT_PAGE_SIZE else None,
            **tool_args,
        )

    # Checked after the runner branch rather than before it, so that reaching
    # the collection path is what proves there is a ``list_fn`` to drive.
    if handler is None or handler.list_fn is None:
        valid = ", ".join(sorted(LISTABLE_TYPES))
        err = EntityArgValidationError(f"Cannot list {entity_type!r}. Listable types: {valid}")
        raise ToolError(str(err)) from err

    size = clamp_size(size)
    page = max(1, page)

    list_kwargs: dict[str, Any] = {"page": page, "size": size}
    if name:
        list_kwargs["name"] = name
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
        "dataset_id": dataset_id,
        "prompt_id": prompt_id,
        "status": status,
    }
    list_kwargs.update(
        {key: value for key, value in candidates.items() if value is not None and key in accepted}
    )

    for required in handler.list_required_kwargs:
        if list_kwargs.get(required) is None:
            # project_name is an accepted alternative to project_id for the
            # project-scoped lists (trace, span, thread) — the client methods
            # take either, so don't force the UUID when a name was given.
            if required == "project_id" and list_kwargs.get("project_name"):
                continue
            hint = f"{required} (or project_name)" if required == "project_id" else required
            err = EntityArgValidationError(
                f"list({entity_type!r}) requires {hint}. "
                f"E.g. list({entity_type!r}, {required}='<uuid>', …)."
            )
            raise ToolError(str(err)) from err

    # Which field table this call's filters and sort are checked against. It
    # is the entity's own name everywhere but one: a dataset item listed under
    # its dataset is filtered on the case, and listed with experiments on the
    # runs, and the two endpoints share no field but ``id``. The registry row
    # says which, so this stays a lookup rather than a branch on a name.
    vocabulary = handler.list_vocabulary or entity_type

    applied: list[str] = []
    clauses: list[dict[str, str]] = []
    source_defaulted = False
    if vocabulary in FILTERABLE_FIELDS or filters:
        try:
            clauses = compile_filters(vocabulary, filters or "")
        except OQLError as err:
            raise ToolError(str(err)) from err
        if entity_type in SOURCE_DEFAULTED_ENTITIES and not any(
            c["field"] in ("source", *PARENT_ID_FIELDS) for c in clauses
        ):
            # The SDK default is for triage — "which traces need attention" —
            # where the Logs page filters the same way. A filter on a parent's
            # id is a drill-in: the caller named the trace or thread and wants
            # all of it, the way read() inlines all of it. Adding the default
            # there would make the continuation a composite read hands out
            # (`moreSpans`) return a different set than the part it continues.
            clauses.append(dict(SDK_SOURCE_CLAUSE))
            source_defaulted = True
        if clauses:
            # A few fields are query parameters to the backend rather than
            # entries in the filter array. They are lifted out here, and the
            # header still echoes the whole list: a clause that narrowed the
            # page and went unmentioned would under-report what was applied.
            try:
                sent, params = split_param_clauses(vocabulary, clauses)
            except OQLError as err:
                raise ToolError(str(err)) from err
            list_kwargs.update(params)
            if sent:
                list_kwargs["filters"] = json.dumps(sent, separators=(",", ":"))
            applied.append(f"filters: {render_filters(vocabulary, clauses)}")
    if entity_type in WINDOWED_ENTITIES:
        # Bodies never reach the table, so let the backend trim them.
        list_kwargs["truncate"] = True
    # The sort label is filled in after the response (the backend may have
    # dropped the sort), but it belongs right after the filters in the header.
    sort_slot = len(applied)

    to_time: str | None = None
    if since is not None or until is not None:
        day_windowed = "from_date" in handler.list_optional_kwargs
        if entity_type not in WINDOWED_ENTITIES and not day_windowed:
            windowed = ", ".join((*WINDOWED_ENTITIES, *_DAY_WINDOWED_TYPES))
            why = handler.no_window_reason or f"only {windowed} take a time window."
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
                list_kwargs["from_date"] = from_time[:10]
                applied.append(f"since: {list_kwargs['from_date']}")
            if until is not None and to_time is not None:
                list_kwargs["to_date"] = to_time[:10]
                applied.append(f"until: {list_kwargs['to_date']}")
        else:
            if since is not None and from_time is not None:
                list_kwargs["from_time"] = from_time
                applied.append(f"since: {_window_echo(since, from_time)}")
            if until is not None and to_time is not None:
                list_kwargs["to_time"] = to_time
                applied.append(f"until: {_window_echo(until, to_time)}")

    if search is not None and search.strip():
        if entity_type not in WINDOWED_ENTITIES:
            # Refused, not dropped. This used to return the whole unfiltered
            # page under a header saying "search ignored" — and an agent that
            # skims the header reads thirty-two rows as the result of the
            # search it asked for. A page that is not what was asked for is
            # worse than an error, and the error can name what would work.
            refusal = EntityArgValidationError(_search_refusal(vocabulary))
            raise ToolError(str(refusal)) from refusal
        list_kwargs["search"] = search
        applied.append(f'search: "{search}"')

    sort_label: str | None = None
    sort_field: str | None = None
    if sort is not None:
        try:
            sort_field, direction = compile_sort(
                VOCABULARIES.get(vocabulary) or Vocabulary(name=vocabulary),
                sort,
                sortable_types=SORTABLE_TYPES,
            )
        except SortError as err:
            raise ToolError(str(err)) from err
        list_kwargs["sorting"] = json.dumps(
            [{"field": sort_field, "direction": direction}], separators=(",", ":")
        )
        sort_label = f"sort: {sort_field} {direction.lower()}"

    # A list can be several backend calls: resolving a project name, the
    # listing itself, and the did-you-mean / empty-result lookups, plus an
    # entity's own page note. The connection is owned for the span of this
    # call so they share it.
    #
    # Free-text search can take the backend >30 s on a cold cache (seen live:
    # 32 s); give only those calls a longer leash.
    resolved_settings = settings or get_settings()
    search_timeout = _SEARCH_TIMEOUT_S if "search" in list_kwargs else None
    async with client_for_call(settings, client, timeout=search_timeout) as opik:
        with _as_tool_error(
            f"list {entity_type}s",
            on_timeout=(
                f"Opik did not answer in time for list({entity_type!r}, …). Narrow the query — "
                "a shorter since window, fewer filters, a smaller size, or drop search — "
                "and retry."
            ),
        ):
            try:
                page_body = await handler.list_fn(opik, **list_kwargs)
            except OpikNotFoundError as e:
                # The backend's 404 for a misspelled project names it ("Project
                # name: X not found"); only that case gets the did-you-mean
                # recovery, and the rest fall through to the mapping above.
                if list_kwargs.get("project_name") and list_kwargs["project_name"] in str(e):
                    raise ToolError(
                        await unknown_project_message(opik, list_kwargs["project_name"])
                    ) from e
                raise

        content_raw = page_body.get("content") or []
        content: list[dict[str, Any]] = [it for it in content_raw if isinstance(it, dict)]
        if handler.list_link_fn is not None:
            # A url per row, for the listing that cannot share one template.
            # Attached here rather than in ``list_row_fn`` because it needs the
            # session's settings, which a row hook is not given: where Opik
            # lives is a fact about this connection, not about the record.
            content = [
                {**row, "url": url}
                if (url := handler.list_link_fn(resolved_settings, row)) is not None
                else row
                for row in content
            ]
        total_raw = page_body.get("total")
        total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else len(content)

        if sort_label is not None:
            # The backend blanks ``sortable_by`` when it dropped sorting for a large
            # workspace — the only signal that the page is not actually ordered.
            if page_body.get("sortable_by") == []:
                sort_label += " (dropped by the backend for this workspace size; page is unsorted)"
                # Nothing downstream may read this page as ordered: the
                # ranking caveat once fired here and told the caller the first
                # two rows were ranked, on a page the header called unsorted.
                sort_field = None
            applied.insert(sort_slot, sort_label)

        wanted = normalise(fields)
        if wanted is not None:
            # Echoed with the filters and the sort because it is the same kind
            # of fact: something the caller asked for that the page in front of
            # them does not otherwise state.
            applied.append(f"fields: {', '.join(wanted)}")
        header = f"[list: {entity_type} | {' | '.join(applied)}]" if applied else None
        # What the page knows about itself, for an entity whose registry entry
        # has something to add. ``windowed``: Diagnostics issues take a
        # report-day window, so a page under one says nothing about the
        # project outside it.
        page_ctx = PageContext(
            project_id=list_kwargs.get("project_id"),
            project_name=list_kwargs.get("project_name"),
            parent_id=next(
                (
                    value
                    for field in handler.list_required_kwargs
                    if field != "project_id"
                    and isinstance(value := list_kwargs.get(field), str)
                    and value
                ),
                None,
            ),
            empty=not content,
            status=list_kwargs.get("status"),
            windowed="from_date" in list_kwargs or "to_date" in list_kwargs,
            window_end=parse_instant(to_time) if to_time else None,
            page=page,
            total=total,
            filtered=bool(filters and filters.strip()),
            sort_field=sort_field,
            rows=tuple(content),
        )

        _PAGE_FACTS.set(
            {"empty": str(not content).lower(), "source_defaulted": str(source_defaulted).lower()}
        )
        if not content:
            # Under the sdk default, the one question worth a call is whether
            # widening it would find anything. Only when the page's own total
            # is zero: a slice past the last page has rows, on an earlier page.
            widened = (
                _without_default(entity_type, opik, handler.list_fn, list_kwargs, clauses)
                if source_defaulted and total == 0
                else None
            )
            unfiltered = (
                _without_filters(entity_type, opik, handler.list_fn, list_kwargs, clauses)
                if page_ctx.filtered and total == 0
                else None
            )
            unnamed = (
                _probe(
                    entity_type,
                    opik,
                    handler.list_fn,
                    {k: v for k, v in list_kwargs.items() if k != "name"},
                    [],
                )
                if name and total == 0
                else None
            )
            empty = await _empty_message(
                opik,
                handler,
                name=name,
                from_time=list_kwargs.get("from_time"),
                widened=widened,
                unfiltered=unfiltered,
                unnamed=unnamed,
                settings=resolved_settings,
                page_ctx=page_ctx,
            )
            return f"{header}\n{empty}" if header else empty

        # A caller who named their columns did not ask for the sort and filter
        # fields to be appended to them; ``fields`` is the whole answer to
        # "which columns", so the request's own columns stand down.
        extra = _requested_columns(sort_field, clauses) if wanted is None else []
        try:
            table = _format_table(
                entity_type,
                handler,
                content,
                total,
                page,
                size,
                name,
                extra,
                _pinned_columns(clauses) if wanted is None else frozenset(),
                fields=wanted,
            )
        except EntityArgValidationError as err:
            # The only thing the table can refuse is a ``fields`` entry, and it
            # can only refuse it here: which names are valid is a fact about
            # the page, so the check cannot run before the page exists.
            raise ToolError(str(err)) from err
        page_note = page_note_of(handler)
        if page_note is not None:
            note = await page_note(opik, resolved_settings, page_ctx)
            if note is not None:
                table = f"{table}\n\n{note}"
        return f"{header}\n{table}" if header else table


def _search_refusal(entity_type: str) -> str:
    """Why free text does not apply here, and the nearest thing that does.

    Every workspace-wide list takes a ``name`` substring, and the filterable
    ones take OQL, so the caller who reached for ``search`` almost always has
    a query that works one keyword away.
    """
    alternatives: list[str] = []
    if entity_type in NAME_SEARCHABLE_ENTITIES:
        alternatives.append("match a name with name=<substring>")
    if entity_type in FILTERABLE_FIELDS:
        # The nearest thing to free text the entity has: one ilike over a whole
        # payload where there is one, a key of one otherwise.
        fields = FILTERABLE_FIELDS[entity_type]
        if "full_data" in fields:
            example = 'full_data contains "…"'
        elif "metadata" in fields:
            example = 'metadata.<key> = "…"'
        else:
            example = 'a field = "…"'
        alternatives.append(f'narrow with filters (e.g. {example}; schema("list.{entity_type}"))')
    joined = "; or ".join(alternatives)
    how = f" {joined[0].upper()}{joined[1:]}." if joined else ""
    return (
        f"search is not supported for {called(entity_type)!r}: "
        f"only {', '.join(WINDOWED_ENTITIES)} take free text.{how}"
    )


def _source_hint(entity_type: str, hidden: int) -> str:
    """What the ``sdk`` default hid, in numbers, and how to see it.

    Names every source the backend writes, ``optimization`` included: the
    optimizer's traces were the ones a caller went looking for and were not
    told about.
    """
    named = [v for v in SOURCE_VALUES if v not in ("sdk", "unknown")]
    choices = ", ".join(f'"{v}"' for v in named[:-1]) + f' or "{named[-1]}"'
    return (
        f"{hidden} {entity_type}{'s' if hidden != 1 else ''} match without the default "
        'source = "sdk", which is all that is listed unless you name a source. Add '
        f"source = {choices} to filters to see them."
    )


def _without_default(
    entity_type: str,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: dict[str, Any],
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """The same listing again, one row wide, with the ``sdk`` default lifted.

    An empty page under the default used to earn a hint only when the default
    was the sole clause — the guess being that a caller's own filter was the
    likelier reason. Driving the tool: ``experiment_id = "…"`` on an
    experiment with twenty traces got "No traces found" and no hint, because
    the guess was wrong and there was no way to know. So the hint is earned by
    asking: it fires when the widened count is non-zero, whatever the caller
    filtered on, and stays silent on a project that is genuinely empty.

    Returns ``None`` when the probe cannot answer; a note decorates an answer
    the caller already has, and must never turn it into an error.
    """

    return _probe(
        entity_type, opik, list_fn, list_kwargs, [c for c in clauses if c != SDK_SOURCE_CLAUSE]
    )


def _without_filters(
    entity_type: str,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: dict[str, Any],
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """The same listing again, one row wide, with the caller's filters lifted.

    Keeps the ``sdk`` default when the page applied it, so the count is of the
    rows the caller would otherwise have seen.
    """
    return _probe(
        entity_type, opik, list_fn, list_kwargs, [c for c in clauses if c == SDK_SOURCE_CLAUSE]
    )


def _probe(
    entity_type: str,
    opik: OpikListClient,
    list_fn: ListFn,
    list_kwargs: dict[str, Any],
    clauses: list[dict[str, str]],
) -> Callable[[], Awaitable[int | None]]:
    """One-row count of ``clauses``, or ``None`` when it cannot be had."""

    async def count() -> int | None:
        probe = {**list_kwargs, "page": 1, "size": 1}
        probe.pop("filters", None)
        try:
            # The same split the page went through: a clause that is a query
            # parameter must not turn back into a filter entry on the probe.
            sent, params = split_param_clauses(entity_type, clauses)
            probe.update(params)
            if sent:
                probe["filters"] = json.dumps(sent, separators=(",", ":"))
            body = await list_fn(opik, **probe)
        except Exception:
            logger.debug("empty-page probe failed", exc_info=True)
            return None
        found = body.get("total") if isinstance(body, dict) else None
        return found if isinstance(found, int) else None

    return count


async def _empty_message(
    opik: OpikListClient,
    handler: EntityHandler,
    *,
    name: str | None,
    from_time: str | None,
    widened: Callable[[], Awaitable[int | None]] | None,
    unfiltered: Callable[[], Awaitable[int | None]] | None,
    unnamed: Callable[[], Awaitable[int | None]] | None,
    settings: Settings,
    page_ctx: PageContext,
) -> str:
    """The empty-page reply, with the one hint that explains it when we can.

    Three cases seen live look identical without help: a project holding only
    experiment traces under the ``source = "sdk"`` default, a window that
    starts after the project's last trace, and a filter that matched none of a
    scope that is not itself empty. Each costs one extra call, spent only on an
    empty page.

    An entity whose empty page has its own ambiguity (a Diagnostics issue
    list: never enabled, off, unscanned, or genuinely clean) explains itself
    through its registry ``page_note_fn``, and its answer replaces the probes
    below — it knows something they cannot work out.

    A note of ``None`` is not that: it means the entity had nothing to add to
    *this* page, so the probes still run. The distinction matters now that a
    note can be about something other than emptiness — a link for opening a
    row has nothing to say about a page with no rows, and silencing the probes
    would have been an odd way to say so.
    """
    entity_type = handler.entity_type
    project_id, project_name = page_ctx.project_id, page_ctx.project_name
    empty = f"No {entity_type}s matching {name!r} found." if name else f"No {entity_type}s found."
    page_note = page_note_of(handler)
    if page_note is not None:
        note = await page_note(opik, settings, page_ctx)
        if note:
            return f"{empty} {note}"

    async def scoped() -> str:
        """What the narrowing matched none of, when nothing else explains it."""
        probe, lifted = (
            (unnamed, "that name") if unnamed is not None else (unfiltered, "your filter")
        )
        found = await probe() if probe is not None else None
        if not found:
            return empty
        plural = "s" if found != 1 else ""
        return (
            f"{empty} Without {lifted} this listing has {found} "
            f"{entity_type}{plural}; none of them match it."
        )

    async def hinted() -> str:
        hidden = await widened() if widened is not None else None
        return f"{empty} {_source_hint(entity_type, hidden)}" if hidden else await scoped()

    if from_time is None:
        return await hinted()

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
    return await hinted()


def _window_echo(raw: str, resolved: str) -> str:
    """``30d (2026-08-09T11:03Z)`` for shorthand, the bound to the minute for ISO."""
    resolved_dt = parse_instant(resolved)
    minute = to_minute(resolved_dt) if resolved_dt is not None else resolved
    if is_relative(raw):
        return f"{raw.strip()} ({minute})"
    return minute


# Filter fields that make no sense as a table column: bodies (never shown in a
# list), the error container (error_type carries the useful part), source (it
# is a scope, not a per-row fact) and experiment_ids (a selector naming the
# rows, which is what the id column already is — seen live as a blank column
# on every row it selected).
_NEVER_COLUMNS = frozenset(
    {
        "input",
        "output",
        "input_json",
        "output_json",
        "error_info",
        "source",
        "experiment_ids",
        # The whole payload as one string: a column of it would be every data
        # column again, and the record does not carry the field at all — the
        # cell would come back blank on every row it matched.
        "full_data",
    }
)


def _pinned(clause: dict[str, str]) -> bool:
    """Does this clause fix its field to one value?

    A column that reads the same on every row distinguishes nothing, and the
    applied-filters header above the table already states the value. Seen
    live: ``optimization_id = "…"`` repeated a 36-character id down the page,
    a tenth of its bytes. A range or a substring still earns its column — it
    explains an order or a membership the rows would not otherwise justify.
    """
    if clause["operator"] == "=":
        return True
    return clause["operator"] == "in" and len(operand_values("in", clause.get("value", ""))) == 1


def _column_of(clause: dict[str, str]) -> str:
    """The column a clause is about: ``field.key`` for a nested reference."""
    return f"{clause['field']}.{clause['key']}" if clause.get("key") else clause["field"]


def _requested_columns(sort_field: str | None, clauses: list[dict[str, str]]) -> list[str]:
    """Columns the request names — the sort field first, then filter fields in
    order of first mention. Nested references keep their ``field.key`` form."""
    out: list[str] = []
    if sort_field is not None:
        out.append(sort_field)
    for c in clauses:
        col = _column_of(c)
        if c["field"] not in _NEVER_COLUMNS and col not in out:
            out.append(col)
    return out


def _pinned_columns(clauses: list[dict[str, str]]) -> frozenset[str]:
    """The exact columns the request fixed to one value.

    Exact, not by root: ``feedback_scores.acc = 0.9`` pins one score, and the
    ``feedback_scores`` summary beside it still carries every other score;
    ``metadata.environment = "staging"`` says nothing about a
    ``metadata.region contains "eu"`` column on the same request.
    """
    return frozenset(_column_of(c) for c in clauses if _pinned(c))


def _projected_columns(
    handler: EntityHandler,
    content: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    """The columns of a projected page: the id, the named fields, the handle.

    The id leads because a row nobody can address again is a dead end, and the
    entity's ``list_identity_fields`` follow for the same reason one level
    down — an experiment's prompt version, say. Neither is added when no row
    on the page fills it: an empty column is a field the records lack, which
    is exactly the mistake the naming rule exists to prevent.

    An entity the backend addresses by name alone (``score_name``) leads with
    the name instead. It is not a second-best id, it is the id: a page of
    counts with nothing saying what was counted is the dead end this rule is
    about, in the one place where dropping ``id`` would have caused it.
    """
    handle = "id" if handler.list_has_id else ("name" if handler.list_has_name else None)
    lead = (handle,) if handle and any(has_value(row, handle) for row in content) else ()
    columns = [*lead]
    columns += [f for f in fields if f not in columns]
    for extra in handler.list_identity_fields:
        if extra not in columns and any(has_value(row, extra) for row in content):
            columns.append(extra)
    return tuple(columns)


def _render_cell(column: str, value: Any, *, cell_limit: int) -> tuple[str, bool]:
    """One cell, and whether the width cut took anything from it.

    The cut keeps a table scannable, which is worth a lot for a long output
    or a payload nobody reads to the end. It is worth nothing for a url: a
    link cut to 60 characters still looks like an address and opens nothing,
    which is the plausible-and-wrong failure this whole feature exists to
    stop — arriving from the renderer rather than the builder. A url is not
    read, it is clicked, so its column is exempt and no other is.
    """
    text = _render(column, value)
    if column == "url" or len(text) <= cell_limit:
        return text, False
    return text[: cell_limit - 3] + "...", True


def _format_table(
    entity_type: str,
    handler: EntityHandler,
    content: list[dict[str, Any]],
    total: int,
    page: int,
    size: int,
    name: str | None,
    extra_columns: list[str] | None = None,
    pinned: frozenset[str] = frozenset(),
    *,
    fields: tuple[str, ...] | None = None,
) -> str:
    """Pipe-delimited table — mirrors ollie's ``_format_table``.

    ``extra_columns`` are the fields the request sorted or filtered on; they
    are appended after the entity's default columns (deduplicated) so the
    table shows why each row is present and in what order. ``pinned`` are the
    fields a clause fixed to one value; a column of those reads the same on
    every row and the header above already states it, so it is dropped
    whichever way it got in — requested, or chosen by the entity from the page.

    ``fields`` replaces all of that. When the caller named their columns, the
    entity's choice, the request's own columns and the pinning rule are all
    answers to a question nobody asked any more: the columns are the named
    fields, in the order they were named, plus the ids that open the next
    level. The cell cut goes with them — "returns exactly those fields" is
    not a 60-character prefix of them.
    """
    base = tuple(
        column
        for column, present in (("id", handler.list_has_id), ("name", handler.list_has_name))
        if present
    )
    # Columns the record does not carry, computed before anything looks at
    # the page: the projection decides on them like any other column, and the
    # renderer resolves them like any other key.
    if handler.list_row_fn is not None:
        content = [handler.list_row_fn(item) for item in content]
    available = row_fields(content)
    if "error_type" not in available and any(
        _cell(row, "error_type") is not None for row in content
    ):
        # The one column the renderer derives rather than reads, out of the
        # error container. A table that shows a column the caller cannot then
        # name would be the naming rule failing on our own field, which is
        # exactly the guess the rule exists to spare them.
        available = tuple(sorted((*available, "error_type")))
    projection = None
    dropped: tuple[str, ...] = ()
    columns: tuple[str, ...]
    if fields is not None:
        check_fields(fields, available, whole=f"list({entity_type!r}, …)")
        columns = _projected_columns(handler, content, fields)
        # No cut: the caller named these fields to read them, and a value cut
        # to sixty characters is not the field, it is a prefix of it.
        cell_limit = _UNCUT
    else:
        projection = (
            handler.list_projection_fn(content) if handler.list_projection_fn is not None else None
        )
        chosen = projection.columns if projection is not None else handler.list_extra_fields
        cell_limit = projection.cell_limit if projection is not None else _TRUNCATE_AT
        columns = (*base, *chosen)
        for col in extra_columns or ():
            # ``feedback_scores.accuracy`` beside a ``feedback_scores`` column
            # that already renders every score as ``name=value`` is the same
            # number twice, and reads like a second metric. The summary wins.
            if col not in columns and col.partition(".")[0] not in columns:
                columns = (*columns, col)
        dropped = tuple(c for c in columns if c not in base and c in pinned)
        columns = tuple(c for c in columns if c not in dropped)
    count = len(content)
    if name:
        header = (
            f"Found {total} {entity_type}s matching {name!r} "
            f"(page {page}, showing {count} of {total}):"
        )
    else:
        header = f"Found {total} {entity_type}s (page {page}, showing {count} of {total}):"

    # The column names are data too: a dataset item's columns are the keys
    # the user chose for its ``data`` map, and one with a line break split
    # the header line in two while a bare pipe left the table with a name no
    # row had a cell for.
    col_header = " | ".join(one_line(_COLUMN_LABELS.get(c, c)) for c in columns)
    rows: list[str] = []
    cut = 0
    for item in content:
        values: list[str] = []
        for col in columns:
            text, was_cut = _render_cell(col, _cell(item, col), cell_limit=cell_limit)
            cut += was_cut
            values.append(text)
        rows.append(" | ".join(values))

    lines = [header, "", col_header, *rows]
    # What the table did to the data, said under the data. A value cut to fit
    # the row would otherwise read as the whole value, and a column the page
    # had no room for would read as a key the items never had.
    notes: list[str] = []
    if fields is not None:
        # One line does both jobs the page owes the caller: it says the answer
        # was projected (spec D3) and, by naming the rest, it says what could
        # have been asked for instead — which is the ``fields:`` line an
        # unprojected page carries, spent on the half that is still news.
        notes.append(
            marker(
                kept=columns,
                # ``covers``, not ``not in``: a caller who named
                # ``feedback_scores`` gets every score in that cell, and
                # listing feedback_scores.helpfulness as omitted beside the
                # cell rendering it is the marker contradicting the table.
                omitted=tuple(
                    c for c in leaves(available) if not any(covers(col, c) for col in columns)
                ),
                whole="Drop fields= for the row as the table chooses it.",
            )
        )
    if projection is not None and projection.note:
        notes.append(projection.note)
    if dropped:
        # The projection's note promised to account for every column the page
        # had; a column the filter pinned is left out after it decided.
        names = ", ".join(dropped)
        verb = "is" if len(dropped) == 1 else "are"
        notes.append(f"{names} {verb} pinned by the filter; the header states the value.")
    if cut:
        cut_line = f"{cut} value{'s' if cut != 1 else ''} cut at {cell_limit} chars"
        if projection is not None and projection.cut_hint:
            cut_line += f"; {projection.cut_hint}"
        notes.append(f"{cut_line}.")
    if notes:
        lines.append("")
        lines.extend(notes)
    if fields is None and (offer := fields_line(available)) is not None:
        # Every page names the fields its records carry, so the caller picks
        # from a list instead of guessing a path and getting a blank column.
        # Under the table's own notes: those say what happened to this answer,
        # and this says what a different one could be.
        if not notes:
            lines.append("")
        lines.append(offer)
    if page * size < total:
        lines.append("")
        lines.append(f"Use page={page + 1} for next {size} results.")
    if handler.list_footer is not None:
        lines.append("")
        lines.append(handler.list_footer)
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
    return resolve_column(item, col)


def _score_summary(scores: list[dict[str, Any]]) -> str:
    return ", ".join(f"{s.get('name')}={s.get('value')}" for s in scores if "name" in s)


# Header labels that carry the unit the backend leaves implicit. The field
# keeps its backend name in filters/sort (``duration > 5000``); only the
# column heading says ``_ms`` so the agent never mistakes 82.461 for seconds.
_COLUMN_LABELS = {
    "duration": "duration_ms",
    "ttft": "ttft_ms",
    # An experiment's duration is a set of percentiles rather than one number.
    # Same reasoning, one level down: the unit is not guessable from 1260.107,
    # and the label is what rounds the cell to whole milliseconds.
    "duration.p50": "duration.p50_ms",
    "duration.p90": "duration.p90_ms",
    "duration.p99": "duration.p99_ms",
}
_ISO_WITH_FRACTION = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)$")


def _render(col: str, val: Any) -> str:
    """One cell of the table: compact, and one cell.

    A name, a reason or a case's data can carry a line break or a bare pipe;
    either splits the row or adds a column to it. The escaping is applied
    here rather than at the call site so that rendering a cell and making it
    safe to put in a cell are one step — it is the same rule the comparison
    table applies, from the same place (``columns.one_line``).
    """
    return one_line(_compact(col, val))


def _compact(col: str, val: Any) -> str:
    """The value itself: whole milliseconds, seconds-precision timestamps,
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
        iso = _ISO_WITH_FRACTION.match(val)
        if iso:
            offset = "Z" if iso.group(2) in ("Z", "+00:00") else iso.group(2)
            return iso.group(1) + offset
    if isinstance(val, dict | list):
        # Compact JSON, not Python's repr: a nested value is still the value,
        # readable and pasteable, rather than a hint that one was there.
        return json.dumps(val, separators=(",", ":"), default=str)
    return str(val)


__all__ = ["run_list"]
