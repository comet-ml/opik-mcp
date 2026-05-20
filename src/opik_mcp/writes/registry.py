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

from opik_mcp.writes.models import EXAMPLES, MODELS
from opik_mcp.writes.scopes import (
    SCOPE_DATASET_EDIT,
    SCOPE_EXPERIMENT_CREATE,
    SCOPE_PROMPT_CREATE,
    SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
    SCOPE_TRACE_SPAN_THREAD_LOG,
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


# Internal mutable map, frozen via ``MappingProxyType`` before export so
# callers cannot accidentally insert at runtime (defends against tests that
# would otherwise leak state into other tests).
_REGISTRY: dict[str, WriteOperation] = {
    "trace.create": WriteOperation(
        name="trace.create",
        pydantic_model=MODELS["trace.create"],
        endpoint="/v1/private/traces",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/traces/batch",
        description=(
            "Log a single trace (or a batch). Sets up the parent for spans/scores/comments."
        ),
        example=EXAMPLES["trace.create"],
    ),
    "trace.update": WriteOperation(
        name="trace.update",
        pydantic_model=MODELS["trace.update"],
        endpoint="/v1/private/traces/{id}",
        method="PATCH",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/traces/batch",
        description="Finalize or amend an existing trace by id.",
        example=EXAMPLES["trace.update"],
    ),
    "span.create": WriteOperation(
        name="span.create",
        pydantic_model=MODELS["span.create"],
        endpoint="/v1/private/spans",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_LOG,
        supports_batch=True,
        batch_endpoint="/v1/private/spans/batch",
        parent_id_fields=("trace_id",),
        description="Log a single span on an existing trace (or a batch).",
        example=EXAMPLES["span.create"],
    ),
    "score.create": WriteOperation(
        name="score.create",
        pydantic_model=MODELS["score.create"],
        # Path is rewritten by the dispatcher from ``target`` / ``target_id``
        # — the template here documents the shape but is not used verbatim.
        endpoint="/v1/private/{target_path}/{target_id}/feedback-scores",
        method="PUT",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
        supports_batch=True,
        batch_endpoint="/v1/private/{target_path}/feedback-scores",
        parent_id_fields=("target", "target_id"),
        description="Attach a numeric feedback score to a trace, span, or thread.",
        example=EXAMPLES["score.create"],
        failure_modes=("thread_requires_batch", "heterogeneous_targets"),
    ),
    "comment.create": WriteOperation(
        name="comment.create",
        pydantic_model=MODELS["comment.create"],
        endpoint="/v1/private/{target_path}/{target_id}/comments",
        method="POST",
        oauth_scope=SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
        supports_batch=False,
        parent_id_fields=("target", "target_id"),
        description="Attach a free-text comment to a trace, span, or thread.",
        example=EXAMPLES["comment.create"],
    ),
    "prompt_version.save": WriteOperation(
        name="prompt_version.save",
        pydantic_model=MODELS["prompt_version.save"],
        endpoint="/v1/private/prompts/versions",
        method="POST",
        oauth_scope=SCOPE_PROMPT_CREATE,
        supports_batch=False,
        description=(
            "Save a new prompt version. Creates the prompt by name if missing; "
            "BE auto-assigns the commit when omitted."
        ),
        example=EXAMPLES["prompt_version.save"],
    ),
    "test_suite.create": WriteOperation(
        name="test_suite.create",
        pydantic_model=MODELS["test_suite.create"],
        endpoint="/v1/private/datasets",
        method="POST",
        oauth_scope=SCOPE_DATASET_EDIT,
        supports_batch=False,
        description=(
            "Create an Opik 2.0 test suite (evaluation suite). The BE path "
            "remains /v1/private/datasets for back-compat; the dispatcher "
            "injects type='evaluation_suite' on the wire."
        ),
        example=EXAMPLES["test_suite.create"],
    ),
    "test_suite_item.upsert": WriteOperation(
        name="test_suite_item.upsert",
        pydantic_model=MODELS["test_suite_item.upsert"],
        endpoint="/v1/private/datasets/items",
        method="PUT",
        oauth_scope=SCOPE_DATASET_EDIT,
        # Always-envelope operation. A top-level list would silently lose
        # all but the first envelope, so the dispatcher rejects it via
        # supports_batch=False — items live inside the envelope.
        supports_batch=False,
        parent_id_fields=("test_suite_name", "test_suite_id"),
        description=(
            "Upsert items into a test suite. Always pass the envelope "
            "{test_suite_name|test_suite_id, items: [...]}."
        ),
        example=EXAMPLES["test_suite_item.upsert"],
        failure_modes=(
            "test_suite_parent_missing",
            "test_suite_parent_conflict",
            "data_field_conflict",
        ),
    ),
    "experiment.create": WriteOperation(
        name="experiment.create",
        pydantic_model=MODELS["experiment.create"],
        endpoint="/v1/private/experiments",
        method="POST",
        oauth_scope=SCOPE_EXPERIMENT_CREATE,
        supports_batch=False,
        parent_id_fields=("test_suite_name", "test_suite_id"),
        description="Create an experiment scoped to a test suite.",
        example=EXAMPLES["experiment.create"],
        failure_modes=("test_suite_parent_missing", "test_suite_parent_conflict"),
    ),
    "experiment_item.create": WriteOperation(
        name="experiment_item.create",
        pydantic_model=MODELS["experiment_item.create"],
        endpoint="/v1/private/experiments/items",
        method="POST",
        oauth_scope=SCOPE_EXPERIMENT_CREATE,
        # Always-array shape via the {experiment_items: [...]} envelope.
        supports_batch=True,
        parent_id_fields=("experiment_id", "test_suite_item_id", "trace_id"),
        description="Attach trace + dataset_item rows to an experiment. Always the array envelope.",
        example=EXAMPLES["experiment_item.create"],
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
