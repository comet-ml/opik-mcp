"""Traces, spans, and the annotations that hang off them.

Four operations that share one shape of problem: the backend's batch route
takes an envelope keyed by the collection name, and the singleton route
encodes an id in the path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from opik_mcp.config import Settings
from opik_mcp.read_list.ui_links import project_page_url, thread_page_url, trace_page_url
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


#: Which Logs view a write's change shows up on, when the link cannot name a row.
_LOGS_VIEW: Final[dict[str, str]] = {
    "trace.create": "logsType=traces",
    "trace.update": "logsType=traces",
    "span.create": "logsType=spans",
    "thread.close": "logsType=threads",
    "thread.open": "logsType=threads",
}


def _id_of(item: BaseModel, field: str) -> str | None:
    """One id off a validated write model, as the string a URL is built from.

    The models type their ids as ``UUID``, not ``str`` — which is what a
    ``isinstance(value, str)`` guard here got wrong, silently: every real
    write came back unlinked while the tests, whose stand-in model typed the
    same fields as ``str``, passed. Hence one accessor rather than the check
    repeated at each call site, where it drifted once already.
    """
    value = getattr(item, field, None)
    if value is None:
        return None
    text = str(value)
    return text or None


def _annotation_url(settings: Settings, project_id: str, item: BaseModel) -> str | None:
    """Where an annotation's target is visible.

    A score or a comment names what it is attached to and what kind of thing
    that is, so a trace or a thread is addressable outright. A span is not:
    it is only reachable inside its trace, and the annotation names the span
    without naming the trace. So a span annotation gets the page rather than
    the row — the same tier a score name gets, for the same reason.
    """
    target = getattr(item, "target", None)
    target_id = _id_of(item, "target_id")
    if not target_id:
        return None
    if target == "trace":
        return trace_page_url(settings, project_id, target_id)
    if target == "thread":
        return thread_page_url(settings, project_id, target_id)
    return project_page_url(settings, project_id, "logs", query="logsType=spans")


def decorate_with_page(
    op: WriteOperation,
    items: list[BaseModel],
    out: dict[str, Any],
    settings: Settings,
    project_id: str | None,
) -> None:
    """Where to go and look at what this write changed.

    One link per call and never one per item: a batch may have changed fifty
    rows, and most write targets — a score, a comment, an experiment item —
    have no page of their own. So a single write of one thing links to that
    thing, and a batch links to the page it landed on.

    The project must already be known, from the request or from what the
    dispatcher resolved before sending. Looking one up here would mean a call
    after the write has already succeeded, and a failure in it would turn a
    write that worked into an error — the price of a link is never the result.
    """
    # ``project_id`` is what the dispatcher resolved, where an operation
    # resolves anything; these do not, so the payload is the other source.
    # ``project_name`` is deliberately not one: turning it into an id is a
    # call, and this runs after the write has already succeeded.
    if not project_id:
        project_id = next(
            (found for item in items if (found := _id_of(item, "project_id"))),
            None,
        )
    if not project_id:
        return
    single = items[0] if len(items) == 1 else None
    url: str | None = None
    if single is not None:
        if op.name == "span.create":
            trace_id = _id_of(single, "trace_id")
            if trace_id:
                url = trace_page_url(settings, project_id, trace_id, span_id=_id_of(single, "id"))
        elif op.name in {"thread.close", "thread.open"}:
            thread_id = _id_of(single, "thread_id")
            if thread_id:
                url = thread_page_url(settings, project_id, thread_id)
        elif op.name in {"trace.create", "trace.update"}:
            trace_id = _id_of(single, "id")
            if trace_id:
                url = trace_page_url(settings, project_id, trace_id)
        elif op.name in {"score.create", "comment.create"}:
            url = _annotation_url(settings, project_id, single)
    if url is None:
        # A batch, or a single whose id the caller left to the backend: the
        # page is still the right answer, just not a row on it.
        url = project_page_url(settings, project_id, "logs", query=_LOGS_VIEW.get(op.name))
    if url is not None:
        out["url"] = url


__all__ = [
    "build_comment_create",
    "build_score_create",
    "build_span_create",
    "build_trace_create",
    "build_trace_update",
    "decorate_with_page",
    "validate_scores",
]
