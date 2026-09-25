"""``trace`` — one request through an instrumented app, with its spans.

The read inlines the span tree, because a trace on its own says a call
happened and the spans say what it did.

The trace itself arrives whole — the payload that makes a trace large is
usually the payload the read was opened for. The spans are asked for slim:
two hundred of them is where a single base64 image turns one read into the
whole context window, and each carries the id that fetches it back in full.
See ``read_list/slim.py``.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler, Vocabulary
from opik_mcp.read_list.paging import (
    collection_total,
    collection_truncated,
    continuation,
    page_items,
    rest_of,
)
from opik_mcp.read_list.slim import count_cut, drop_bodies_past, dropped_notice, slim_notice
from opik_mcp.read_list.ui_links import logs_page_url, trace_link_template

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200

#: Character ceiling on the bodies of inlined spans. The count cap alone
#: left the read unbounded: the backend cuts each field at 10,001 chars.
SPANS_INLINE_CHARS = 14_000

#: The span fields ``truncate=true`` acts on, in the order the backend cuts
#: them. Used to count what actually lost bytes, not to cut anything here.
SLIM_SPAN_FIELDS = ("input", "output", "metadata")


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    """Trace + inlined spans (up to ``SPANS_INLINE_LIMIT``).

    The spans index in opik-backend is sharded by project, so the second
    call needs the trace's ``project_id``. A trace without project_id is
    anomalous — return an empty spans list rather than failing the read.

    ``spanBodies`` says how many spans lost bytes to the backend's cut. It is
    absent when no spans arrived — from an empty tree or a failed call — since
    a notice about a payload the caller never received is only noise they have
    to reason about.
    """
    trace = await client.get_trace(entity_id)
    project_id = trace.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return {"trace": trace, "spans": [], "spansTruncated": False}
    try:
        spans_page = await client.list_spans(
            trace_id=entity_id,
            project_id=project_id,
            page=1,
            size=SPANS_INLINE_LIMIT,
            truncate=True,
        )
    except Exception:
        return {"trace": trace, "spans": [], "spansTruncated": False}
    fetched = page_items(spans_page)
    # Counted before the budget spends anything: a dropped body is not an uncut one.
    cut = count_cut(fetched, SLIM_SPAN_FIELDS)
    spans, dropped = drop_bodies_past(fetched, SPANS_INLINE_CHARS, SLIM_SPAN_FIELDS)
    truncated = collection_truncated(spans_page, inlined=len(spans), limit=SPANS_INLINE_LIMIT)
    result: dict[str, Any] = {"trace": trace, "spans": spans, "spansTruncated": truncated}
    if truncated:
        # An agent loop can run to hundreds of spans; the flag alone left the
        # caller knowing the tree was short and not how to see the rest.
        result["moreSpans"] = rest_of(
            "spans",
            inlined=len(spans),
            total=collection_total(spans_page),
            call=(
                f"list('span', project_id='{project_id}', "
                f"filters='trace_id = \"{entity_id}\"', {continuation(SPANS_INLINE_LIMIT)})"
            ),
        )
    if spans:
        notice = slim_notice(
            cut=cut,
            total=len(spans),
            noun="span",
            whole="read('span', id)",
        )
        if dropped:
            spent = dropped_notice(
                dropped=dropped,
                total=len(spans),
                noun="span",
                budget=SPANS_INLINE_CHARS,
            )
            notice = f"{notice} {spent}"
        result["spanBodies"] = notice
    return result


def trace_page_url(
    settings: Settings,
    project_id: str,
    trace_id: str,
    *,
    span_id: str | None = None,
) -> str | None:
    """The Logs page with this trace open, or ``None`` when it cannot be built.

    The direct address, for when the project and the workspace are both known.
    :func:`trace_link_template` is the fallback for when they are not — it
    costs a hop and lands on ``/traces``, which v2 keeps only to forward here.

    ``span_id`` selects one span inside the opened trace. It is not an address
    of its own: the UI treats it as panel state under the trace, and writes an
    empty one into the query when a trace is opened without a span.
    """
    if not trace_id:
        return None
    if span_id:
        return logs_page_url(settings, project_id, "traces", trace=trace_id, span=span_id)
    return logs_page_url(settings, project_id, "traces", trace=trace_id)


def row_link_template(settings: Settings, project_id: str | None) -> str | None:
    if not project_id:
        return None
    return logs_page_url(settings, project_id, "traces", trace="{id}")


def trace_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The trace's UI link: the Logs page directly, or the redirect.

    The direct address is preferred because the redirect costs a hop and lands
    on ``/traces``, which v2 keeps only to forward to ``/logs``. It needs the
    project and the workspace, and the record carries the first — so the
    redirect stays as the fallback for the session that cannot name its
    workspace, which is an OAuth bearer introspection did not resolve. Losing
    the link there would be worse than the hop.
    """
    trace = data.get("trace")
    trace_id = trace.get("id") if isinstance(trace, dict) else None
    if not isinstance(trace_id, str) or not trace_id:
        return {}
    project_id = trace.get("project_id") if isinstance(trace, dict) else None
    if isinstance(project_id, str) and project_id:
        direct = trace_page_url(settings, project_id, trace_id)
        if direct is not None:
            return {"url": direct}
    template = trace_link_template(settings)
    if template is None:
        return {}
    return {"url": template.replace("{trace_id}", trace_id)}


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Trace listing is project-scoped — ``list`` tool enforces project_id
    # presence via ``list_required_kwargs``. ``name`` filtering on traces
    # isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_traces(**kw)


def derive_columns(record: dict[str, Any]) -> dict[str, Any]:
    """``experiment_id``, from the ``experiment`` reference the record carries.

    The filter field is ``experiment_id``; the trace record has ``experiment:
    {id, …}``. A column named after the field resolved to nothing, and an
    empty column under a filter on it reads as "these traces have no
    experiment". A filter that pins one id drops the column (the header
    states it); ``!=`` or ``in`` over several keeps it, and this fills it.
    """
    if "experiment_id" in record:
        return record
    experiment = record.get("experiment")
    if isinstance(experiment, dict) and experiment.get("id"):
        return {**record, "experiment_id": experiment["id"]}
    return record


VOCABULARY = Vocabulary(
    name="trace",
    filter_examples=(
        "error_info is_not_empty AND duration > 5000",
        'feedback_scores.accuracy < 0.5 AND start_time >= "2026-09-08T00:00:00Z"',
    ),
    sort_fields=(
        "id",
        "name",
        "input",
        "output",
        "start_time",
        "end_time",
        "duration",
        "ttft",
        "metadata",
        "thread_id",
        "span_count",
        "llm_span_count",
        "usage.*",
        "total_estimated_cost",
        "tags",
        "error_info",
        "created_by",
        "feedback_scores.*",
        "experiment_id",
        "environment",
    ),
)


HANDLER = EntityHandler(
    entity_type="trace",
    vocabularies=(VOCABULARY,),
    row_link_template=row_link_template,
    fetch_fn=fetch,
    link_fn=trace_links,
    list_fn=list_page,
    list_row_fn=derive_columns,
    # Triage columns: what a "which traces need attention" list needs
    # without a read() per row. error_type is derived from error_info.
    list_extra_fields=("start_time", "duration", "error_type", "total_estimated_cost"),
    list_required_kwargs=("project_id",),
    id_only=True,
    description=(
        "Single trace + child spans tree (up to 200 spans inlined, bodies "
        "slim). Returns {trace, spans, spansTruncated}, plus spanBodies "
        "saying what the cut took when any span was inlined, and moreSpans "
        "with the call for the rest when the tree is longer than 200."
    ),
)
