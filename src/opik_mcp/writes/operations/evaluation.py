"""Prompts, datasets and experiments.

What these have in common is a payload the wire does not take verbatim: a
prompt version nests under ``version``, a dataset's ``type`` is a backend
enum whose test-suite value is spelled differently, and a dataset item is
re-shaped into the backend's ``{source, data}`` envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opik_mcp.writes.models import (
    InputOutput,
    Metadata,
    TagList,
    _ClientIdMixin,
    _StrictBase,
    example_uuid,
)
from opik_mcp.writes.wire import BuildContext, WireRequest, dump

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


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


#
# One BE entity, two flavors: a plain dataset and a test suite (the UI's
# "evaluation suite"), told apart by ``type`` on ``POST /v1/private/datasets``.
# ``type`` is the caller's choice here and the builder always sends it, so a
# create never rides on the backend's default.


class DatasetCreate(_StrictBase, _ClientIdMixin):
    """``POST /v1/private/datasets`` — create a dataset or a test suite."""

    name: str = Field(min_length=1, max_length=200)
    type: Literal["dataset", "test_suite"] = Field(
        default="dataset",
        description=(
            "`dataset` for a plain dataset, `test_suite` for an evaluation suite "
            "(a dataset whose items carry assertions and run as tests)."
        ),
    )
    description: str | None = Field(default=None, max_length=10_000)
    tags: TagList | None = Field(default=None)
    metadata: Metadata = Field(default=None)


class DatasetItem(BaseModel):
    """Single item inside a ``dataset_item.upsert`` envelope.

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
    def _validate_no_data_conflict(self) -> DatasetItem:
        if self.data is None:
            return self
        for k in ("input", "expected_output", "metadata"):
            if getattr(self, k) is not None and k in self.data:
                raise ValueError(
                    f"data_field_conflict: {k!r} is set both at the top level "
                    f"and inside `data` — pick one form."
                )
        return self


class DatasetItemUpsert(_StrictBase):
    """``PUT /v1/private/datasets/items`` — always envelope form.

    Exactly one of ``dataset_name`` / ``dataset_id`` is required; spec §3.2.
    Distinguished error codes per failure mode so recovery tooling can react
    differently to "you passed neither" vs. "you passed both".
    """

    dataset_name: str | None = Field(default=None, max_length=200)
    dataset_id: UUID | None = Field(default=None)
    items: list[DatasetItem] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate_parent_xor(self) -> DatasetItemUpsert:
        has_name = self.dataset_name is not None
        has_id = self.dataset_id is not None
        if has_name and has_id:
            raise ValueError(
                "dataset_parent_conflict: pass either `dataset_name` or `dataset_id`, not both."
            )
        if not has_name and not has_id:
            raise ValueError("dataset_parent_missing: pass `dataset_name` or `dataset_id`.")
        return self


class ExperimentCreate(_StrictBase, _ClientIdMixin):
    """``POST /v1/private/experiments`` — start a new experiment run."""

    dataset_name: str | None = Field(default=None, max_length=200)
    dataset_id: UUID | None = Field(default=None)
    name: str | None = Field(default=None, max_length=200)
    metadata: Metadata = Field(default=None)
    prompt_versions: list[dict[str, Any]] | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_dataset_xor(self) -> ExperimentCreate:
        has_name = self.dataset_name is not None
        has_id = self.dataset_id is not None
        if has_name and has_id:
            raise ValueError(
                "dataset_parent_conflict: pass either `dataset_name` or `dataset_id`, not both."
            )
        if not has_name and not has_id:
            raise ValueError("dataset_parent_missing: pass `dataset_name` or `dataset_id`.")
        return self


class ExperimentItem(BaseModel):
    """Single experiment-item row inside the always-array envelope."""

    model_config = ConfigDict(extra="allow")

    id: UUID | None = Field(default=None)
    experiment_id: UUID
    dataset_item_id: UUID = Field(
        description="UUID of the dataset item the trace ran against.",
    )
    trace_id: UUID


class ExperimentItemCreate(_StrictBase):
    """``POST /v1/private/experiments/items`` — array envelope only.

    The BE has no singleton route for this endpoint; the model rejects bare
    object payloads with a corrected example so the LLM can recover in one
    extra turn.
    """

    experiment_items: list[ExperimentItem] = Field(min_length=1, max_length=1000)


PROMPT_VERSION_SAVE_EXAMPLE: Final[dict[str, Any]] = {
    "name": "support_reply",
    "template": "Hi {{name}}, …",
    "commit": "v3",
    "change_description": "tighten greeting",
}

DATASET_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "name": "eval_q3",
    "type": "test_suite",
    "description": "Q3 regression set",
    "tags": ["regression"],
}

DATASET_ITEM_UPSERT_EXAMPLE: Final[dict[str, Any]] = {
    "dataset_name": "eval_q3",
    "items": [
        {"input": {"query": "what is opik?"}, "expected_output": {"text": "an LLM eval tool"}},
    ],
}

EXPERIMENT_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "dataset_name": "eval_q3",
    "name": "gpt-4o-baseline",
    "metadata": {"git_sha": "abc123"},
}

EXPERIMENT_ITEM_CREATE_EXAMPLE: Final[dict[str, Any]] = {
    "experiment_items": [
        {
            "experiment_id": example_uuid("01"),
            "dataset_item_id": example_uuid("02"),
            "trace_id": example_uuid("03"),
        }
    ]
}


#: MCP ``dataset.create`` type → the backend's ``DatasetType`` value. The two
#: names agree except for the test suite, whose DB value is still the older
#: ``evaluation_suite`` (opik-backend's DatasetType carries a TODO, OPIK-5795,
#: to migrate it to ``test_suite``); when it moves, only this table changes.
DATASET_TYPE_TO_WIRE: Final[dict[str, str]] = {
    "dataset": "dataset",
    "test_suite": "evaluation_suite",
}


def build_prompt_version_save(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    d = dump(items[0])
    version_keys = ("template", "commit", "tags", "metadata")
    version = {k: d[k] for k in version_keys if k in d and d[k] is not None}
    body: dict[str, Any] = {"name": d["name"], "version": version}
    if d.get("change_description") is not None:
        body["change_description"] = d["change_description"]
    return WireRequest(op.endpoint, body)


def build_dataset_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """One endpoint, two flavors: /v1/private/datasets creates a plain dataset
    or a test suite, and ``type`` is the discriminator. Ours is spelled
    ``test_suite``; the backend's DatasetType still calls that value
    ``evaluation_suite``, so it is translated here. The type is always sent —
    before this, the operation named ``test_suite.create`` hard-coded
    ``evaluation_suite``, which left no way to create a plain dataset at all.
    """
    body = dump(items[0])
    body["type"] = DATASET_TYPE_TO_WIRE[body.get("type", "dataset")]
    return WireRequest(op.endpoint, body)


def build_dataset_item_upsert(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Single-envelope shape (``supports_batch=False`` enforces this in Stage
    2). Re-shape each item into the BE's
    ``{source, data: {input, expected_output, metadata}}`` envelope."""
    body = dump(items[0])
    for item in body.get("items", []):
        if not isinstance(item, dict):
            continue
        inner: dict[str, Any] = item.pop("data", None) or {}
        for k in ("input", "expected_output", "metadata"):
            if k in item:
                inner.setdefault(k, item.pop(k))
        if inner:
            item["data"] = inner
        item.setdefault("source", "sdk")
    return WireRequest(op.endpoint, body)


__all__ = [
    "build_dataset_create",
    "build_dataset_item_upsert",
    "build_prompt_version_save",
]
