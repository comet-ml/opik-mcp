"""Cross-cutting data-rule tests — spec §7.1 'Cross-cutting validators'.

Exercises the shared mixins on every operation that wears them. A new
operation that picks up the ``_TagsMixin`` automatically inherits the
``combined_tag_modes`` rejection — these tests prove that.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel, ValidationError

from opik_mcp.writes.dispatch import _resolve_idempotency_key
from opik_mcp.writes.models import (
    PromptVersionSave,
    SpanCreate,
    TraceCreate,
    TraceUpdate,
)

# --- tags: replace-vs-patch xor ----------------------------------------- #


@pytest.mark.parametrize(
    "model_cls",
    [TraceCreate, TraceUpdate, SpanCreate],
)
def test_tags_combined_with_patch_rejects(model_cls: type[BaseModel]) -> None:
    """Setting both `tags` and `tags_to_add` is a validation error on every
    model that exposes the patch fields."""
    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(_minimal_for(model_cls) | {"tags": ["a"], "tags_to_add": ["b"]})
    assert "combined_tag_modes" in str(exc_info.value)


@pytest.mark.parametrize(
    "model_cls",
    [TraceCreate, TraceUpdate, SpanCreate],
)
def test_patch_fields_can_combine(model_cls: type[BaseModel]) -> None:
    """`tags_to_add` + `tags_to_remove` together is fine — only mixing replace+patch
    is the violation."""
    model_cls.model_validate(
        _minimal_for(model_cls) | {"tags_to_add": ["new"], "tags_to_remove": ["old"]}
    )


def test_promptversion_does_not_share_tags_patch_rule() -> None:
    """`prompt_version.save` only carries the replace `tags` field — no patch — so the
    combined-mode check should NOT trigger. Catches accidental mixin inheritance.

    The model itself doesn't expose `tags_to_add`, so passing it fails for a
    different reason (`extra='forbid'`) — that's the assertion."""
    with pytest.raises(ValidationError) as exc_info:
        PromptVersionSave.model_validate({"name": "p", "template": "t", "tags_to_add": ["x"]})
    assert "extra_forbidden" in str(exc_info.value) or "Extra inputs" in str(exc_info.value)


# --- project xor -------------------------------------------------------- #


def test_project_name_and_project_id_both_set_rejects() -> None:
    """TraceCreate has the project mixin — setting both is an xor violation."""
    with pytest.raises(ValidationError) as exc_info:
        TraceCreate.model_validate(
            {
                "name": "t",
                "start_time": "2026-05-18T12:00:00Z",
                "project_name": "demo",
                "project_id": "00000000-0000-0000-0000-000000000001",
            }
        )
    assert "project_xor" in str(exc_info.value)


def test_project_neither_set_is_fine() -> None:
    TraceCreate.model_validate({"name": "t", "start_time": "2026-05-18T12:00:00Z"})


# --- idempotency-key precedence ---------------------------------------- #


def test_idempotency_key_overrides_item_id(caplog: pytest.LogCaptureFixture) -> None:
    """When tool-level and item-level differ, tool-level wins and a warning fires."""
    item = TraceCreate.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "t",
            "start_time": "2026-05-18T12:00:00Z",
        }
    )
    with caplog.at_level(logging.WARNING, logger="opik_mcp.writes.dispatch"):
        resolved = _resolve_idempotency_key("tool-key", [item])
    assert resolved == "tool-key"
    assert any("idempotency_conflict" in rec.message for rec in caplog.records)


def test_idempotency_key_passthrough_without_conflict() -> None:
    item = TraceCreate.model_validate({"name": "t", "start_time": "2026-05-18T12:00:00Z"})
    assert _resolve_idempotency_key("only-tool-key", [item]) == "only-tool-key"
    assert _resolve_idempotency_key(None, [item]) is None


# --- helpers ------------------------------------------------------------ #


def _minimal_for(model_cls: type[BaseModel]) -> dict[str, object]:
    """Smallest valid kwargs for a model — fills only the required fields."""
    if model_cls is TraceCreate:
        return {"name": "t", "start_time": "2026-05-18T12:00:00Z"}
    if model_cls is TraceUpdate:
        return {"id": "00000000-0000-0000-0000-000000000001"}
    if model_cls is SpanCreate:
        return {
            "trace_id": "00000000-0000-0000-0000-000000000001",
            "name": "s",
            "start_time": "2026-05-18T12:00:00Z",
        }
    raise ValueError(f"unhandled model {model_cls!r}")
