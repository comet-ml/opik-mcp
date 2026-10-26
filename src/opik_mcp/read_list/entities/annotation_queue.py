"""``annotation_queue`` — a project's review queue, and the custom view its annotators see.

A queue's ``annotator_view.html`` is up to 200,000 characters of HTML: most
reads of a queue ("which scores does it collect", "how many items are left")
need none of it, and a large one is over what a host takes in one answer. So
a read without ``fields`` states the html by size and names the call that
returns it whole.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from opik_mcp.client.protocols import AnnotationQueueClient, OpikListClient, OpikReadClient
from opik_mcp.config import Settings
from opik_mcp.read_list.handler import EntityHandler, Vocabulary
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.project_scope import scope_of
from opik_mcp.read_list.size import estimate_tokens
from opik_mcp.read_list.ui_links import project_page_url, scoped_entity_links
from opik_mcp.read_list.uri import opik_uri

VOCABULARY = Vocabulary(
    name="annotation_queue",
    filter_fields={
        "id": "string",
        "name": "string",
        "description": "string",
        "instructions": "string",
        "scope": "enum",
        "feedback_definition_names": "list",
        "created_at": "date_time",
        "created_by": "string",
        "last_updated_at": "date_time",
        "last_updated_by": "string",
    },
    enum_values={"scope": ("trace", "thread")},
    sort_fields=(
        "id",
        "name",
        "description",
        "instructions",
        "created_at",
        "last_updated_at",
        "created_by",
        "last_updated_by",
    ),
    filter_examples=('scope = "thread"', 'feedback_definition_names contains "helpfulness"'),
)


async def fetch(client: AnnotationQueueClient, entity_id: str) -> dict[str, object]:
    return await client.get_annotation_queue(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, object]]:
    queues = cast(AnnotationQueueClient, client)
    return list(name_candidates(await queues.list_annotation_queues(name=name, size=5)))


async def list_page(client: OpikListClient, **kw: object) -> dict[str, object]:
    project_id = await scope_of(client, kw, caller="list('annotation_queue')")
    page = kw.get("page")
    size = kw.get("size")
    name = kw.get("name")
    sorting = kw.get("sorting")
    return await cast(AnnotationQueueClient, client).list_annotation_queues(
        page=page if isinstance(page, int) else 1,
        size=size if isinstance(size, int) else 10,
        name=name if isinstance(name, str) else None,
        filters=_with_project(kw.get("filters"), project_id),
        sorting=sorting if isinstance(sorting, str) else None,
    )


def _with_project(filters: object, project_id: str) -> str:
    """The caller's compiled filters plus the project, which the endpoint takes
    only as a filter clause."""
    clauses = json.loads(filters) if isinstance(filters, str) and filters else []
    clauses.append({"field": "project_id", "operator": "=", "key": "", "value": project_id})
    return json.dumps(clauses, separators=(",", ":"))


def queue_links(settings: Settings, data: dict[str, object]) -> dict[str, str]:
    return scoped_entity_links(settings, data, area="annotation-queues", noun="annotation queue")


def row_link_template(settings: Settings, project_id: str | None) -> str | None:
    if not project_id:
        return None
    return project_page_url(settings, project_id, "annotation-queues", subpath="{id}")


def plain_read_view(record: dict[str, object]) -> dict[str, object]:
    view = record.get("annotator_view")
    html = view.get("html") if isinstance(view, Mapping) else None
    if not isinstance(view, Mapping) or not isinstance(html, str) or not html:
        return {
            **record,
            "annotatorViewHtml": (
                "none: this queue has no custom view, so annotators see the default one"
            ),
        }
    shown = {key: value for key, value in view.items() if key != "html"}
    shown["html_chars"] = len(html)
    return {
        **record,
        "annotator_view": shown,
        "annotatorViewHtml": (
            f"{len(html):,} characters (~{estimate_tokens(html):,} tok) not inlined; "
            f"read('annotation_queue', '{record.get('id')}', fields=['annotator_view.html']) "
            "returns it whole"
        ),
    }


HANDLER = EntityHandler(
    entity_type="annotation_queue",
    is_name_searchable=True,
    uri_patterns=(opik_uri("annotation-queues/{id}"),),
    link_fn=queue_links,
    row_link_template=row_link_template,
    read_view_fn=plain_read_view,
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("scope", "items_count", "feedback_definition_names", "last_updated_at"),
    list_required_kwargs=("project_id",),
    vocabularies=(VOCABULARY,),
    description=(
        "A project's annotation queue: the traces or threads people review and score. "
        "A read states its custom annotator view's html by size; "
        "fields=['annotator_view.html'] returns it whole."
    ),
)
