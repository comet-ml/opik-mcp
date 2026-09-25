"""Operation registry for the universal ``write`` tool (spec §3).

One ``WriteOperation`` entry per ``operation`` enum value. The dispatcher
treats the registry as the single source of truth for endpoint, HTTP
method, OAuth scope, batch handling, and parent-id fields. Adding a new
operation is a single-entry diff that picks up the validation pipeline,
the description string, the schema tool, and conformance tests
automatically.

The registry is built once at module import and exposed as an immutable
mapping so other modules can ``from .registry import WRITE_REGISTRY``
without paying re-build cost on every call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from pydantic import BaseModel

from opik_mcp.writes.operations import diagnostics, evaluation, observability, threads
from opik_mcp.writes.scopes import (
    SCOPE_DATASET_EDIT,
    SCOPE_EXPERIMENT_CREATE,
    SCOPE_PROJECT_DATA_VIEW,
    SCOPE_PROMPT_CREATE,
    SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
    SCOPE_TRACE_SPAN_THREAD_LOG,
)
from opik_mcp.writes.wire import (
    BuildFn,
    DecorateFn,
    DryRunNoteFn,
    PrepareFn,
    RetryFn,
    ValidateFn,
)

BATCH_LIMIT: Final = 1000


@dataclass(frozen=True)
class WriteOperation:
    name: str
    pydantic_model: type[BaseModel]
    endpoint: str
    method: str
    oauth_scope: str
    supports_batch: bool
    batch_endpoint: str | None = None
    parent_id_fields: tuple[str, ...] = ()
    description: str = ""
    example: dict[str, Any] = field(default_factory=dict)
    # Domain-specific issue ``code`` values this operation can emit on
    # ``validation_failed``. Universal Pydantic codes (``missing``,
    # ``extra_forbidden``, ``type_mismatch``, ``float_parsing``, ...) and
    # universal dispatcher codes (``batch_too_large``, ``empty_batch``,
    # ``batch_unsupported``) are always possible and not duplicated here.
    failure_modes: tuple[str, ...] = ()
    envelope_items_key: str | None = None
    """Where an always-envelope operation keeps its records, in the built body.

    Without it ``item_count`` is the length of the top-level payload, which for
    these operations is the envelope: a 500-case upsert reported 1.
    """

    # --- what this operation does that no other one does ----------------- #
    # The dispatcher runs the same five stages for every write; these are the
    # points where an operation differs. Each lives in the operation's own
    # module (``writes/operations/``) so its wire translation, its pre-flight
    # resolves and what it says about its result sit together instead of as
    # branches in the dispatcher. See ``writes/wire.py`` for the signatures.
    validate_fn: ValidateFn | None = None
    """A rule the payload's model cannot express on its own, checked after
    Pydantic and before anything is sent. Raises; returns nothing."""
    build_fn: BuildFn | None = None
    """Turn validated models into the request. Defaults to the endpoint plus
    an ``exclude_none`` dump of the single item, which is right for the plain
    creates and wrong for anything with a batch envelope or a path id."""
    prepare_fn: PrepareFn | None = None
    """Resolve an identifier the wire needs but the caller does not carry, or
    refuse the call. Live path only: a dry run touches no network."""
    retry_fn: RetryFn | None = None
    """Reinterpret a backend answer that means something else."""
    decorate_fn: DecorateFn | None = None
    """Add to the success envelope: a link to open, what to expect next."""
    dry_run_note_fn: DryRunNoteFn | None = None
    """What a preview cannot show, when it cannot show it."""


# Internal mutable map, frozen via ``MappingProxyType`` before export so
# callers cannot accidentally insert at runtime (defends against tests that
# would otherwise leak state into other tests).
_REGISTRY: dict[str, WriteOperation] = {
    "trace.create": WriteOperation(
        name="trace.create",
        decorate_fn=observability.decorate_with_page,
        build_fn=observability.build_trace_create,
        pydantic_model=observability.TraceCreate,
        endpoint="/v1/private/traces",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/traces/batch",
        description=(
            "Log a single trace (or a batch). Sets up the parent for spans/scores/comments."
        ),
        example=observability.TRACE_CREATE_EXAMPLE,
    ),
    "trace.update": WriteOperation(
        name="trace.update",
        decorate_fn=observability.decorate_with_page,
        build_fn=observability.build_trace_update,
        pydantic_model=observability.TraceUpdate,
        endpoint="/v1/private/traces/{id}",
        method="PATCH",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/traces/batch",
        description="Finalize or amend an existing trace by id.",
        example=observability.TRACE_UPDATE_EXAMPLE,
    ),
    "span.create": WriteOperation(
        name="span.create",
        decorate_fn=observability.decorate_with_page,
        build_fn=observability.build_span_create,
        pydantic_model=observability.SpanCreate,
        endpoint="/v1/private/spans",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/spans/batch",
        parent_id_fields=("trace_id",),
        description="Log a single span on an existing trace (or a batch).",
        example=observability.SPAN_CREATE_EXAMPLE,
    ),
    "score.create": WriteOperation(
        name="score.create",
        decorate_fn=observability.decorate_with_page,
        build_fn=observability.build_score_create,
        validate_fn=observability.validate_scores,
        pydantic_model=observability.ScoreCreate,
        # Path is rewritten by the dispatcher from ``target`` / ``target_id``
        # — the template here documents the shape but is not used verbatim.
        endpoint="/v1/private/{target_path}/{target_id}/feedback-scores",
        method="PUT",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
        supports_batch=True,
        batch_endpoint="/v1/private/{target_path}/feedback-scores",
        parent_id_fields=("target", "target_id"),
        description="Attach a numeric feedback score to a trace, span, or thread.",
        example=observability.SCORE_CREATE_EXAMPLE,
        failure_modes=("thread_requires_batch", "heterogeneous_targets"),
    ),
    "comment.create": WriteOperation(
        name="comment.create",
        decorate_fn=observability.decorate_with_page,
        build_fn=observability.build_comment_create,
        prepare_fn=threads.resolve_comment_thread_id,
        dry_run_note_fn=threads.comment_dry_run_note,
        pydantic_model=observability.CommentCreate,
        endpoint="/v1/private/{target_path}/{target_id}/comments",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
        supports_batch=False,
        parent_id_fields=("target", "target_id"),
        description="Attach a free-text comment to a trace, span, or thread.",
        example=observability.COMMENT_CREATE_EXAMPLE,
    ),
    "prompt_version.save": WriteOperation(
        name="prompt_version.save",
        build_fn=evaluation.build_prompt_version_save,
        pydantic_model=evaluation.PromptVersionSave,
        endpoint="/v1/private/prompts/versions",
        method="POST",
        oauth_scope=SCOPE_PROMPT_CREATE,
        supports_batch=False,
        description=(
            "Save a new prompt version. Creates the prompt by name if missing; "
            "BE auto-assigns the commit when omitted."
        ),
        example=evaluation.PROMPT_VERSION_SAVE_EXAMPLE,
    ),
    "dataset.create": WriteOperation(
        name="dataset.create",
        build_fn=evaluation.build_dataset_create,
        pydantic_model=evaluation.DatasetCreate,
        endpoint="/v1/private/datasets",
        method="POST",
        oauth_scope=SCOPE_DATASET_EDIT,
        supports_batch=False,
        description=(
            "Create a dataset. Pass type='test_suite' for an evaluation suite "
            "(items carry assertions and run as tests); default is a plain dataset."
        ),
        example=evaluation.DATASET_CREATE_EXAMPLE,
    ),
    "dataset_item.upsert": WriteOperation(
        name="dataset_item.upsert",
        build_fn=evaluation.build_dataset_item_upsert,
        pydantic_model=evaluation.DatasetItemUpsert,
        endpoint="/v1/private/datasets/items",
        method="PUT",
        oauth_scope=SCOPE_DATASET_EDIT,
        # Always-envelope operation. A top-level list would silently lose
        # all but the first envelope, so the dispatcher rejects it via
        # supports_batch=False — items live inside the envelope.
        supports_batch=False,
        envelope_items_key="items",
        parent_id_fields=("dataset_name", "dataset_id"),
        description=(
            "Upsert items into a dataset or test suite. Always pass the envelope "
            "{dataset_name|dataset_id, items: [...]}."
        ),
        example=evaluation.DATASET_ITEM_UPSERT_EXAMPLE,
        failure_modes=(
            "dataset_parent_missing",
            "dataset_parent_conflict",
            "data_field_conflict",
        ),
    ),
    "experiment.create": WriteOperation(
        name="experiment.create",
        pydantic_model=evaluation.ExperimentCreate,
        endpoint="/v1/private/experiments",
        method="POST",
        oauth_scope=SCOPE_EXPERIMENT_CREATE,
        supports_batch=False,
        parent_id_fields=("dataset_name", "dataset_id"),
        description="Create an experiment scoped to a dataset or test suite.",
        example=evaluation.EXPERIMENT_CREATE_EXAMPLE,
        failure_modes=("dataset_parent_missing", "dataset_parent_conflict"),
    ),
    "experiment_item.create": WriteOperation(
        name="experiment_item.create",
        pydantic_model=evaluation.ExperimentItemCreate,
        endpoint="/v1/private/experiments/items",
        method="POST",
        oauth_scope=SCOPE_EXPERIMENT_CREATE,
        # Always-array shape via the {experiment_items: [...]} envelope.
        supports_batch=True,
        envelope_items_key="experiment_items",
        parent_id_fields=("experiment_id", "dataset_item_id", "trace_id"),
        description="Attach trace + dataset_item rows to an experiment. Always the array envelope.",
        example=evaluation.EXPERIMENT_ITEM_CREATE_EXAMPLE,
    ),
    "thread.close": WriteOperation(
        name="thread.close",
        decorate_fn=observability.decorate_with_page,
        build_fn=threads.build_thread_lifecycle,
        pydantic_model=observability.ThreadClose,
        endpoint="/v1/private/traces/threads/close",
        method="PUT",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=False,
        description=(
            "Close a thread (mark it inactive/done). Pass thread_id + project "
            "(project_name or project_id)."
        ),
        example=observability.THREAD_CLOSE_EXAMPLE,
        failure_modes=("thread_project_missing",),
    ),
    "thread.open": WriteOperation(
        name="thread.open",
        decorate_fn=observability.decorate_with_page,
        build_fn=threads.build_thread_lifecycle,
        pydantic_model=observability.ThreadOpen,
        endpoint="/v1/private/traces/threads/open",
        method="PUT",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=False,
        description=(
            "Reopen a previously closed thread (mark it active). Pass thread_id "
            "+ project (project_name or project_id)."
        ),
        example=observability.THREAD_OPEN_EXAMPLE,
        failure_modes=("thread_project_missing",),
    ),
    "agent_insights_issue.resolve": WriteOperation(
        name="agent_insights_issue.resolve",
        build_fn=diagnostics.build_issue_action,
        prepare_fn=diagnostics.prepare_scope,
        decorate_fn=diagnostics.decorate,
        dry_run_note_fn=diagnostics.dry_run_note,
        pydantic_model=diagnostics.AgentInsightsIssueResolve,
        endpoint="/v1/private/agent-insights/issues/{issue_id}",
        method="PATCH",
        oauth_scope=SCOPE_PROJECT_DATA_VIEW,
        supports_batch=False,
        description=(
            "Mark a Diagnostics (Agent Insights) issue resolved — the failure it groups has been "
            "dealt with. It leaves the default issue list and shows under status='resolved'. Pass "
            "issue_id + project_id or project_name. Do this only when the user asks: whether a "
            "failure is fixed is their call, not an inference from the traces."
        ),
        example=diagnostics.AGENT_INSIGHTS_ISSUE_RESOLVE_EXAMPLE,
        failure_modes=("project_scope_missing",),
    ),
    "agent_insights_issue.close": WriteOperation(
        name="agent_insights_issue.close",
        build_fn=diagnostics.build_issue_action,
        prepare_fn=diagnostics.prepare_scope,
        decorate_fn=diagnostics.decorate,
        dry_run_note_fn=diagnostics.dry_run_note,
        pydantic_model=diagnostics.AgentInsightsIssueClose,
        endpoint="/v1/private/agent-insights/issues/{issue_id}",
        method="PATCH",
        oauth_scope=SCOPE_PROJECT_DATA_VIEW,
        supports_batch=False,
        description=(
            "Mark a Diagnostics (Agent Insights) issue closed — not worth acting on, as opposed "
            "to fixed. It leaves the default issue list and shows under status='closed'. Pass "
            "issue_id + project_id or project_name. Do this only when the user asks."
        ),
        example=diagnostics.AGENT_INSIGHTS_ISSUE_CLOSE_EXAMPLE,
        failure_modes=("project_scope_missing",),
    ),
    "agent_insights_issue.reopen": WriteOperation(
        name="agent_insights_issue.reopen",
        build_fn=diagnostics.build_issue_action,
        prepare_fn=diagnostics.prepare_scope,
        decorate_fn=diagnostics.decorate,
        dry_run_note_fn=diagnostics.dry_run_note,
        pydantic_model=diagnostics.AgentInsightsIssueReopen,
        endpoint="/v1/private/agent-insights/issues/{issue_id}",
        method="PATCH",
        oauth_scope=SCOPE_PROJECT_DATA_VIEW,
        supports_batch=False,
        description=(
            "Put a resolved or closed Diagnostics (Agent Insights) issue back on the open list, "
            "for a failure that came back or was closed too early. Pass issue_id + project_id or "
            "project_name."
        ),
        example=diagnostics.AGENT_INSIGHTS_ISSUE_REOPEN_EXAMPLE,
        failure_modes=("project_scope_missing",),
    ),
    "agent_insights_job.enable": WriteOperation(
        name="agent_insights_job.enable",
        build_fn=diagnostics.build_job_action,
        prepare_fn=diagnostics.prepare_scope,
        retry_fn=diagnostics.retry,
        decorate_fn=diagnostics.decorate,
        dry_run_note_fn=diagnostics.dry_run_note,
        pydantic_model=diagnostics.AgentInsightsJobEnable,
        endpoint="/v1/private/agent-insights/jobs/{project_id}",
        method="POST",
        oauth_scope=SCOPE_PROJECT_DATA_VIEW,
        supports_batch=False,
        description=(
            "Turn Diagnostics (Agent Insights) on for a project: it then scans "
            "daily and groups recurring failures into issues you can read with "
            "list('agent_insights_issue', …). Pass project_id or project_name. "
            "Idempotent — an existing job is switched back on. Pair it with "
            "agent_insights_job.trigger to scan now instead of waiting for the "
            "nightly run. Refused when the deployment has no Diagnostics."
        ),
        example=diagnostics.AGENT_INSIGHTS_JOB_ENABLE_EXAMPLE,
        failure_modes=("project_scope_missing", "diagnostics_unavailable"),
    ),
    "agent_insights_job.trigger": WriteOperation(
        name="agent_insights_job.trigger",
        build_fn=diagnostics.build_job_action,
        prepare_fn=diagnostics.prepare_scope,
        retry_fn=diagnostics.retry,
        decorate_fn=diagnostics.decorate,
        dry_run_note_fn=diagnostics.dry_run_note,
        pydantic_model=diagnostics.AgentInsightsJobTrigger,
        endpoint="/v1/private/agent-insights/jobs/{project_id}/trigger",
        method="POST",
        oauth_scope=SCOPE_PROJECT_DATA_VIEW,
        supports_batch=False,
        description=(
            "Run a Diagnostics (Agent Insights) scan for a project now, over the "
            "last 24 hours, instead of waiting for the nightly run. Fire and "
            "forget: the response says where to watch it, and issues appear in "
            "list('agent_insights_issue', …) once the run finishes (minutes). "
            "Needs Diagnostics enabled for the project — see "
            "agent_insights_job.enable."
        ),
        example=diagnostics.AGENT_INSIGHTS_JOB_TRIGGER_EXAMPLE,
        failure_modes=(
            "project_scope_missing",
            "diagnostics_unavailable",
            "diagnostics_not_enabled",
        ),
    ),
}


WRITE_REGISTRY: Final[Mapping[str, WriteOperation]] = MappingProxyType(_REGISTRY)
WRITE_OPERATIONS: Final[tuple[str, ...]] = tuple(_REGISTRY.keys())


def get_operation(name: str) -> WriteOperation | None:
    return WRITE_REGISTRY.get(name)


__all__ = [
    "BATCH_LIMIT",
    "WRITE_OPERATIONS",
    "WRITE_REGISTRY",
    "WriteOperation",
    "get_operation",
]
