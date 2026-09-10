"""``thread`` — a conversation, assembled from the traces that make it up.

A thread has no body of its own on the backend: it is metadata plus the
traces carrying its ``thread_id``, so the read projects each trace to one
turn and sorts them into conversation order. Compression drops the turns'
payloads before the metadata, since a long conversation is long because of
what was said, and the metadata is what says which conversation this is.
"""

from __future__ import annotations

import json
from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.compression import (
    TOKEN_FULL_THRESHOLD,
    TOKEN_SKELETON_THRESHOLD,
    CompressionTier,
    compact_json,
    estimate_tokens,
    truncate_strings,
)
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import collection_truncated, page_items

MESSAGES_INLINE_LIMIT = 200


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
    thread = await client.get_thread(entity_id, project_id=project_id, project_name=project_name)
    filters = json.dumps([{"field": "thread_id", "operator": "=", "value": entity_id}])
    try:
        traces_page = await client.list_traces(
            project_id=project_id,
            project_name=project_name,
            filters=filters,
            page=1,
            size=MESSAGES_INLINE_LIMIT,
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
    return {
        "thread": thread,
        "messages": as_messages(traces),
        "messagesTruncated": truncated,
    }


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # Thread listing is project-scoped — the ``list`` tool enforces project_id
    # via ``list_required_kwargs``. ``name`` filtering on threads isn't
    # supported by opik-backend; drop it if passed.
    kw.pop("name", None)
    return await client.list_threads(**kw)


def compress(data: dict[str, Any], max_tokens: int | None) -> tuple[str, CompressionTier]:
    """Thread+messages: FULL → MEDIUM (truncated strings) → SKELETON (turn list only).

    Mirrors ``_compress_trace``: SKELETON drops the message payloads but keeps
    the turn list so the LLM can drill into a specific turn via
    ``read('trace', trace_id)``.
    """
    full_json = compact_json(data)
    full_tokens = estimate_tokens(full_json)

    budget = max_tokens if max_tokens is not None else TOKEN_FULL_THRESHOLD
    if full_tokens <= budget:
        return full_json, CompressionTier.FULL

    if full_tokens < TOKEN_SKELETON_THRESHOLD:
        truncated = truncate_strings(data, ".thread")
        return compact_json(truncated), CompressionTier.MEDIUM

    thread = data.get("thread") or {}
    messages = data.get("messages") or []
    skeleton = {
        "thread": {"id": thread.get("id"), "status": thread.get("status")},
        "messages": [
            {
                "trace_id": m.get("trace_id"),
                "name": m.get("name"),
                "start_time": m.get("start_time"),
                "feedback_scores": m.get("feedback_scores"),
            }
            for m in messages
            if isinstance(m, dict)
        ],
        "messagesTruncated": data.get("messagesTruncated", False),
        "note": (
            "SKELETON compression: message payloads omitted. "
            "Use read('trace', trace_id) for details."
        ),
    }
    return compact_json(skeleton), CompressionTier.SKELETON


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
    compress_fn=compress,
    id_only=True,
    needs_project=True,
    description=(
        "Conversation thread: metadata + messages list (each turn's trace "
        "input/output, up to 200 inlined). Returns {thread, messages, "
        "messagesTruncated}. Requires project scope — pass a thread link/URI "
        "or project_id. list('thread', project_id=…) enumerates a project's "
        "threads."
    ),
)
