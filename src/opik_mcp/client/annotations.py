"""Feedback scores and comments on traces, spans and threads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from opik_mcp.client.base import OpikClientBase, _drop_none

FeedbackSource = Literal["sdk", "ui", "online_scoring"]
"""Mirrors ``com.comet.opik.api.ScoreSource``. The MCP server reports as ``sdk``."""


@dataclass(frozen=True)
class FeedbackScore:
    """Internal write shape mirroring opik-backend's ``FeedbackScore`` DTO.

    Not user-facing — the MCP tool layer builds this from its own params.
    """

    name: str
    value: float
    source: FeedbackSource = "sdk"
    category_name: str | None = None
    reason: str | None = None


class AnnotationEndpoints(OpikClientBase):
    async def add_trace_feedback_score(self, trace_id: str, score: FeedbackScore) -> None:
        """``PUT /v1/private/traces/{id}/feedback-scores`` — single score on a trace."""
        await self._request(
            "PUT",
            f"/v1/private/traces/{trace_id}/feedback-scores",
            json=_score_body(score),
            expected_status=204,
            entity_hint=f"trace {trace_id!r}",
        )

    async def add_span_feedback_score(self, span_id: str, score: FeedbackScore) -> None:
        """``PUT /v1/private/spans/{id}/feedback-scores`` — single score on a span."""
        await self._request(
            "PUT",
            f"/v1/private/spans/{span_id}/feedback-scores",
            json=_score_body(score),
            expected_status=204,
            entity_hint=f"span {span_id!r}",
        )

    async def add_thread_feedback_score(
        self,
        thread_id: str,
        score: FeedbackScore,
        *,
        project_name: str | None = None,
    ) -> None:
        """``PUT /v1/private/traces/threads/feedback-scores`` — batch-only endpoint.

        opik-backend exposes no single-item write for threads, so we send a
        ``scores: [...]`` envelope with one entry. ``project_name`` is optional
        (defaults to the workspace's default project server-side).
        """
        item: dict[str, Any] = {"thread_id": thread_id} | _score_body(score)
        if project_name is not None:
            item["project_name"] = project_name
        await self._request(
            "PUT",
            "/v1/private/traces/threads/feedback-scores",
            json={"scores": [item]},
            expected_status=204,
            entity_hint=f"thread {thread_id!r}",
        )

    # -- comments --

    async def add_trace_comment(self, trace_id: str, *, text: str) -> None:
        """``POST /v1/private/traces/{id}/comments``. Returns 201 with no body."""
        await self._request(
            "POST",
            f"/v1/private/traces/{trace_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"trace {trace_id!r}",
        )

    async def add_span_comment(self, span_id: str, *, text: str) -> None:
        """``POST /v1/private/spans/{id}/comments``. Returns 201 with no body."""
        await self._request(
            "POST",
            f"/v1/private/spans/{span_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"span {span_id!r}",
        )

    async def add_thread_comment(self, thread_id: str, *, text: str) -> None:
        """``POST /v1/private/traces/threads/{id}/comments``. ``{id}`` is the thread UUID."""
        await self._request(
            "POST",
            f"/v1/private/traces/threads/{thread_id}/comments",
            json={"text": text},
            expected_status=201,
            entity_hint=f"thread {thread_id!r}",
        )


def _score_body(score: FeedbackScore) -> dict[str, Any]:
    """FeedbackScore → JSON body with ``None`` fields stripped."""
    return _drop_none(asdict(score))
