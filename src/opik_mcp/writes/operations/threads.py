"""Threads: their lifecycle, and the id a thread comment has to be sent as.

A thread is addressed two different ways by the backend, and this module is
where that asymmetry is absorbed so no caller has to know about it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.writes.errors import BackendError
from opik_mcp.writes.wire import BuildContext, WireRequest, dump, refuse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


def build_thread_lifecycle(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Fixed endpoint, generic body.

    The model is exactly ``{thread_id, project_name?, project_id?}`` with no
    ``target`` field, so the exclude_none dump IS the wire body
    (``TraceThreadIdentifier``). ``supports_batch=False``, so ``items[0]`` is
    the only item.
    """
    return WireRequest(op.endpoint, dump(items[0]))


async def resolve_comment_thread_id(
    op: WriteOperation, items: list[BaseModel], client: OpikClient
) -> str | None:
    """Swap a thread-comment's ``thread_id`` string for the thread's model UUID.

    The BE's ``POST /threads/{id}/comments`` takes the thread *model* UUID as
    the path id, but the caller passes the same ``thread_id`` string used for
    scoring and reading (one uniform contract). Resolve it via ``get_thread``
    so the asymmetry never surfaces. A no-op for non-thread comments.
    """
    from opik_mcp.writes.models import CommentCreate

    model = items[0]
    if not isinstance(model, CommentCreate) or model.target != "thread":
        return None
    try:
        # truncate=True — we only need the model id, not the full messages.
        thread = await client.get_thread(
            model.target_id,
            project_id=str(model.project_id) if model.project_id else None,
            project_name=model.project_name,
            truncate=True,
        )
    except OpikNotFoundError as e:
        raise refuse(
            op,
            "target_id",
            f"thread {model.target_id!r} not found in the given project — "
            "check the thread_id and project_name/project_id.",
            "thread_not_found",
        ) from e
    except (OpikAuthError, OpikValidationError, OpikServerError) as e:
        # Mirror the live write path: a non-404 backend failure during the
        # resolve becomes a structured BackendError, not a raw OpikError that
        # would bypass the write tool's JSON-envelope contract.
        raise BackendError.build(
            op.name,
            e.http_status or 502,
            str(e),
            method="POST",
            path="/v1/private/traces/threads/retrieve",
        ) from e
    # ``id`` on a TraceThread is the string thread_id, NOT a UUID — the comment
    # path needs the model UUID, so there is no valid fallback to ``id`` here.
    model_id = thread.get("thread_model_id")
    if not isinstance(model_id, str) or not model_id:
        raise refuse(op, "target_id", "thread has no resolvable model id.", "thread_not_found")
    items[0] = model.model_copy(update={"target_id": model_id})
    return None


def comment_dry_run_note(
    op: WriteOperation, items: list[BaseModel], prepared: str | None
) -> str | None:
    """A dry run skips the live resolve, so a thread comment's previewed path
    still shows the thread_id string. Say so rather than imply the preview is
    exact."""
    if getattr(items[0], "target", None) != "thread":
        return None
    return (
        "thread comments resolve the thread_id string to the thread's model UUID at "
        "execution; the live path will use /threads/{model_uuid}/comments."
    )


__all__ = ["build_thread_lifecycle", "comment_dry_run_note", "resolve_comment_thread_id"]
