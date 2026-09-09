"""Traces, spans, and the annotations that hang off them.

Four operations that share one shape of problem: the backend's batch route
takes an envelope keyed by the collection name, and the singleton route
encodes an id in the path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from opik_mcp.writes.errors import ValidationFailedError, ValidationIssue
from opik_mcp.writes.wire import TARGET_PATH, BuildContext, WireRequest, dump

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


def build_trace_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    if ctx.is_batch:
        return WireRequest(op.batch_endpoint or op.endpoint, {"traces": [dump(m) for m in items]})
    return WireRequest(op.endpoint, dump(items[0]))


def build_trace_update(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """PATCH one trace by id, or POST the batch upsert.

    ``/v1/private/traces/batch`` is a POST-only upsert that handles create and
    update by matching on ``id``; sending PATCH there returns 405. The method
    override lives here rather than as a rule in the dispatcher.
    """
    if ctx.is_batch:
        return WireRequest(
            op.batch_endpoint or op.endpoint,
            {"traces": [dump(m) for m in items]},
            method="POST",
        )
    single = dump(items[0])
    trace_id = single.pop("id")
    return WireRequest(op.endpoint.format(id=trace_id), single)


def build_span_create(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    if ctx.is_batch:
        return WireRequest(op.batch_endpoint or op.endpoint, {"spans": [dump(m) for m in items]})
    return WireRequest(op.endpoint, dump(items[0]))


def _score_batch_item(item: dict[str, Any], target: str) -> dict[str, Any]:
    """Reshape a per-target batch item into the BE's expected per-target body.

    The trace/span batch endpoints take ``id`` (the trace/span id); the thread
    batch endpoint takes ``thread_id``. The discriminator ``target`` is dropped
    from the wire body — it is encoded in the path segment instead.
    """
    target_id = item.pop("target_id")
    item.pop("target", None)
    if target == "thread":
        item["thread_id"] = target_id
    else:
        item["id"] = target_id
        item.pop("project_name", None)
        item.pop("project_id", None)
    return item


def build_score_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    from opik_mcp.writes.models import ScoreCreate

    # All items share a target (enforced in Stage 2) so we read from item 0.
    first = items[0]
    assert isinstance(first, ScoreCreate)
    target = first.target
    target_path = TARGET_PATH[target]
    if ctx.is_batch:
        scores = [_score_batch_item(dump(m), target) for m in items]
        return WireRequest(f"/v1/private/{target_path}/feedback-scores", {"scores": scores})
    single = dump(first)
    target_id = single.pop("target_id")
    single.pop("target", None)
    # ``project_name``/``project_id`` are only consulted by the thread route;
    # the single-trace/span routes ignore them, so strip them to keep the body
    # minimal. (Thread scores always take the batch path, so this branch is
    # trace/span only in practice.)
    if target != "thread":
        single.pop("project_name", None)
        single.pop("project_id", None)
    return WireRequest(f"/v1/private/{target_path}/{target_id}/feedback-scores", single)


def build_comment_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    single = dump(items[0])
    target = single.pop("target")
    target_id = single.pop("target_id")
    return WireRequest(
        f"/v1/private/{TARGET_PATH[target]}/{target_id}/comments", {"text": single["text"]}
    )


def validate_scores(
    op: WriteOperation,
    items: list[BaseModel],
    *,
    is_batch: bool,
    schema: dict[str, Any],
    example: dict[str, Any],
) -> None:
    """The two rules the score model cannot express on its own.

    A batch must be homogeneous by ``target``, because separate backend routes
    per target make a mixed batch structurally impossible to honour in one
    HTTP call. And ``target='thread'`` has no singleton route at all, so the
    single-object form fails early and points at the array form; the example
    is rebuilt from the caller's own validated fields rather than the registry
    placeholder, so one extra turn truly recovers.
    """
    from opik_mcp.writes.models import ScoreCreate

    if is_batch:
        targets = {getattr(it, "target", None) for it in items}
        if len(targets) > 1:
            raise ValidationFailedError.build(
                op.name,
                [
                    ValidationIssue(
                        "target",
                        f"score.create batches must use a single target; got "
                        f"{sorted(str(t) for t in targets)}. "
                        "Split into one batch per target.",
                        "heterogeneous_targets",
                    )
                ],
                expected_schema=schema,
                example=[example],
            )
        return

    model = items[0]
    if not isinstance(model, ScoreCreate) or model.target != "thread":
        return
    thread_example: dict[str, Any] = {
        "target": "thread",
        "target_id": model.target_id,
        "name": model.name,
        "value": model.value,
    }
    if model.project_name is not None:
        thread_example["project_name"] = model.project_name
    if model.project_id is not None:
        thread_example["project_id"] = str(model.project_id)
    if model.reason is not None:
        thread_example["reason"] = model.reason
    if model.category_name is not None:
        thread_example["category_name"] = model.category_name
    raise ValidationFailedError.build(
        op.name,
        [
            ValidationIssue(
                "target",
                "target='thread' requires the array form — wrap this object in a list.",
                "thread_requires_batch",
            )
        ],
        expected_schema=schema,
        example=[thread_example],
    )


__all__ = [
    "build_comment_create",
    "build_score_create",
    "build_span_create",
    "build_trace_create",
    "build_trace_update",
    "validate_scores",
]
