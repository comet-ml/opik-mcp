"""Orchestrators for the ``score`` and ``comment`` MCP tools.

These functions are the seam between the MCP tool registration (server.py)
and the REST adapter (opik_client.py). They:

1. Resolve config (api_key, workspace, opik base url) from Settings.
2. Validate the target discriminator and build a typed FeedbackScore.
3. Dispatch to the correct OpikClient method based on ``target.type``.
4. Return a small result envelope so MCP's structured-content path has
   something to echo back.

See design.md §1.5 — "target shape (used by score, comment, save_eval_item)".
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    FeedbackScore,
    make_opik_client,
    resolve_opik_config,
)

logger = logging.getLogger("opik_mcp.score_comment")


TargetType = Literal["trace", "span", "thread"]


class Target(BaseModel):
    """Discriminator for entities the MCP server can annotate.

    ``id`` is the entity's UUID — for ``thread`` targets this is the *thread
    UUID* (the ``id`` on a thread row), not the user-facing ``thread_id`` string
    that may appear on individual traces. Callers typically obtain it via
    ``read("thread", <id>)`` or by listing threads under a project.
    """

    type: TargetType = Field(description="Which entity kind to annotate.")
    id: str = Field(description="Entity UUID.", min_length=1)


class ScoreResult(BaseModel):
    target: Target
    name: str
    value: float


class CommentResult(BaseModel):
    target: Target


class _OpikClientProto(Protocol):
    async def add_trace_feedback_score(self, trace_id: str, score: FeedbackScore) -> None: ...

    async def add_span_feedback_score(self, span_id: str, score: FeedbackScore) -> None: ...

    async def add_thread_feedback_score(
        self,
        thread_id: str,
        score: FeedbackScore,
        *,
        project_name: str | None = None,
    ) -> None: ...

    async def add_trace_comment(self, trace_id: str, text: str) -> None: ...

    async def add_span_comment(self, span_id: str, text: str) -> None: ...

    async def add_thread_comment(self, thread_id: str, text: str) -> None: ...


# Compat shim — ``tests/test_score_comment_live.py`` imports ``_require_opik_config``
# to construct a hand-rolled client for the bad-api-key test. New code should call
# ``opik_client.resolve_opik_config`` / ``make_opik_client`` directly.
_require_opik_config = resolve_opik_config
_make_client = make_opik_client


async def run_score(
    *,
    target: Target,
    name: str,
    value: float,
    reason: str | None = None,
    category_name: str | None = None,
    project_name: str | None = None,
    settings: Settings | None = None,
    client: _OpikClientProto | None = None,
) -> ScoreResult:
    """Attach a feedback score to a trace, span, or thread.

    ``project_name`` is only consulted when ``target.type == "thread"`` —
    Opik's thread feedback endpoint accepts it to disambiguate threads that
    exist in multiple projects. For traces and spans, the entity id alone
    identifies it server-side and the param is ignored. We expose name only
    (no UUID variant) to match the Opik Python/TS SDKs.
    """
    settings = settings or get_settings()
    opik = client if client is not None else _make_client(settings)
    score = FeedbackScore(
        name=name,
        value=value,
        reason=reason,
        category_name=category_name,
    )

    if target.type == "trace":
        await opik.add_trace_feedback_score(target.id, score)
    elif target.type == "span":
        await opik.add_span_feedback_score(target.id, score)
    else:  # "thread"
        await opik.add_thread_feedback_score(target.id, score, project_name=project_name)

    logger.info(
        "score.added target_type=%s target_id=%s name=%s",
        target.type,
        target.id,
        name,
    )
    return ScoreResult(target=target, name=name, value=value)


async def run_comment(
    *,
    target: Target,
    text: str,
    settings: Settings | None = None,
    client: _OpikClientProto | None = None,
) -> CommentResult:
    """Post a free-text comment on a trace, span, or thread."""
    settings = settings or get_settings()
    opik = client if client is not None else _make_client(settings)

    if target.type == "trace":
        await opik.add_trace_comment(target.id, text)
    elif target.type == "span":
        await opik.add_span_comment(target.id, text)
    else:  # "thread"
        await opik.add_thread_comment(target.id, text)

    logger.info(
        "comment.added target_type=%s target_id=%s len=%d",
        target.type,
        target.id,
        len(text),
    )
    return CommentResult(target=target)
