"""Per-operation positive + negative model tests (spec §7.1).

One negative + one positive case per operation. Negatives assert the
issue code / field path; positives validate the spec's bundled example
through the dispatcher's Stage 2 so we catch any inconsistency between
the example and the model.
"""

from __future__ import annotations

import json

import pytest

from opik_mcp.writes.errors import ValidationFailedError, WriteError
from opik_mcp.writes.models import EXAMPLES
from opik_mcp.writes.registry import WRITE_OPERATIONS, WRITE_REGISTRY


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("operation", WRITE_OPERATIONS)
@pytest.mark.anyio
async def test_bundled_example_validates(operation: str) -> None:
    """Every operation's example must pass Stage 2 and reach Stage 3.

    We use the dispatcher's ``dry_run=True`` path with an empty scope set
    so authorization fails AFTER validation — proves the example cleared
    the model.
    """
    from opik_mcp.writes.dispatch import run_write

    try:
        await run_write(
            operation=operation,
            data=EXAMPLES[operation],
            dry_run=True,
            scopes=frozenset(),
        )
        pytest.fail("expected AuthorizationDenied (Stage 3) — the example should clear Stage 2")
    except WriteError as we:
        assert we.error == "authorization_denied", (
            f"{operation}: expected authorization_denied at Stage 3 with empty scopes; "
            f"got {we.error!r} — example failed Stage 2 validation: {we.to_json()}"
        )


# --- per-operation negative cases (spec §7.1) ---------------------------- #


@pytest.mark.anyio
async def test_trace_create_missing_start_time() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(operation="trace.create", data={"name": "t"})
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "start_time" in fields
    assert body["expected_schema"]["type"] == "object"
    assert "example" in body


@pytest.mark.anyio
async def test_span_create_missing_trace_id() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="span.create",
            data={"name": "s", "start_time": "2026-05-18T12:00:00Z"},
        )
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "trace_id" in fields
    assert "trace_id" in body["expected_schema"]["required"]


@pytest.mark.anyio
async def test_score_thread_single_form_returns_array_example() -> None:
    """target='thread' with single-item shape rejects with the corrected array example."""
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="score.create",
            data={
                "target": "thread",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "name": "helpfulness",
                "value": 0.5,
            },
        )
    body = json.loads(exc_info.value.to_json())
    assert any("thread_requires_batch" in i.get("code", "") for i in body["issues"])
    assert isinstance(body["example"], list), "thread error must propose the array form"


@pytest.mark.anyio
async def test_score_value_not_a_number_fails_stage2() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="score.create",
            data={
                "target": "trace",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "name": "h",
                "value": "not-a-number",
            },
        )
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "value" in fields


@pytest.mark.anyio
async def test_comment_span_missing_target_id() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="comment.create",
            data={"target": "span", "text": "note"},
        )
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "target_id" in fields


@pytest.mark.anyio
async def test_prompt_version_save_missing_template() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(operation="prompt_version.save", data={"name": "p"})
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "template" in fields


@pytest.mark.anyio
async def test_test_suite_create_missing_name() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(operation="test_suite.create", data={})
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "name" in fields


@pytest.mark.anyio
async def test_test_suite_item_upsert_both_parent_conflict() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="test_suite_item.upsert",
            data={
                "test_suite_name": "x",
                "test_suite_id": "00000000-0000-0000-0000-000000000001",
                "items": [{"input": {"q": "a"}}],
            },
        )
    body = json.loads(exc_info.value.to_json())
    assert any("test_suite_parent_conflict" in i.get("code", "") for i in body["issues"])


@pytest.mark.anyio
async def test_test_suite_item_upsert_rejects_data_field_conflict() -> None:
    """Same key on both ``data: {…}`` and the top level fails Stage 2 loudly.

    Earlier versions silently kept the ``data`` value and dropped the top-
    level one via ``setdefault`` + ``item.pop``. The model_validator now
    raises ``data_field_conflict`` so neither is lost in a black box.
    """
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="test_suite_item.upsert",
            data={
                "test_suite_name": "x",
                "items": [
                    {
                        "data": {"input": {"q": "in-data"}},
                        "input": {"q": "top-level"},
                    }
                ],
            },
        )
    body = json.loads(exc_info.value.to_json())
    assert any("data_field_conflict" in i.get("code", "") for i in body["issues"])


@pytest.mark.anyio
async def test_experiment_create_missing_test_suite_parent() -> None:
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(operation="experiment.create", data={"name": "exp"})
    body = json.loads(exc_info.value.to_json())
    assert any("test_suite_parent_missing" in i.get("code", "") for i in body["issues"])


@pytest.mark.anyio
async def test_experiment_item_create_bare_object_returns_envelope_example() -> None:
    """Bare object → validation error with the {experiment_items: [...]} corrected example."""
    from opik_mcp.writes.dispatch import run_write

    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="experiment_item.create",
            data={
                "experiment_id": "00000000-0000-0000-0000-000000000001",
                "test_suite_item_id": "00000000-0000-0000-0000-000000000002",
                "trace_id": "00000000-0000-0000-0000-000000000003",
            },
        )
    body = json.loads(exc_info.value.to_json())
    fields = {i["field"] for i in body["issues"]}
    assert "experiment_items" in fields
    assert "experiment_items" in body["example"]


# --- unknown operation rejected at Stage 1 ------------------------------- #


@pytest.mark.anyio
async def test_unknown_operation_lists_valid_set() -> None:
    from opik_mcp.writes.dispatch import run_write
    from opik_mcp.writes.errors import UnknownOperationError

    with pytest.raises(UnknownOperationError) as exc_info:
        await run_write(operation="trace.delete", data={})
    body = json.loads(exc_info.value.to_json())
    assert body["error"] == "unknown_operation"
    assert set(body["valid_operations"]) == set(WRITE_REGISTRY.keys())
