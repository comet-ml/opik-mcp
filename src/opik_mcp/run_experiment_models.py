"""Input + output Pydantic models for the `run_experiment` MCP tool.

Wire format mirrors the FE `ExperimentExecutionRequest` body
(see opik-frontend/src/api/playground/useRunExperimentExecution.ts and
opik-backend/.../ExperimentExecutionRequest.java).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PromptVariant(_StrictBase):
    """One prompt configuration to run against the dataset.

    The /execute endpoint accepts a list of these; opik-backend creates one
    experiment record per variant. `prompt_version_id` (optional) ties the
    experiment to a saved prompt version for lineage; if omitted, the prompt
    is treated as inline.
    """

    model: str = Field(min_length=1, max_length=200)
    messages: list[dict[str, Any]] = Field(min_length=1)
    configs: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific knobs: temperature, max_tokens, etc.",
    )
    prompt_version_id: UUID | None = Field(
        default=None,
        description="UUID of a saved prompt version in the Opik prompt library.",
    )

    @field_validator("messages")
    @classmethod
    def _every_message_has_content(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, m in enumerate(v):
            if not m.get("role"):
                raise ValueError(f"messages[{i}].role is required")
            if "content" not in m:
                raise ValueError(f"messages[{i}].content is required")
        return v

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "configs": self.configs,
        }
        if self.prompt_version_id is not None:
            body["prompt_versions"] = [{"id": str(self.prompt_version_id)}]
        return body


class RunExperimentConfig(_StrictBase):
    """`POST /v1/private/experiments/execute` request body.

    `dataset_id` must reference a dataset whose evaluation_method is
    `evaluation_suite` — only test-suite-backed datasets are supported by
    this tool in v1.
    """

    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_id: UUID
    prompts: list[PromptVariant] = Field(min_length=1, max_length=10)
    dataset_version_id: UUID | None = None
    version_hash: str | None = Field(default=None, max_length=200)
    project_name: str | None = Field(default=None, max_length=200)

    def to_wire_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "dataset_name": self.dataset_name,
            "dataset_id": str(self.dataset_id),
            "prompts": [p.to_wire() for p in self.prompts],
        }
        if self.dataset_version_id is not None:
            body["dataset_version_id"] = str(self.dataset_version_id)
        if self.version_hash is not None:
            body["version_hash"] = self.version_hash
        if self.project_name is not None:
            body["project_name"] = self.project_name
        return body


class ExperimentHandle(_StrictBase):
    """One created experiment + its index in the `prompts` array."""

    experiment_id: UUID
    prompt_index: int = Field(ge=0)


class RunExperimentResult(BaseModel):
    """Tool result returned the moment opik-backend accepts the request.

    Status is intentionally NOT included — the experiment is async; the
    caller checks progress by reading the experiment record later.
    """

    experiment_ids: list[str]
    prompt_indexes: list[int]
    total_items: int
    summary_url: str
