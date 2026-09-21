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
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import (
    collection_total,
    collection_truncated,
    continuation,
    page_items,
    rest_of,
)
from opik_mcp.read_list.slim import count_cut, drop_bodies_past, dropped_notice, slim_notice
from opik_mcp.read_list.ui_links import trace_link_template

# Inline caps for composite reads — match the previous resources.py
# constants so cache shapes stay stable for any in-flight integration.
SPANS_INLINE_LIMIT = 200

#: And what those spans may cost, in characters of serialised JSON. The count
#: cap alone left the read unbounded in size: the backend cuts each field at
#: 10,001 characters, so two hundred spans of three cut fields is six million.
#: Measured live, five spans came to 38,423 characters — 8,421 tokens by the
#: header's own estimate — and the client refused the whole answer, so the
#: caller saw an error where the trace should have been.
#:
#: 14,000 keeps an ordinary trace read near 7,000 tokens, inside the tightest
#: host budget seen in use and far inside the common default. The first number
#: tried was 25,000, chosen against the header's own estimate of four
#: characters per token — which is prose's ratio, not JSON's, so the budget
#: was calibrated at nearly twice the size it meant to allow and the read it
#: was written for was still refused. See ``size._CHARS_PER_TOKEN``.
#:
#: A constant rather than a setting because a fetcher sees a client, not
#: ``Settings`` — and because the number that matters is the host's, which
#: this process cannot read either way.
#:
#: One span can still exceed this on its own: the backend cuts each field at
#: 10,001 characters and a span has three, so a single maximally-cut span is
#: the floor this cannot go under without answering a trace with no bodies at
#: all. ``fields=[…]`` is the caller's remedy there, and the only one that
#: can be, since what to keep is their question and not ours.
#:
#: What the budget spends is bodies, not spans: see
#: :func:`opik_mcp.read_list.slim.drop_bodies_past` for why the tree survives
#: a cut that its payloads do not.
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
    # What the backend cut is a fact about the fetch, so it is counted before
    # the inline budget spends anything: a body this read drops is one the
    # caller never sees the length of, and counting it as uncut would report
    # fewer cut spans the larger the trace got.
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


def trace_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The trace's own page in the UI.

    Built from the template rather than a project path because a trace read is
    reached from places that never carried a project id — ``worst_trace`` on a
    comparison is the one this exists for. The backend's redirect resolves both
    the project and the workspace from the id, which is also why this is the
    one link that survives an OAuth session introspection could not name.
    """
    trace = data.get("trace")
    trace_id = trace.get("id") if isinstance(trace, dict) else None
    if not isinstance(trace_id, str) or not trace_id:
        return {}
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


HANDLER = EntityHandler(
    entity_type="trace",
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
