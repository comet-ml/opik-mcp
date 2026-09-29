"""``list`` tool — paginated discovery and search of Opik entities.

Ported from ollie-assist's ``tools/list.py``. A page renders as the table in
``list_table`` (id, name, plus a few entity-specific fields like
``created_at`` / ``dataset_name``). An entity whose records have no fixed
fields (``dataset_item``, whose payload is a user-shaped ``data`` map) chooses
its columns from the page instead, through the registry's
``list_projection_fn``.

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

Arguments are checked in ``list_args``; an empty page explains itself from
``list_empty_page``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, cast

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.base import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.client.opik import client_for_call
from opik_mcp.client.protocols import OpikListClient, OpikReadClient
from opik_mcp.config import Settings, get_settings
from opik_mcp.cost_intelligence import enabled_features
from opik_mcp.read_list.decorations import page_note_of
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler, PageContext, RunFn
from opik_mcp.read_list.list_args import resolve_list_args
from opik_mcp.read_list.list_empty_page import (
    empty_message,
    probe_count,
    without_default,
    without_filters,
)
from opik_mcp.read_list.list_table import format_table, pinned_columns, requested_columns
from opik_mcp.read_list.oql import OQLError
from opik_mcp.read_list.paging import DEFAULT_PAGE_SIZE
from opik_mcp.read_list.project_scope import (
    remember_resolved_project,
    resolve_project_id,
)
from opik_mcp.read_list.projection import normalise
from opik_mcp.read_list.registry import VOCABULARIES, resolve_entity_type
from opik_mcp.read_list.size import list_size_header, with_list_size
from opik_mcp.read_list.visibility import listable_types, visible_handler
from opik_mcp.read_list.window import parse_instant

logger = logging.getLogger("opik_mcp.read_list.list")

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
    settings: Settings,
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
    resolved_settings = settings
    async with client_for_call(resolved_settings, client) as opik:
        with _as_tool_error(
            f"{handler.run_verb} {entity_type}",
            on_timeout=handler.run_timeout_hint
            or f"Opik did not answer in time for list({entity_type!r}, …). Retry with a smaller "
            "page (size=…).",
        ):
            answer = await run(
                cast("OpikReadClient", opik),
                vocabularies=VOCABULARIES,
                settings=resolved_settings,
                **tool_args,
            )
        page_note = page_note_of(handler)
        if page_note is None:
            return with_list_size(entity_type, answer)
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
        return with_list_size(entity_type, f"{answer}\n\n{note}" if note else answer)


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
    """List tool entrypoint. See ``server/tools/list.py`` for the registered tool."""
    _PAGE_FACTS.set({})
    resolved_settings = settings or get_settings()
    features = enabled_features(resolved_settings)
    # Cleared per call for the same reason the page facts are: a project a
    # previous listing resolved is not a fact about this one, and a link
    # built from it would point into the wrong project — the exact failure
    # this whole feature exists to stop.
    remember_resolved_project(None)
    entity_type = resolve_entity_type(entity_type)
    handler = visible_handler(entity_type, features)
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
            settings=resolved_settings,
            client=client,
            page=page if page != 1 else None,
            size=size if size != DEFAULT_PAGE_SIZE else None,
            **tool_args,
        )

    # Checked after the runner branch rather than before it, so that reaching
    # the collection path is what proves there is a ``list_fn`` to drive.
    if handler is None or handler.list_fn is None:
        valid = ", ".join(sorted(listable_types(features)))
        err = EntityArgValidationError(f"Cannot list {entity_type!r}. Listable types: {valid}")
        raise ToolError(str(err)) from err

    resolved = resolve_list_args(
        handler,
        entity_type,
        name=name,
        filters=filters,
        sort=sort,
        since=since,
        until=until,
        search=search,
        page=page,
        size=size,
        project_id=project_id,
        project_name=project_name,
        dataset_id=dataset_id,
        prompt_id=prompt_id,
        status=status,
        features=features,
    )
    page, size, vocabulary = resolved.page, resolved.size, resolved.vocabulary
    list_kwargs, applied, clauses = resolved.list_kwargs, resolved.applied, resolved.clauses
    source_defaulted, sort_slot = resolved.is_source_defaulted, resolved.sort_slot
    sort_label, sort_field, to_time = resolved.sort_label, resolved.sort_field, resolved.to_time

    # A list can be several backend calls: resolving a project name, the
    # listing itself, and the did-you-mean / empty-result lookups, plus an
    # entity's own page note. The connection is owned for the span of this
    # call so they share it.
    #
    # Free-text search can take the backend >30 s on a cold cache (seen live:
    # 32 s); give only those calls a longer leash.
    search_timeout = _SEARCH_TIMEOUT_S if "search" in list_kwargs else None
    async with client_for_call(resolved_settings, client, timeout=search_timeout) as opik:
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
                # A misspelled project is the common 404 here. The error no
                # longer carries the backend's text, so the projects endpoint
                # is asked; only a name it does not know gets the did-you-mean,
                # and every other 404 falls through to the mapping above.
                if list_kwargs.get("project_name"):
                    await _refuse_unknown_project(opik, list_kwargs["project_name"], cause=e)
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
        # What the page knows about itself, for an entity whose registry entry
        # has something to add. ``is_windowed``: Diagnostics issues take a
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
            is_empty=not content,
            status=list_kwargs.get("status"),
            is_windowed="from_date" in list_kwargs or "to_date" in list_kwargs,
            window_end=parse_instant(to_time) if to_time else None,
            page=page,
            total=total,
            is_filtered=bool(filters and filters.strip()),
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
                without_default(vocabulary, opik, handler.list_fn, list_kwargs, clauses)
                if source_defaulted and total == 0
                else None
            )
            unfiltered = (
                without_filters(vocabulary, opik, handler.list_fn, list_kwargs, clauses)
                if page_ctx.is_filtered and total == 0
                else None
            )
            unnamed = (
                probe_count(
                    vocabulary,
                    opik,
                    handler.list_fn,
                    {k: v for k, v in list_kwargs.items() if k != "name"},
                    [],
                )
                if name and total == 0
                else None
            )
            empty = await empty_message(
                opik,
                handler,
                name=name,
                from_time=list_kwargs.get("from_time"),
                widened=widened,
                unfiltered=unfiltered,
                unnamed=unnamed,
                settings=resolved_settings,
                page_ctx=page_ctx,
                source_values=vocabulary.enum_values.get("source", ()),
            )
            return f"{list_size_header(entity_type, empty, applied)}\n{empty}"

        # A caller who named their columns did not ask for the sort and filter
        # fields to be appended to them; ``fields`` is the whole answer to
        # "which columns", so the request's own columns stand down.
        extra = requested_columns(sort_field, clauses) if wanted is None else []
        try:
            table = format_table(
                entity_type,
                handler,
                content,
                total,
                page,
                size,
                name,
                extra,
                pinned_columns(clauses) if wanted is None else frozenset(),
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
        return f"{list_size_header(entity_type, table, applied)}\n{table}"


async def _refuse_unknown_project(
    opik: OpikListClient, project_name: str, *, cause: OpikNotFoundError
) -> None:
    """Raise the did-you-mean refusal when no project carries this name."""
    try:
        await resolve_project_id(opik, project_name)
    except EntityArgValidationError as unknown:
        raise ToolError(str(unknown)) from cause
    except (
        OpikAuthError,
        OpikNotFoundError,
        OpikValidationError,
        OpikServerError,
        httpx.HTTPError,
    ):
        logger.debug("project lookup after a 404 failed", exc_info=True)


__all__ = ["run_list"]
