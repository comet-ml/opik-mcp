"""A collection call's arguments, checked and turned into the backend's.

``since`` / ``until`` is one vocabulary for every windowed type: an instant
window (``from_time`` / ``to_time``) for trace, span and thread, and a
report-day window (``from_date`` / ``to_date``) for Diagnostics issues, whose
backend aggregates by day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler, ListKwargs, Vocabulary
from opik_mcp.read_list.oql import OQLError, compile_filters, render_filters, split_param_clauses
from opik_mcp.read_list.oql_fields import PARENT_ID_FIELDS, SDK_SOURCE_CLAUSE
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.registry import (
    ENTITY_REGISTRY,
    FILTERABLE_TYPES,
    SORTABLE_TYPES,
    VOCABULARIES,
    WINDOWED_TYPES,
)
from opik_mcp.read_list.sorting import SortError, compile_sort
from opik_mcp.read_list.window import (
    WindowError,
    is_relative,
    parse_instant,
    resolve_window,
    to_minute,
)

#: The entities whose backend takes a window as whole UTC days, named in the
#: refusal beside the instant-windowed ones.
_DAY_WINDOWED_TYPES: tuple[str, ...] = tuple(
    entity_type
    for entity_type, handler in ENTITY_REGISTRY.items()
    if "from_date" in handler.list_optional_kwargs
)


#: The tool arguments that pass through to a ``list_fn`` unchanged, when it takes them.
_PassedKwarg = Literal["project_id", "project_name", "dataset_id", "prompt_id", "status"]


@dataclass(frozen=True)
class ListArgs:
    """What ``run_list`` sends and echoes for one collection call."""

    page: int
    size: int
    vocabulary: Vocabulary
    list_kwargs: ListKwargs
    applied: list[str]
    clauses: list[dict[str, str]]
    is_source_defaulted: bool
    #: Where the sort label goes in ``applied`` once the response says
    #: whether the backend kept the sort.
    sort_slot: int
    sort_label: str | None
    sort_field: str | None
    to_time: str | None


def resolve_list_args(
    handler: EntityHandler,
    entity_type: str,
    *,
    name: str | None,
    filters: str | None,
    sort: str | None,
    since: str | None,
    until: str | None,
    search: str | None,
    page: int,
    size: int,
    project_id: str | None,
    project_name: str | None,
    dataset_id: str | None,
    prompt_id: str | None,
    status: str | None,
) -> ListArgs:
    """Every argument refusal after the entity type is known is raised here,
    as a ``ToolError``, before a connection is opened."""
    size = clamp_size(size)
    page = max(1, page)

    list_kwargs: ListKwargs = {"page": page, "size": size}
    # Only an endpoint that matches names is sent one; the others have no such parameter.
    if name and handler.is_name_searchable:
        list_kwargs["name"] = name
    # Entity-specific kwargs are forwarded only when the registry entry declares
    # them (required or optional). A parent id meant for another entity, or
    # project scope on a workspace-wide list, would otherwise reach the client
    # as an unexpected kwarg. ``project_name`` rides along with ``project_id``:
    # every project-scoped client method accepts either.
    accepted = set(handler.list_required_kwargs) | set(handler.list_optional_kwargs)
    if "project_id" in accepted:
        accepted.add("project_name")
    candidates: dict[_PassedKwarg, str | None] = {
        "project_id": project_id,
        "project_name": project_name,
        "dataset_id": dataset_id,
        "prompt_id": prompt_id,
        "status": status,
    }
    for key, value in candidates.items():
        if value is not None and key in accepted:
            list_kwargs[key] = value

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
    vocabulary = _vocabulary(handler.list_vocabulary or entity_type)

    applied: list[str] = []
    clauses: list[dict[str, str]] = []
    source_defaulted = False
    if vocabulary.filter_fields or filters:
        try:
            clauses = compile_filters(vocabulary, filters or "", filterable_types=FILTERABLE_TYPES)
        except OQLError as err:
            raise ToolError(str(err)) from err
        if vocabulary.is_source_defaulted and not any(
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
    if handler.is_windowed:
        # Bodies never reach the table, so let the backend trim them.
        list_kwargs["should_truncate"] = True
    # The sort label is filled in after the response (the backend may have
    # dropped the sort), but it belongs right after the filters in the header.
    sort_slot = len(applied)

    to_time: str | None = None
    if since is not None or until is not None:
        day_windowed = "from_date" in handler.list_optional_kwargs
        if not handler.is_windowed and not day_windowed:
            windowed = ", ".join((*WINDOWED_TYPES, *_DAY_WINDOWED_TYPES))
            why = handler.no_window_reason or f"only {windowed} take a time window."
            unsupported = WindowError(f"since/until are not supported for {entity_type!r}: {why}")
            raise ToolError(str(unsupported)) from unsupported
        try:
            from_time, to_time = resolve_window(since=since, until=until)
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
        if not handler.is_windowed:
            # Refused, not dropped. This used to return the whole unfiltered
            # page under a header saying "search ignored" — and an agent that
            # skims the header reads thirty-two rows as the result of the
            # search it asked for. A page that is not what was asked for is
            # worse than an error, and the error can name what would work.
            refusal = EntityArgValidationError(_search_refusal(handler, vocabulary))
            raise ToolError(str(refusal)) from refusal
        list_kwargs["search"] = search
        applied.append(f'search: "{search}"')

    sort_label: str | None = None
    sort_field: str | None = None
    if sort is not None:
        try:
            sort_field, direction = compile_sort(vocabulary, sort, sortable_types=SORTABLE_TYPES)
        except SortError as err:
            raise ToolError(str(err)) from err
        list_kwargs["sorting"] = json.dumps(
            [{"field": sort_field, "direction": direction}], separators=(",", ":")
        )
        sort_label = f"sort: {sort_field} {direction.lower()}"
    return ListArgs(
        page=page,
        size=size,
        vocabulary=vocabulary,
        list_kwargs=list_kwargs,
        applied=applied,
        clauses=clauses,
        is_source_defaulted=source_defaulted,
        sort_slot=sort_slot,
        sort_label=sort_label,
        sort_field=sort_field,
        to_time=to_time,
    )


def _vocabulary(name: str) -> Vocabulary:
    """The field table ``name`` names, or an empty one: a table-less entity
    refuses filters and sort in the same words as any other."""
    return VOCABULARIES.get(name) or Vocabulary(name=name)


def _search_refusal(handler: EntityHandler, vocabulary: Vocabulary) -> str:
    """Why free text does not apply here, and the nearest thing that does.

    Every workspace-wide list takes a ``name`` substring, and the filterable
    ones take OQL, so the caller who reached for ``search`` almost always has
    a query that works one keyword away.
    """
    alternatives: list[str] = []
    if handler.is_name_searchable:
        alternatives.append("match a name with name=<substring>")
    if vocabulary.filter_fields:
        # The nearest thing to free text the entity has: one ilike over a whole
        # payload where there is one, a key of one otherwise.
        fields = vocabulary.filter_fields
        if "full_data" in fields:
            example = 'full_data contains "…"'
        elif "metadata" in fields:
            example = 'metadata.<key> = "…"'
        else:
            example = 'a field = "…"'
        alternatives.append(
            f'narrow with filters (e.g. {example}; schema("list.{vocabulary.name}"))'
        )
    joined = "; or ".join(alternatives)
    how = f" {joined[0].upper()}{joined[1:]}." if joined else ""
    return (
        f"search is not supported for {vocabulary.entity_type!r}: "
        f"only {', '.join(WINDOWED_TYPES)} take free text.{how}"
    )


def _window_echo(raw: str, resolved: str) -> str:
    """``30d (2026-08-09T11:03Z)`` for shorthand, the bound to the minute for ISO."""
    resolved_dt = parse_instant(resolved)
    minute = to_minute(resolved_dt) if resolved_dt is not None else resolved
    if is_relative(raw):
        return f"{raw.strip()} ({minute})"
    return minute
