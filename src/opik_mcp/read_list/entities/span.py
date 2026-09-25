"""``span`` — one span by id, and a project's spans as a page.

A span read is the flat record. Nothing is inlined: a span's children are
spans of the same project, and the caller asked for one.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.entities import SOURCE_VALUES
from opik_mcp.read_list.handler import EntityHandler, Vocabulary
from opik_mcp.read_list.oql import PAYLOAD_FIELDS, TIMING_FIELDS
from opik_mcp.read_list.ui_links import logs_page_url
from opik_mcp.read_list.uri import opik_uri


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_span(entity_id)


def span_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The span, open inside its trace.

    A span has no page and no panel of its own: the Logs spans view lists
    them but opens nothing, and ``?span=`` is a selection *within* an already
    opened trace panel — the UI writes an empty one into the address when a
    trace is opened without a span. So the address is the trace's, with the
    span named; it opens the trace panel with this span selected in the tree.

    Both parts come from the record, and a span whose trace is unknown gets no
    link rather than one that lands on a list it is not on.
    """
    project_id = data.get("project_id")
    trace_id = data.get("trace_id")
    span_id = data.get("id")
    if not all(isinstance(v, str) and v for v in (project_id, trace_id, span_id)):
        return {}
    url = logs_page_url(settings, str(project_id), "traces", trace=str(trace_id), span=str(span_id))
    return {"url": url} if url is not None else {}


def row_link_template(settings: Settings, project_id: str | None) -> str | None:
    """The trace column a span row already prints fills the trace slot."""
    if not project_id:
        return None
    return logs_page_url(settings, project_id, "traces", trace="{trace_id}", span="{id}")


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Project-wide span search: no ``trace_id`` — that scoping (and ``type``)
    # is expressed in OQL (``trace_id = "…"``, ``type = "llm"``). ``name``
    # filtering isn't supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_spans(**kw)


VOCABULARY = Vocabulary(
    name="span",
    filter_fields={
        "id": "string",
        "name": "string",
        "type": "enum",
        "trace_id": "string",
        **TIMING_FIELDS,
        **PAYLOAD_FIELDS,
        "model": "string",
        "provider": "string",
    },
    enum_values={
        "source": SOURCE_VALUES,
        "type": ("general", "tool", "llm", "guardrail", "unknown"),
    },
    is_source_defaulted=True,
    filter_examples=(
        'type = "llm" AND usage.total_tokens > 10000',
        'name = "search_docs" AND error_info is_not_empty',
    ),
    sort_fields=(
        "id",
        "name",
        "type",
        "trace_id",
        "parent_span_id",
        "input",
        "output",
        "metadata",
        "start_time",
        "end_time",
        "duration",
        "ttft",
        "usage.*",
        "tags",
        "created_at",
        "last_updated_at",
        "model",
        "provider",
        "total_estimated_cost",
        "error_info",
        "created_by",
        "feedback_scores.*",
        "environment",
    ),
)


HANDLER = EntityHandler(
    entity_type="span",
    is_windowed=True,
    uri_patterns=(opik_uri("spans/{id}"),),
    vocabularies=(VOCABULARY,),
    row_link_template=row_link_template,
    fetch_fn=fetch,
    link_fn=span_links,
    list_fn=list_page,
    list_extra_fields=("type", "trace_id", "duration", "model", "error_type"),
    list_required_kwargs=("project_id",),
    id_only=True,
    description=(
        "Single span: inputs, outputs, metadata, timing, feedback_scores. "
        "list('span', project_id=…, filters=…) searches spans across a project."
    ),
)
