"""Annotation queues: create one, change one, and set or clear its custom annotator view.

Every released Opik before custom views ignores ``annotator_view`` and answers
201 or 204 anyway, so a write that carried a view reads the queue back and says
when the view did not stick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import httpx
from pydantic import BaseModel

from opik_mcp.client.errors import OpikNotFoundError
from opik_mcp.client.opik import OpikClient
from opik_mcp.config import Settings
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.project_scope import resolve_project_id
from opik_mcp.read_list.ui_links import project_page_url
from opik_mcp.writes.models import example_uuid
from opik_mcp.writes.operations.observability import AnnotationQueueUpdate
from opik_mcp.writes.wire import BuildContext, WireRequest, dump, refuse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation

_VIEW_DROPPED: Final = (
    "The queue was saved, but this Opik backend did not store the annotator view: "
    "custom views need an Opik version that supports them."
)
_VIEW_UNCONFIRMED: Final = (
    "The queue was saved; reading it back to confirm the annotator view failed. "
    "read('annotation_queue', id) shows whether the view is there."
)

_EXAMPLE_VIEW: Final = (
    "<script>kit.mount(({ trace }) => kit.card({ title: 'Reply', highlight: true, "
    "body: kit.h('div', { class: 'kit-text', text: kit.asText(trace.output) }) }));</script>"
)

ANNOTATION_QUEUE_CREATE_EXAMPLE: Final[dict[str, object]] = {
    "project_name": "support-bot",
    "name": "Reply review",
    "scope": "trace",
    "instructions": "Score whether the reply answers the customer's question.",
    "feedback_definition_names": ["helpfulness"],
    "annotator_view": {"html": _EXAMPLE_VIEW, "summary": "Annotators see the reply as a card."},
}

ANNOTATION_QUEUE_UPDATE_EXAMPLE: Final[dict[str, object]] = {
    "id": example_uuid("a001"),
    "instructions": "Score whether the reply answers the question and stays polite.",
    "clear_annotator_view": True,
}


async def prepare(op: WriteOperation, items: list[BaseModel], client: OpikClient) -> str | None:
    """The queue's project id, which the link needs: resolved from the caller's
    project on a create, read from the queue itself on an update."""
    item = items[0]
    if isinstance(item, AnnotationQueueUpdate):
        return await _project_of_queue(op, client, str(item.id))
    project_id = getattr(item, "project_id", None)
    if project_id is not None:
        return str(project_id)
    try:
        return await resolve_project_id(client, str(getattr(item, "project_name", None)))
    except EntityArgValidationError as e:
        raise refuse(op, "project_name", str(e), "project_scope_missing") from e


async def _project_of_queue(op: WriteOperation, client: OpikClient, queue_id: str) -> str | None:
    """A missing queue is refused before the write; any other failure only
    costs the link, never the write."""
    try:
        queue = await client.get_annotation_queue(queue_id)
    except OpikNotFoundError as e:
        raise refuse(
            op,
            "id",
            f"No annotation queue {queue_id} in this workspace; "
            "list('annotation_queue', project_name=…) shows a project's queues.",
            "queue_not_found",
        ) from e
    except Exception:
        return None
    project_id = queue.get("project_id")
    return project_id if isinstance(project_id, str) and project_id else None


def build(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    item = items[0]
    body = dump(item)
    if isinstance(item, AnnotationQueueUpdate):
        queue_id = body.pop("id")
        if body.pop("clear_annotator_view", False):
            body["annotator_view"] = {}
        return WireRequest(f"{op.endpoint}/{queue_id}", body)
    body.pop("project_name", None)
    body["project_id"] = ctx.prepared or body.get("project_id") or "{project_id}"
    return WireRequest(op.endpoint, body)


def decorate(
    _op: WriteOperation,
    items: list[BaseModel],
    out: dict[str, object],
    settings: Settings,
    project_id: str | None,
) -> None:
    """The queue's page, or the project's queues when the backend chose the id."""
    if project_id is None:
        return
    queue_id = getattr(items[0], "id", None)
    url = project_page_url(
        settings,
        project_id,
        "annotation-queues",
        subpath=str(queue_id) if queue_id is not None else None,
    )
    if url is not None:
        out["url"] = url


async def confirm_view(
    _op: WriteOperation,
    client: OpikClient,
    items: list[BaseModel],
    resp: httpx.Response,
    out: dict[str, object],
) -> None:
    item = items[0]
    view = getattr(item, "annotator_view", None)
    if view is None or not getattr(view, "html", None):
        return
    queue_id = getattr(item, "id", None) or _created_id(resp)
    if queue_id is None:
        out["warning"] = _VIEW_UNCONFIRMED
        return
    try:
        queue = await client.get_annotation_queue(str(queue_id))
    except Exception:
        out["warning"] = _VIEW_UNCONFIRMED
        return
    stored = queue.get("annotator_view")
    if not isinstance(stored, dict) or not stored.get("html"):
        out["warning"] = _VIEW_DROPPED


def _created_id(resp: httpx.Response) -> str | None:
    location = resp.headers.get("location", "")
    return location.rstrip("/").rsplit("/", 1)[-1] or None


def dry_run_note(_op: WriteOperation, items: list[BaseModel], _prepared: str | None) -> str | None:
    item = items[0]
    if isinstance(item, AnnotationQueueUpdate):
        return None
    if getattr(item, "project_id", None) is not None:
        return None
    return (
        "project_name is resolved to the project's UUID at execution; the live body "
        "will carry that id."
    )


__all__ = [
    "ANNOTATION_QUEUE_CREATE_EXAMPLE",
    "ANNOTATION_QUEUE_UPDATE_EXAMPLE",
    "build",
    "confirm_view",
    "decorate",
    "dry_run_note",
    "prepare",
]
