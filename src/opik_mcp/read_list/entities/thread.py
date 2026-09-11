"""``thread`` — a conversation, assembled from the traces that make it up.

A thread has no body of its own on the backend: it is metadata plus the
traces carrying its ``thread_id``, so the read projects each trace to one
turn and sorts them into conversation order.

Both halves are asked for slim, and this entity is the reason the rule is
"slim the children" rather than "slim everything but the parent". A thread
record has no body of its own: ``first_message`` is ``argMin(t.input,
t.start_time)``, the very bytes ``messages[0].input`` already carries. Leaving
the record whole while cutting the turns would ship one payload twice, once
short and once long, with nothing saying which is authoritative — worse than
either choice made consistently. ``read('trace', trace_id)`` returns any turn
in full. See ``read_list/slim.py``.
"""

from __future__ import annotations

import json
from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import (
    collection_total,
    collection_truncated,
    page_items,
    rest_of,
)
from opik_mcp.read_list.slim import count_cut, slim_notice

MESSAGES_INLINE_LIMIT = 200

#: ``as_messages`` projects a trace down to input and output, so those are
#: the only cut fields a caller can see on a turn.
SLIM_TURN_FIELDS = ("input", "output")


def as_messages(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project each trace to one conversation turn, sorted by ``start_time`` asc.

    Ascending order is conversation order. Each turn keeps a ``trace_id`` so the
    agent can ``read('trace', id)`` to drill into spans/metadata.
    """
    ordered = sorted(traces, key=lambda t: t.get("start_time") or "")
    messages: list[dict[str, Any]] = []
    for t in ordered:
        msg: dict[str, Any] = {
            "trace_id": t.get("id"),
            "name": t.get("name"),
            "input": t.get("input"),
            "output": t.get("output"),
            "start_time": t.get("start_time"),
            "end_time": t.get("end_time"),
            "duration": t.get("duration"),
            "usage": t.get("usage"),
            "total_estimated_cost": t.get("total_estimated_cost"),
            "feedback_scores": t.get("feedback_scores"),
        }
        error_info = t.get("error_info")
        if error_info is not None:
            msg["error_info"] = error_info
        messages.append(msg)
    return messages


async def fetch(
    client: OpikReadClient,
    entity_id: str,
    *,
    project_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Thread metadata + its messages (traces filtered by ``thread_id``).

    Two project-scoped calls reproduce the UI's Thread panel: ``get_thread``
    for metadata, then ``list_traces`` filtered by ``thread_id`` for the turns.
    Mirrors ``_fetch_trace``'s defensive inline: if the messages call fails,
    return the metadata with an empty messages list rather than failing the
    whole read.
    """
    thread = await client.get_thread(
        entity_id, project_id=project_id, project_name=project_name, truncate=True
    )
    filters = json.dumps([{"field": "thread_id", "operator": "=", "value": entity_id}])
    try:
        traces_page = await client.list_traces(
            project_id=project_id,
            project_name=project_name,
            filters=filters,
            page=1,
            size=MESSAGES_INLINE_LIMIT,
            truncate=True,
        )
    except Exception:
        # Unlike a trace's spans (secondary), messages ARE a thread's primary
        # payload — so an empty list here must NOT read as "no messages" when
        # the metadata says otherwise. Signal the load failure explicitly.
        return {
            "thread": thread,
            "messages": [],
            "messagesTruncated": False,
            "messagesError": "Failed to load thread messages; retry or read the traces directly.",
        }
    traces = page_items(traces_page)
    truncated = collection_truncated(traces_page, inlined=len(traces), limit=MESSAGES_INLINE_LIMIT)
    messages = as_messages(traces)
    result: dict[str, Any] = {
        "thread": thread,
        "messages": messages,
        "messagesTruncated": truncated,
    }
    if truncated:
        scope = f"project_id='{project_id}'" if project_id else f"project_name='{project_name}'"
        result["moreMessages"] = rest_of(
            "turns",
            inlined=len(messages),
            total=collection_total(traces_page),
            call=(
                f"list('trace', {scope}, filters='thread_id = \"{entity_id}\"', "
                f"page={MESSAGES_INLINE_LIMIT // 100 + 1}, size=100)"
            ),
        )
    if messages:
        result["messageBodies"] = slim_notice(
            cut=count_cut(messages, SLIM_TURN_FIELDS),
            total=len(messages),
            noun="turn",
            whole="read('trace', trace_id)",
        )
    return result


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Thread listing is project-scoped — the ``list`` tool enforces project_id
    # via ``list_required_kwargs``. ``name`` filtering on threads isn't
    # supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_threads(**kw)


HANDLER = EntityHandler(
    entity_type="thread",
    fetch_fn=fetch,
    list_fn=list_page,
    # first_message stands in for the name a thread doesn't have: the
    # agent can pick the conversation without a read() per row.
    list_extra_fields=(
        "first_message",
        "status",
        "number_of_messages",
        "duration",
        "last_updated_at",
    ),
    list_required_kwargs=("project_id",),
    list_has_name=False,
    id_only=True,
    needs_project=True,
    description=(
        "Conversation thread: metadata + messages list (each turn's trace "
        "input/output, up to 200 inlined, bodies slim). Returns {thread, "
        "messages, messagesTruncated}, plus messageBodies saying what the cut "
        "took when any turn was inlined, and moreMessages with the call for "
        "the rest past 200. Requires project scope — pass a "
        "thread link/URI or project_id. list('thread', project_id=…) "
        "enumerates a project's threads."
    ),
)
