from uuid import UUID

import pytest
from pydantic import ValidationError

from opik_mcp.run_experiment_models import PromptVariant, RunExperimentConfig


def test_minimal_valid_config() -> None:
    cfg = RunExperimentConfig(
        dataset_name="my-suite",
        dataset_id=UUID("0193a300-0000-7000-8000-000000000123"),
        prompts=[
            PromptVariant(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hi"}],
            )
        ],
    )
    assert cfg.prompts[0].configs == {}
    assert cfg.dataset_version_id is None
    body = cfg.to_wire_body()
    assert body["dataset_id"] == "0193a300-0000-7000-8000-000000000123"
    assert body["prompts"][0]["messages"][0]["role"] == "user"
    # Optional fields omitted, not None:
    assert "dataset_version_id" not in body
    assert "project_name" not in body


def test_prompts_required_non_empty() -> None:
    with pytest.raises(ValidationError) as ei:
        RunExperimentConfig(
            dataset_name="s",
            dataset_id=UUID("0193a300-0000-7000-8000-000000000123"),
            prompts=[],
        )
    assert "prompts" in str(ei.value)


def test_prompt_requires_model_and_messages() -> None:
    with pytest.raises(ValidationError):
        PromptVariant(model="", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ValidationError):
        PromptVariant(model="gpt-4o", messages=[])


def test_dataset_id_must_be_uuid() -> None:
    with pytest.raises(ValidationError):
        RunExperimentConfig(
            dataset_name="s",
            dataset_id="not-a-uuid",  # type: ignore[arg-type]
            prompts=[PromptVariant(model="gpt-4o", messages=[{"role": "u", "content": "x"}])],
        )


def test_prompt_versions_wire_shape() -> None:
    cfg = RunExperimentConfig(
        dataset_name="s",
        dataset_id=UUID("0193a300-0000-7000-8000-000000000123"),
        prompts=[
            PromptVariant(
                model="gpt-4o",
                messages=[{"role": "user", "content": "x"}],
                prompt_version_id=UUID("0193a300-0000-7000-8000-0000000000aa"),
            )
        ],
    )
    body = cfg.to_wire_body()
    assert body["prompts"][0]["prompt_versions"] == [
        {"id": "0193a300-0000-7000-8000-0000000000aa"}
    ]
