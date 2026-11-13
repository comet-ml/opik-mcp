"""The automation writes over both transports: a recipe on and off, an
automation's lifecycle, a shadow test, a dismissal, and the three backend
answers the hooks reinterpret.

Their hooks are ``src/opik_mcp/writes/operations/automations.py``.
"""

from __future__ import annotations

import pytest

from tests.hermetic.servers import issues, result_json
from tests.hermetic.stub_records import (
    AUTOMATION_ID,
    PROJECT_ID,
    PROJECT_NAME,
    RECOMMENDATION_ID,
    SUITE_NAME,
    TRACE_ID,
)
from tests.hermetic.writes.surface import (
    Sent,
    Wire,
    WriteCase,
    assert_refused_before_anything_is_sent,
    assert_the_write_lands_and_links,
    operations_of,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

_PREFIX = "/v1/private/automations"
_TRIGGER = {
    "event": "feedback_score:created",
    "scope": "trace",
    "conditions": {
        "groups": [{"conditions": [{"score_name": "resolution", "operator": "<", "value": 0.5}]}]
    },
}
_STEP = {"id": "keep", "type": "test_suite.add", "test_suite_name": SUITE_NAME}

CASES: tuple[WriteCase, ...] = (
    WriteCase(
        case_id="workflow.enable",
        operation="workflow.enable",
        data={"recipe": "collect", "project_name": PROJECT_NAME, "overrides": {"threshold": 0.4}},
        sent=(
            Sent(
                "POST",
                f"{_PREFIX}/workflows/enable",
                {
                    "recipe": "collect",
                    "overrides": {"threshold": 0.4},
                    "project_id": PROJECT_ID,
                    "created_via": "mcp",
                },
            ),
        ),
        url_suffix=None,
        extra_keys=frozenset({"note"}),
    ),
    WriteCase(
        case_id="workflow.disable",
        operation="workflow.disable",
        data={"recipe": "collect", "project_id": PROJECT_ID},
        sent=(
            Sent(
                "POST",
                f"{_PREFIX}/workflows/disable",
                {"recipe": "collect", "project_id": PROJECT_ID},
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="automation.create",
        operation="automation.create",
        data={
            "project_name": PROJECT_NAME,
            "name": "Low-resolution traces to the suite",
            "triggers": [_TRIGGER],
            "steps": [_STEP],
            "limits": {"max_items_per_day": 200},
        },
        sent=(
            Sent(
                "POST",
                _PREFIX,
                {
                    "name": "Low-resolution traces to the suite",
                    "triggers": [{**_TRIGGER}],
                    "steps": [_STEP],
                    "limits": {"max_items_per_day": 200},
                    "enabled": True,
                    "project_id": PROJECT_ID,
                    "created_via": "mcp",
                },
            ),
        ),
        url_suffix=None,
        extra_keys=frozenset({"note"}),
    ),
    WriteCase(
        case_id="automation.update",
        operation="automation.update",
        data={"automation_id": AUTOMATION_ID, "limits": {"max_items_per_day": 100}},
        sent=(Sent("PATCH", f"{_PREFIX}/{AUTOMATION_ID}", {"limits": {"max_items_per_day": 100}}),),
        url_suffix=None,
    ),
    WriteCase(
        case_id="automation.enable",
        operation="automation.enable",
        data={"automation_id": AUTOMATION_ID},
        sent=(Sent("PATCH", f"{_PREFIX}/{AUTOMATION_ID}", {"enabled": True}),),
        url_suffix=None,
    ),
    WriteCase(
        case_id="automation.disable",
        operation="automation.disable",
        data={"automation_id": AUTOMATION_ID},
        sent=(Sent("PATCH", f"{_PREFIX}/{AUTOMATION_ID}", {"enabled": False}),),
        url_suffix=None,
    ),
    WriteCase(
        case_id="automation.delete",
        operation="automation.delete",
        data={"automation_id": AUTOMATION_ID},
        sent=(Sent("POST", f"{_PREFIX}/delete", {"ids": [AUTOMATION_ID]}),),
        url_suffix=None,
    ),
    WriteCase(
        case_id="automation.test",
        operation="automation.test",
        data={"automation_id": AUTOMATION_ID, "trace_id": TRACE_ID},
        sent=(Sent("POST", f"{_PREFIX}/{AUTOMATION_ID}/test", {"trace_id": TRACE_ID}),),
        url_suffix=None,
    ),
    WriteCase(
        case_id="recommendation.dismiss",
        operation="recommendation.dismiss",
        data={"recommendation_id": RECOMMENDATION_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "POST",
                f"{_PREFIX}/recommendations/{RECOMMENDATION_ID}/dismiss",
                {"project_id": PROJECT_ID},
            ),
        ),
        url_suffix=None,
    ),
)


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_a_write_sends_its_request_and_answers_with_where_to_look(
    wire: Wire, case: WriteCase
) -> None:
    await assert_the_write_lands_and_links(wire, case)


@pytest.mark.parametrize("operation", operations_of(CASES))
async def test_a_payload_the_operation_does_not_accept_is_refused_before_anything_is_sent(
    wire: Wire, operation: str
) -> None:
    await assert_refused_before_anything_is_sent(wire, operation)


async def test_a_dry_run_create_asks_the_backend_and_shows_its_estimate(wire: Wire) -> None:
    """The backend validates and estimates; the preview carries both and the
    request it was shown is the real one, project resolved."""
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {
                "operation": "automation.create",
                "data": CASES[2].data,
                "dry_run": True,
            },
        )
    assert not result.isError, result_json(result)
    preview = result_json(result)
    assert preview["dry_run"] is True
    assert preview["estimate"] == {
        "window_days": 14,
        "fired": 240,
        "matched": 62,
        "items": 62,
        "cost_usd": 0,
    }
    automation = preview["automation"]
    assert isinstance(automation, dict)
    assert automation["name"] == "Low-resolution traces to the suite"
    (sent,) = [r for r in wire.backend.requests if r.path == _PREFIX and r.method == "POST"]
    assert sent.query.get("dry_run") == ["true"]
    assert sent.payload["project_id"] == PROJECT_ID


async def test_a_dry_run_recipe_enable_sends_the_flag_as_a_query_parameter(wire: Wire) -> None:
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {
                "operation": "workflow.enable",
                "data": {"recipe": "collect", "project_name": PROJECT_NAME},
                "dry_run": True,
            },
        )
    assert not result.isError, result_json(result)
    preview = result_json(result)
    would_create = preview["would_create"]
    assert isinstance(would_create, list)
    assert would_create[0]["name"] == "Low-resolution traces to the suite"
    assert "estimate" in preview
    (sent,) = wire.backend.sent(f"{_PREFIX}/workflows/enable")
    # The backend reads the flag from the query string only; in the body it is ignored
    # and the recipe is enabled for real.
    assert sent.query.get("dry_run") == ["true"]
    assert "dry_run" not in sent.payload


async def test_a_definition_the_backend_refuses_comes_back_as_its_code(wire: Wire) -> None:
    wire.backend.automation_refusal = "target_not_found"
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "automation.create", "data": CASES[2].data}
        )
    assert result.isError
    envelope = result_json(result)
    assert envelope["error"] == "validation_failed"
    (issue,) = issues(envelope)
    assert issue["code"] == "target_not_found"
    assert issue["field"] == "steps"
    message = str(envelope["message"])
    assert "create it or name an existing one" in message
    assert "Backend said:" in message
    assert "/v1/private" not in message


async def test_a_backend_without_automations_says_so_on_a_write(wire: Wire) -> None:
    wire.backend.has_automations = False
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {
                "operation": "workflow.enable",
                "data": {"recipe": "collect", "project_id": PROJECT_ID},
            },
        )
    assert result.isError
    envelope = result_json(result)
    (issue,) = issues(envelope)
    assert issue["code"] == "automations_unavailable"
    assert "no automations yet" in str(envelope["message"])
    assert "/v1/private" not in str(envelope)


async def test_a_shadow_test_returns_each_steps_outcome(wire: Wire) -> None:
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {
                "operation": "automation.test",
                "data": {"automation_id": AUTOMATION_ID, "trace_id": TRACE_ID},
            },
        )
    assert not result.isError, result_json(result)
    body = result_json(result)["backend_body"]
    assert isinstance(body, dict)
    assert body["kind"] == "single"
    assert body["steps"][0]["status"] == "done"


async def test_an_update_that_changes_nothing_is_refused_unsent(wire: Wire) -> None:
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "automation.update", "data": {"automation_id": AUTOMATION_ID}}
        )
    assert result.isError
    (issue,) = issues(result_json(result))
    assert issue["code"] == "nothing_to_update"
    assert wire.backend.writes() == []
