"""Traces, spans, and the annotations that hang off them.

Four operations that share one shape of problem: the backend's batch route
takes an envelope keyed by the collection name, and the singleton route
encodes an id in the path.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from opik_mcp.config import Settings
from opik_mcp.read_list.ui_links import project_page_url, thread_page_url, trace_page_url
from opik_mcp.writes.errors import ValidationFailedError, ValidationIssue
from opik_mcp.writes.models import (
    EXAMPLE_TIME,
    InputOutput,
    Metadata,
    _ClientIdMixin,
    _ProjectMixin,
    _RequiredProjectMixin,
    _StrictBase,
    _TagsMixin,
    example_uuid,
)
from opik_mcp.writes.wire import BuildContext, WireRequest, dump

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


ScoreTarget = Literal["trace", "span", "thread"]
"""Targets supported by ``score.create`` / ``comment.create``. ``thread``
forces the batch shape on the score endpoint — see spec §3.2."""


class TraceCreate(_StrictBase, _ClientIdMixin, _TagsMixin, _ProjectMixin):
    """``POST /v1/private/traces`` — log a single trace."""

    name: str = Field(min_length=1, max_length=200, description="Display name.")
    start_time: datetime = Field(description="ISO-8601 timestamp; required by the BE.")
    end_time: datetime | None = Field(default=None, description="ISO-8601; set when finalizing.")
    input: InputOutput = Field(default=None)
    output: InputOutput = Field(default=None)
    metadata: Metadata = Field(default=None)
    thread_id: str | None = Field(default=None, max_length=200)
    last_updated_at: datetime | None = Field(default=None)


class TraceUpdate(_StrictBase, _TagsMixin, _ProjectMixin):
    """``PATCH /v1/private/traces/{id}`` — finalize or amend a trace.

    Project context (``project_name`` or ``project_id``) must match the
    trace's current project — the BE rejects mismatches with a 409.
    """

    id: UUID = Field(description="UUID of the trace to update.")
    end_time: datetime | None = Field(default=None)
    output: InputOutput = Field(default=None)
    metadata: Metadata = Field(default=None)
    thread_id: str | None = Field(default=None, max_length=200)
    last_updated_at: datetime | None = Field(default=None)


SpanType = Literal["general", "llm", "tool"]


class SpanCreate(_StrictBase, _ClientIdMixin, _TagsMixin, _ProjectMixin):
    """``POST /v1/private/spans`` — log a single span on an existing trace."""

    trace_id: UUID = Field(description="UUID of the parent trace.")
    parent_span_id: UUID | None = Field(default=None)
    name: str = Field(min_length=1, max_length=200)
    type: SpanType = Field(default="general")
    start_time: datetime
    end_time: datetime | None = Field(default=None)
    input: InputOutput = Field(default=None)
    output: InputOutput = Field(default=None)
    metadata: Metadata = Field(default=None)
    model: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    usage: dict[str, Any] | None = Field(default=None)


_TARGET_ID_DESC = (
    "Identifier of the thing being annotated. For target='trace'/'span': the "
    "entity UUID. For target='thread': the thread_id STRING — the value shown "
    "as `id` in list('thread', project_id=…) and read('thread', …). Threads are "
    "keyed by that string, not a UUID."
)


class _AnnotationTarget(_StrictBase):
    """Shared target fields + per-target id validation for score/comment.

    One uniform contract for the LLM: ``target_id`` is a UUID for trace/span
    and the ``thread_id`` string for thread. Thread annotations also need a
    project (a thread_id is unique only within a project); trace/span ignore it
    (their ids are globally unique). The dispatcher turns the string thread_id
    into whatever each BE route needs (score sends it as-is; comment resolves it
    to the thread's model UUID), so callers never juggle two identifiers.
    """

    target: ScoreTarget = Field(description="What the annotation attaches to.")
    target_id: str = Field(min_length=1, max_length=200, description=_TARGET_ID_DESC)
    project_name: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_target(self) -> _AnnotationTarget:
        if self.target in ("trace", "span"):
            try:
                # Normalize to canonical dashed form: Python's UUID() also
                # accepts braces / urn: / dashless hex, but the Java BE's
                # `UUID.fromString` path param does not — canonicalize so a
                # locally-valid id is always BE-valid on the wire.
                self.target_id = str(UUID(self.target_id))
            except ValueError:
                raise ValueError(
                    f"target_id_not_uuid: target={self.target!r} needs a UUID "
                    f"target_id; got {self.target_id!r}."
                ) from None
        else:  # thread — keyed by the thread_id string, scoped to a project
            if self.project_name is None and self.project_id is None:
                raise ValueError(
                    "thread_project_missing: annotating a thread needs "
                    "project_name or project_id (a thread_id is unique only "
                    "within a project)."
                )
        return self


class ScoreCreate(_AnnotationTarget):
    """Attach a numeric feedback score to a trace, span, or thread.

    trace/span -> ``PUT /v1/private/{traces|spans}/{id}/feedback-scores``.
    thread -> ``PUT /v1/private/traces/threads/feedback-scores`` (batch-only on
    the BE, so the dispatcher requires the array form for target='thread').
    """

    name: str = Field(min_length=1, max_length=200)
    value: float = Field(ge=-1e9, le=1e9)
    source: Literal["sdk", "ui", "online_scoring"] = "sdk"
    category_name: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)


class CommentCreate(_AnnotationTarget):
    """Attach a free-text comment to a trace, span, or thread.

    trace/span -> ``POST /v1/private/{traces|spans}/{id}/comments``.
    thread -> ``POST /v1/private/traces/threads/{thread_model_id}/comments``;
    the dispatcher resolves the thread_id string to the model UUID first, so the
    caller passes the same thread_id string used everywhere else.
    """

    text: str = Field(min_length=1, max_length=10_000)


TRACE_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "name": "openai.chat",
    "start_time": EXAMPLE_TIME,
    "project_name": "demo",
    "input": {"messages": [{"role": "user", "content": "hi"}]},
}

TRACE_UPDATE_EXAMPLE: Final[dict[str, Any]] = {
    "id": example_uuid("01"),
    "end_time": EXAMPLE_TIME,
    "output": {"text": "hello!"},
    "tags_to_add": ["regression"],
}

SPAN_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "trace_id": example_uuid("01"),
    "name": "openai.chat",
    "type": "llm",
    "start_time": EXAMPLE_TIME,
    "model": "gpt-4o",
    "provider": "openai",
}

SCORE_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "target": "trace",
    "target_id": example_uuid("01"),
    "name": "helpfulness",
    "value": 0.8,
    "reason": "user-confirmed",
}

COMMENT_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "target": "span",
    "target_id": example_uuid("01"),
    "text": "retry with temperature=0",
}


# The thread lifecycle models sit here, not beside their builder in threads.py:
# mypy reads every Pydantic model as explicit Any, and threads.py is typed
# strictly while this module is still on the Any baseline.
class _ThreadLifecycle(_StrictBase, _RequiredProjectMixin):
    """Shared shape for thread status changes (close/open).

    A thread is keyed by ``thread_id`` within a project, so the BE's
    ``TraceThreadIdentifier`` body takes the id plus project scope, and its
    validator rejects a request with neither project field (400). Catching it
    locally with a recovery code gets the LLM to add a project instead of
    round-tripping a confusing 400. The model carries no ``target`` field, so
    the dispatcher's ``exclude_none`` dump is the wire body verbatim.
    """

    thread_id: str = Field(min_length=1, max_length=200)

    _missing_project_error: ClassVar[str] = (
        "thread_project_missing: pass `project_name` or `project_id` "
        "to identify the thread's project."
    )


class ThreadClose(_ThreadLifecycle):
    """``POST /v1/private/traces/threads/close`` — mark a thread inactive/done."""


class ThreadOpen(_ThreadLifecycle):
    """``POST /v1/private/traces/threads/open`` — reopen a thread (→ active)."""


THREAD_CLOSE_EXAMPLE: Final[dict[str, str]] = {
    "thread_id": "conversation-42",
    "project_name": "demo",
}

THREAD_OPEN_EXAMPLE: Final[dict[str, str]] = {
    "thread_id": "conversation-42",
    "project_name": "demo",
}


#: Path segment per annotation target, for the score and comment routes.
TARGET_PATH: Final[dict[str, str]] = {
    "trace": "traces",
    "span": "spans",
    "thread": "traces/threads",
}


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
    _op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
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
    _op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """A thread comment's path takes the model UUID ``prepare_fn`` resolved.
    A dry run has none and shows the caller's thread_id in its place."""
    single = dump(items[0])
    target = single.pop("target")
    target_id = single.pop("target_id")
    if target == "thread" and ctx.prepared is not None:
        target_id = ctx.prepared
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


#: Which Logs view a write's change shows up on, when the link cannot name a
#: row. The two annotation ops are absent by decision, not omission: a batch
#: of them may name traces, spans and threads together, and no one view shows
#: all three, so their link is the Logs page itself.
_LOGS_VIEW: Final[dict[str, str]] = {
    "trace.create": "logsType=traces",
    "trace.update": "logsType=traces",
    "span.create": "logsType=spans",
    "thread.close": "logsType=threads",
    "thread.open": "logsType=threads",
}


def _id_of(item: BaseModel, field: str) -> str | None:
    """One id off a validated write model, as the string a URL is built from.

    The models type their ids as ``UUID``, not ``str``, so an
    ``isinstance(value, str)`` guard silently unlinked every real write while
    tests whose stand-in typed them as ``str`` passed. One accessor rather
    than that check repeated at each call site.
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


def decorate_comment(
    op: WriteOperation,
    items: list[BaseModel],
    out: dict[str, Any],
    settings: Settings,
    _prepared: str | None,
) -> None:
    """``decorate_with_page`` without the resolved value, which for a comment is
    a thread's model UUID and not the project id that function expects there."""
    decorate_with_page(op, items, out, settings, None)


__all__ = [
    "build_comment_create",
    "build_score_create",
    "build_span_create",
    "build_trace_create",
    "build_trace_update",
    "decorate_comment",
    "decorate_with_page",
    "validate_scores",
]
