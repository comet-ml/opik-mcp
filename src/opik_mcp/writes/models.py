"""Pydantic models for the 10 write operations (spec §3).

Each operation has one model that validates a single-item payload. Batch
form is handled at the dispatcher level by validating an array against the
same model element-by-element; the model definitions themselves do not see
the batch envelope.

Cross-cutting rules (spec §3.3) live on small mixins so a regression in
"tags xor tags_to_add" rejection on, say, ``trace.create`` cannot silently
ship — every model with tags inherits the same validator.

The models are deliberately permissive about fields the BE accepts but the
LLM rarely needs (``input``/``output`` are ``dict``-or-``list``, metadata
is ``dict[str, Any]``, etc.) so that valid BE payloads from the SDKs round
trip through the MCP tool without losing fidelity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

# --- shared types --------------------------------------------------------- #

# Most write payloads accept either a list or a dict shape on input/output;
# the BE preserves whichever the caller sent.
InputOutput = dict[str, Any] | list[Any] | None
Metadata = dict[str, Any] | None
TagList = list[str]

ScoreTarget = Literal["trace", "span", "thread"]
"""Targets supported by ``score.create`` / ``comment.create``. ``thread``
forces the batch shape on the score endpoint — see spec §3.2."""


# --- mixins --------------------------------------------------------------- #


class _StrictBase(BaseModel):
    """Common config. ``extra='forbid'`` matches the spec's
    ``additionalProperties: false`` and surfaces typos as validation errors
    instead of letting them silently round-trip to the BE.
    """

    model_config = ConfigDict(extra="forbid")


class _TagsMixin(BaseModel):
    """Replace-vs-patch tag handling — spec §3.3 'Tags'.

    Mixing ``tags`` (replace) with either patch field is rejected with the
    ``combined_tag_modes`` code so the LLM gets an unambiguous correction
    path. The two patch fields can be combined with each other freely.
    """

    tags: TagList | None = Field(
        default=None,
        description="Replace the entity's tags with this set.",
    )
    tags_to_add: TagList | None = Field(
        default=None,
        description=(
            "Patch: add these tags. Combine with tags_to_remove; mutually exclusive with `tags`."
        ),
    )
    tags_to_remove: TagList | None = Field(
        default=None,
        description=(
            "Patch: remove these tags. Combine with tags_to_add; mutually exclusive with `tags`."
        ),
    )

    @model_validator(mode="after")
    def _validate_tag_modes(self) -> _TagsMixin:
        replace_set = self.tags is not None
        patch_set = self.tags_to_add is not None or self.tags_to_remove is not None
        if replace_set and patch_set:
            raise ValueError(
                "combined_tag_modes: pass either `tags` (replace) or "
                "`tags_to_add`/`tags_to_remove` (patch), not both."
            )
        return self


class _ProjectMixin(BaseModel):
    """``project_name`` xor ``project_id`` — spec §3.3 'Project resolution'.

    Both unset is fine (BE falls back to a default project for workspaces
    that have one). Both set with conflicting values is a validation error.
    """

    project_name: str | None = Field(
        default=None, description="Project name. Mutually exclusive with project_id."
    )
    project_id: UUID | None = Field(
        default=None, description="Project UUID. Mutually exclusive with project_name."
    )

    @model_validator(mode="after")
    def _validate_project_xor(self) -> _ProjectMixin:
        if self.project_name is not None and self.project_id is not None:
            raise ValueError("project_xor: pass either `project_name` or `project_id`, not both.")
        return self


class _ClientIdMixin(BaseModel):
    """Optional client-side id for idempotency — spec §3.3 'IDs'.

    The top-level ``idempotency_key`` parameter (handed to the dispatcher)
    takes precedence if both are set; conflict-detection happens there.
    """

    id: UUID | None = Field(
        default=None,
        description=(
            "Client-supplied UUID for idempotency. Overridden by tool-level "
            "idempotency_key when both are present."
        ),
    )


# --- 1. trace.create ------------------------------------------------------ #


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


# --- 2. trace.update ------------------------------------------------------ #


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


# --- 3. span.create ------------------------------------------------------- #


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


# --- 4. score.create ------------------------------------------------------ #


class ScoreCreate(_StrictBase):
    """``PUT /v1/private/{target_path}/{target_id}/feedback-scores`` —
    attaches a numeric score to a trace, span, or thread.

    ``target`` selects which BE route the dispatcher hits. For ``thread``,
    the BE has no single-item endpoint, so the dispatcher rewrites this
    payload into a batch envelope; that constraint is captured by the
    ``target='thread'`` branch in ``dispatch.py`` rather than here so the
    model stays uniform across targets.
    """

    target: ScoreTarget = Field(description="What the score is attached to.")
    target_id: UUID = Field(description="UUID of the trace, span, or thread.")
    name: str = Field(min_length=1, max_length=200)
    value: float = Field(ge=-1e9, le=1e9)
    source: Literal["sdk", "ui", "online_scoring"] = "sdk"
    category_name: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)
    # ``thread`` writes optionally scope by project name so the BE can
    # disambiguate threads that exist in multiple projects; ignored for
    # trace/span writes (entity id is globally unique there).
    project_name: str | None = Field(default=None, max_length=200)


# --- 5. comment.create ---------------------------------------------------- #


class CommentCreate(_StrictBase):
    """``POST /v1/private/{target_path}/{target_id}/comments`` — free-text."""

    target: ScoreTarget = Field(description="What the comment is attached to.")
    target_id: UUID = Field(description="UUID of the trace, span, or thread.")
    text: str = Field(min_length=1, max_length=10_000)


# --- 6. prompt_version.save ---------------------------------------------- #


class PromptVersionSave(_StrictBase):
    """``POST /v1/private/prompts/versions`` — idempotent upsert.

    Creates the prompt if it doesn't exist (matched on ``name``) and
    attaches a new version. The BE auto-assigns ``commit`` when omitted.
    """

    name: str = Field(min_length=1, max_length=200, description="Prompt name (workspace-unique).")
    template: str = Field(min_length=1, max_length=200_000)
    commit: str | None = Field(default=None, max_length=200)
    tags: TagList | None = Field(default=None)
    metadata: Metadata = Field(default=None)
    change_description: str | None = Field(default=None, max_length=2000)


# --- 7. test_suite.create ------------------------------------------------ #
#
# Opik 2.0 renamed the entity formerly called "dataset" to "test_suite"
# (a.k.a. "evaluation suite"). REST paths on the BE keep `/datasets` for
# back-compat; the MCP surface mirrors the FE/UI naming. The dispatcher
# translates between MCP-facing `test_suite_*` fields and the wire's
# `dataset_*` fields, and injects ``type="evaluation_suite"`` on create.


class TestSuiteCreate(_StrictBase, _ClientIdMixin):
    """``POST /v1/private/datasets`` — create an Opik 2.0 test suite (eval suite)."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    tags: TagList | None = Field(default=None)
    metadata: Metadata = Field(default=None)


# --- 8. test_suite_item.upsert ------------------------------------------ #


class TestSuiteItem(BaseModel):
    """Single item inside a ``test_suite_item.upsert`` envelope.

    Items accept two equivalent shapes — flat (``input`` / ``expected_output``
    / ``metadata`` at the top level) or pre-enveloped (``data: {input, …}``).
    The dispatcher folds the flat form into the wire's ``{source, data: …}``
    shape; mixing the two on the same item is a validation error so neither
    form is silently dropped on conflict.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID | None = Field(default=None)
    data: dict[str, Any] | None = Field(default=None)
    input: InputOutput = Field(default=None)
    expected_output: InputOutput = Field(default=None)
    metadata: Metadata = Field(default=None)

    @model_validator(mode="after")
    def _validate_no_data_conflict(self) -> TestSuiteItem:
        if self.data is None:
            return self
        for k in ("input", "expected_output", "metadata"):
            if getattr(self, k) is not None and k in self.data:
                raise ValueError(
                    f"data_field_conflict: {k!r} is set both at the top level "
                    f"and inside `data` — pick one form."
                )
        return self


class TestSuiteItemUpsert(_StrictBase):
    """``PUT /v1/private/datasets/items`` — always envelope form.

    Exactly one of ``test_suite_name`` / ``test_suite_id`` is required;
    spec §3.2. Distinguished error codes per failure mode so recovery
    tooling can react differently to "you passed neither" vs. "you passed
    both".
    """

    test_suite_name: str | None = Field(default=None, max_length=200)
    test_suite_id: UUID | None = Field(default=None)
    items: list[TestSuiteItem] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate_parent_xor(self) -> TestSuiteItemUpsert:
        has_name = self.test_suite_name is not None
        has_id = self.test_suite_id is not None
        if has_name and has_id:
            raise ValueError(
                "test_suite_parent_conflict: pass either `test_suite_name` "
                "or `test_suite_id`, not both."
            )
        if not has_name and not has_id:
            raise ValueError(
                "test_suite_parent_missing: pass `test_suite_name` or `test_suite_id`."
            )
        return self


# --- 9. experiment.create ------------------------------------------------ #


class ExperimentCreate(_StrictBase, _ClientIdMixin):
    """``POST /v1/private/experiments`` — start a new experiment run."""

    test_suite_name: str | None = Field(default=None, max_length=200)
    test_suite_id: UUID | None = Field(default=None)
    name: str | None = Field(default=None, max_length=200)
    metadata: Metadata = Field(default=None)
    prompt_versions: list[dict[str, Any]] | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_test_suite_xor(self) -> ExperimentCreate:
        has_name = self.test_suite_name is not None
        has_id = self.test_suite_id is not None
        if has_name and has_id:
            raise ValueError(
                "test_suite_parent_conflict: pass either `test_suite_name` "
                "or `test_suite_id`, not both."
            )
        if not has_name and not has_id:
            raise ValueError(
                "test_suite_parent_missing: pass `test_suite_name` or `test_suite_id`."
            )
        return self


# --- 10. experiment_item.create ----------------------------------------- #


class ExperimentItem(BaseModel):
    """Single experiment-item row inside the always-array envelope."""

    model_config = ConfigDict(extra="allow")

    id: UUID | None = Field(default=None)
    experiment_id: UUID
    test_suite_item_id: UUID = Field(
        description=(
            "UUID of the test-suite item the trace ran against (wire field: dataset_item_id)."
        ),
    )
    trace_id: UUID


class ExperimentItemCreate(_StrictBase):
    """``POST /v1/private/experiments/items`` — array envelope only.

    The BE has no singleton route for this endpoint; the model rejects bare
    object payloads with a corrected example so the LLM can recover in one
    extra turn.
    """

    experiment_items: list[ExperimentItem] = Field(min_length=1, max_length=1000)


# --- examples (used by registry + validation errors) --------------------- #
#
# One validated example per operation. These are the source of truth for the
# ``example`` field on validation errors and on ``schema()`` responses; tests
# round-trip each example through its own model.


def _example_uuid(label: str) -> str:
    # Deterministic placeholder UUIDs so examples are easy to spot in
    # transcripts. Real callers always supply real UUIDs.
    base = "0193a300-0000-7000-8000-000000000000"
    return base[: -len(label)] + label


_NOW = "2026-05-18T12:00:00Z"


EXAMPLES: dict[str, dict[str, Any]] = {
    "trace.create": {
        "name": "openai.chat",
        "start_time": _NOW,
        "project_name": "demo",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
    },
    "trace.update": {
        "id": _example_uuid("01"),
        "end_time": _NOW,
        "output": {"text": "hello!"},
        "tags_to_add": ["regression"],
    },
    "span.create": {
        "trace_id": _example_uuid("01"),
        "name": "openai.chat",
        "type": "llm",
        "start_time": _NOW,
        "model": "gpt-4o",
        "provider": "openai",
    },
    "score.create": {
        "target": "trace",
        "target_id": _example_uuid("01"),
        "name": "helpfulness",
        "value": 0.8,
        "reason": "user-confirmed",
    },
    "comment.create": {
        "target": "span",
        "target_id": _example_uuid("01"),
        "text": "retry with temperature=0",
    },
    "prompt_version.save": {
        "name": "support_reply",
        "template": "Hi {{name}}, …",
        "commit": "v3",
        "change_description": "tighten greeting",
    },
    "test_suite.create": {
        "name": "eval_q3",
        "description": "Q3 regression set",
        "tags": ["regression"],
    },
    "test_suite_item.upsert": {
        "test_suite_name": "eval_q3",
        "items": [
            {"input": {"query": "what is opik?"}, "expected_output": {"text": "an LLM eval tool"}},
        ],
    },
    "experiment.create": {
        "test_suite_name": "eval_q3",
        "name": "gpt-4o-baseline",
        "metadata": {"git_sha": "abc123"},
    },
    "experiment_item.create": {
        "experiment_items": [
            {
                "experiment_id": _example_uuid("01"),
                "test_suite_item_id": _example_uuid("02"),
                "trace_id": _example_uuid("03"),
            }
        ]
    },
}


# Public registry alias — kept here so model module is the single source of
# truth for the type → model mapping; the operation registry imports it.
MODELS: dict[str, type[BaseModel]] = {
    "trace.create": TraceCreate,
    "trace.update": TraceUpdate,
    "span.create": SpanCreate,
    "score.create": ScoreCreate,
    "comment.create": CommentCreate,
    "prompt_version.save": PromptVersionSave,
    "test_suite.create": TestSuiteCreate,
    "test_suite_item.upsert": TestSuiteItemUpsert,
    "experiment.create": ExperimentCreate,
    "experiment_item.create": ExperimentItemCreate,
}


# Annotated re-exports (used by tests).
TagListT = Annotated[list[str], "list of tags"]


__all__ = [
    "EXAMPLES",
    "MODELS",
    "CommentCreate",
    "ExperimentCreate",
    "ExperimentItem",
    "ExperimentItemCreate",
    "PromptVersionSave",
    "ScoreCreate",
    "ScoreTarget",
    "SpanCreate",
    "SpanType",
    "TestSuiteCreate",
    "TestSuiteItem",
    "TestSuiteItemUpsert",
    "TraceCreate",
    "TraceUpdate",
]
