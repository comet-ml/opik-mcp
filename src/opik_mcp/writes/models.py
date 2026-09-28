"""What the write operation models share: field types, mixins, example helpers.

Each operation has one model, in its module under ``writes/operations/``,
that validates a single-item payload. Batch form is handled at the
dispatcher level by validating an array against the same model
element-by-element; the model definitions themselves do not see the batch
envelope.

Cross-cutting rules (spec §3.3) live on small mixins so a regression in
"tags xor tags_to_add" rejection on, say, ``trace.create`` cannot silently
ship — every model with tags inherits the same validator.

The models are deliberately permissive about fields the BE accepts but the
LLM rarely needs (``input``/``output`` are ``dict``-or-``list``, metadata
is ``dict[str, Any]``, etc.) so that valid BE payloads from the SDKs round
trip through the MCP tool without losing fidelity.
"""

from __future__ import annotations

from typing import Any, ClassVar
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


class _RequiredProjectMixin(BaseModel):
    """Project scope that must be present, for BE routes that reject a request
    without it.

    Three operations need this and each one names a different recovery code,
    because the field the LLM has to add is the same but the reason differs (a
    thread's project, a Diagnostics job's project, an issue's project). So the
    fields and the check live here once and the subclass supplies the message.
    ``_ProjectMixin`` below is the opposite case: optional, xor-checked.
    """

    project_name: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = Field(default=None)

    #: ``"<code>: <what to do>"``, raised verbatim when neither field is set.
    _missing_project_error: ClassVar[str] = (
        "project_scope_missing: pass `project_id` or `project_name`."
    )

    @model_validator(mode="after")
    def _require_project(self) -> _RequiredProjectMixin:
        if self.project_name is None and self.project_id is None:
            raise ValueError(self._missing_project_error)
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


# --- examples ------------------------------------------------------------- #
#
# Each operation module holds one validated example per operation, the source
# of truth for the ``example`` field on validation errors and on ``schema()``
# responses; tests round-trip each example through its own model.


def example_uuid(label: str) -> str:
    # Deterministic placeholder UUIDs so examples are easy to spot in
    # transcripts. Real callers always supply real UUIDs.
    base = "0193a300-0000-7000-8000-000000000000"
    return base[: -len(label)] + label


EXAMPLE_TIME = "2026-05-18T12:00:00Z"
