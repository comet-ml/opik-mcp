"""Traces, threads and spans."""

from __future__ import annotations

from typing import cast

from opik_mcp.client.base import OpikClientBase, QueryParams, _search_params
from opik_mcp.client.shapes import Page, Span, Trace, TraceThread


class ObservabilityEndpoints(OpikClientBase):
    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> Page[Trace]:
        """``GET /v1/private/traces`` — requires ``project_id`` or ``project_name``.

        ``filters`` is the backend's JSON-encoded filter array (query param),
        e.g. ``[{"field":"thread_id","operator":"=","value":"<id>"}]``; the
        ``list`` tool compiles OQL into it and the thread read uses it to pull a
        thread's messages. ``sorting`` is the JSON-encoded ``[{field,direction}]``
        array, ``search`` a free-text term, ``from_time``/``to_time`` ISO-8601
        instants. Each is forwarded only when set.
        """
        if project_id is None and project_name is None:
            raise ValueError("list_traces requires project_id or project_name")
        params: QueryParams = {"page": page, "size": size}
        if project_id is not None:
            params["project_id"] = project_id
        if project_name is not None:
            params["project_name"] = project_name
        params.update(
            _search_params(
                filters=filters,
                sorting=sorting,
                search=search,
                from_time=from_time,
                to_time=to_time,
                should_truncate=should_truncate,
            )
        )
        return cast(
            Page[Trace],
            await self._get_json("/v1/private/traces", params=params, entity_hint="traces"),
        )

    async def get_trace(self, trace_id: str) -> Trace:
        """``GET /v1/private/traces/{id}`` — trace metadata only (spans fetched separately)."""
        return cast(
            Trace,
            await self._get_json(
                f"/v1/private/traces/{trace_id}",
                params=None,
                entity_hint=f"trace {trace_id!r}",
            ),
        )

    async def list_threads(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> Page[TraceThread]:
        """``GET /v1/private/traces/threads`` — project-scoped page of threads.

        A thread groups traces by ``thread_id`` within one project, so listing
        requires ``project_id`` or ``project_name`` (like ``list_traces``).
        Returns a Spring Page envelope ``{content, page, size, total}``. Search
        params are the same as ``list_traces`` and forwarded only when set.
        """
        if project_id is None and project_name is None:
            raise ValueError("list_threads requires project_id or project_name")
        params: QueryParams = {"page": page, "size": size}
        if project_id is not None:
            params["project_id"] = project_id
        if project_name is not None:
            params["project_name"] = project_name
        params.update(
            _search_params(
                filters=filters,
                sorting=sorting,
                search=search,
                from_time=from_time,
                to_time=to_time,
                should_truncate=should_truncate,
            )
        )
        return cast(
            Page[TraceThread],
            await self._get_json(
                "/v1/private/traces/threads",
                params=params,
                entity_hint="threads",
            ),
        )

    async def get_thread(
        self,
        thread_id: str,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        should_truncate: bool = False,
    ) -> TraceThread:
        """``POST /v1/private/traces/threads/retrieve`` — one thread's metadata.

        A thread is keyed by ``thread_id`` within a single project, so the
        backend has no ``GET /{id}`` route — it takes a ``TraceThreadIdentifier``
        body and requires ``project_id`` or ``project_name`` (raise ``ValueError``
        if neither is given, mirroring ``list_traces``). ``should_truncate`` cuts
        ``first_message``/``last_message``, which are ``argMin``/``argMax`` over
        the thread's trace bodies — the same bytes the turns carry, so the read
        asks for them slim rather than shipping one payload at two lengths.
        """
        if project_id is None and project_name is None:
            raise ValueError("get_thread requires project_id or project_name")
        body: dict[str, str | bool] = {"thread_id": thread_id, "truncate": should_truncate}
        if project_id is not None:
            body["project_id"] = project_id
        if project_name is not None:
            body["project_name"] = project_name
        return cast(
            TraceThread,
            await self._post_json(
                "/v1/private/traces/threads/retrieve",
                json=body,
                entity_hint=f"thread {thread_id!r}",
            ),
        )

    async def list_spans(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 100,
    ) -> Page[Span]:
        """``GET /v1/private/spans`` — spans of one trace, or across a project.

        opik-backend rejects ``GET /v1/private/spans`` with 400 if neither
        ``project_id`` nor ``project_name`` is supplied (the spans index is
        sharded by project). ``trace_id`` is optional: the trace read passes it
        to inline one trace's spans, the ``list`` tool omits it to search spans
        project-wide. Search params are the same as ``list_traces``.
        """
        if project_id is None and project_name is None:
            raise ValueError("list_spans requires project_id or project_name")
        params: QueryParams = {"page": page, "size": size}
        if trace_id is not None:
            params["trace_id"] = trace_id
        if project_id is not None:
            params["project_id"] = project_id
        if project_name is not None:
            params["project_name"] = project_name
        params.update(
            _search_params(
                filters=filters,
                sorting=sorting,
                search=search,
                from_time=from_time,
                to_time=to_time,
                should_truncate=should_truncate,
            )
        )
        hint = f"spans for trace {trace_id!r}" if trace_id is not None else "spans"
        return cast(
            Page[Span], await self._get_json("/v1/private/spans", params=params, entity_hint=hint)
        )

    async def get_span(self, span_id: str) -> Span:
        """``GET /v1/private/spans/{id}`` — single span."""
        return cast(
            Span,
            await self._get_json(
                f"/v1/private/spans/{span_id}",
                params=None,
                entity_hint=f"span {span_id!r}",
            ),
        )
