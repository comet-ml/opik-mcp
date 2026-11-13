"""The automation hooks against a mocked backend: what a dry run asks, what a
coded refusal becomes, what a 404 means on each kind of route, and the id a
create hands back."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from opik_mcp.client.opik import OpikClient
from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import AuthorizationDeniedError, BackendError, ValidationFailedError
from opik_mcp.writes.scopes import SCOPE_AUTOMATION_MANAGE, SCOPE_PROJECT_DATA_VIEW

OPIK_BASE = "https://opik.test"
PROJECT = "0193a300-0000-7000-8000-00000000aa01"
AUTOMATION = "0193a300-0000-7000-8000-00000000aa02"
_PREFIX = "/v1/private/automations"
_CREATE = {
    "project_id": PROJECT,
    "name": "Low scores to the suite",
    "triggers": [{"event": "feedback_score:created"}],
    "steps": [{"id": "keep", "type": "test_suite.add", "test_suite_name": "regressions"}],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


@pytest.mark.anyio
async def test_a_dry_run_create_sends_the_flag_and_merges_the_estimate() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(_PREFIX, params={"dry_run": "true"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "automation": {"name": "Low scores to the suite"},
                    "estimate": {"window_days": 14, "fired": 11, "items": 4, "cost_usd": 0},
                    "warnings": ["cycle_warning: …"],
                },
            )
        )
        out = await run_write(
            operation="automation.create", data=_CREATE, dry_run=True, client=_client()
        )
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["project_id"] == PROJECT
    assert body["created_via"] == "mcp"
    assert out["dry_run"] is True
    assert out["would_call"]["path"] == _PREFIX
    assert out["automation"] == {"name": "Low scores to the suite"}
    assert out["estimate"]["fired"] == 11
    assert out["warnings"] == ["cycle_warning: …"]
    assert "setup" not in out


@pytest.mark.anyio
async def test_a_dry_run_update_patches_with_the_flag() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.patch(f"{_PREFIX}/{AUTOMATION}", params={"dry_run": "true"}).mock(
            return_value=httpx.Response(200, json={"automation": {}, "estimate": {"fired": 0}})
        )
        out = await run_write(
            operation="automation.update",
            data={"automation_id": AUTOMATION, "enabled": False},
            dry_run=True,
            client=_client(),
        )
    assert route.called
    assert json.loads(route.calls.last.request.content) == {"enabled": False}
    assert out["estimate"] == {"fired": 0}


@pytest.mark.anyio
async def test_a_dry_run_recipe_enable_resolves_the_project_first() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(
                200, json={"content": [{"id": PROJECT, "name": "demo"}], "total": 1}
            )
        )
        route = mock.post(f"{_PREFIX}/workflows/enable").mock(
            return_value=httpx.Response(
                200,
                json={
                    "created": [{"name": "Test new versions"}],
                    "estimate": {"fired_14d": 11},
                    "setup": {"github_action": "- uses: comet-ml/opik-ci@v1"},
                },
            )
        )
        out = await run_write(
            operation="workflow.enable",
            data={"recipe": "gate", "project_name": "demo"},
            dry_run=True,
            client=_client(),
        )
    body = json.loads(route.calls.last.request.content)
    assert body == {"recipe": "gate", "project_id": PROJECT, "created_via": "mcp", "dry_run": True}
    assert out["would_create"] == [{"name": "Test new versions"}]
    assert out["setup"] == {"github_action": "- uses: comet-ml/opik-ci@v1"}
    assert "note" not in out["would_call"], "the project was resolved, so nothing is left to say"


@pytest.mark.anyio
async def test_a_plain_dry_run_still_says_the_name_resolves_at_execution() -> None:
    out = await run_write(
        operation="workflow.disable",
        data={"recipe": "collect", "project_name": "demo"},
        dry_run=True,
        client=_client(),
    )
    assert out["would_call"]["body"]["project_id"] == "{project_id}"
    assert "resolved" in out["would_call"]["note"]


@pytest.mark.anyio
async def test_a_coded_400_without_a_code_key_is_read_off_the_error_prefix() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(_PREFIX).mock(
            return_value=httpx.Response(
                400, json={"errors": ["limit_required: step 'score' costs money"]}
            )
        )
        with pytest.raises(ValidationFailedError) as refused:
            await run_write(operation="automation.create", data=_CREATE, client=_client())
    envelope = json.loads(refused.value.to_json())
    assert envelope["issues"] == [{"field": "limits", "code": "limit_required"}]
    assert "max_cost_usd_per_day" in envelope["message"]
    assert "Backend said: \"limit_required: step 'score' costs money\"" in envelope["message"]


@pytest.mark.anyio
async def test_a_400_without_a_definition_code_stays_a_backend_error() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(_PREFIX).mock(
            return_value=httpx.Response(400, json={"errors": ["name must be unique"]})
        )
        with pytest.raises(BackendError) as failed:
            await run_write(operation="automation.create", data=_CREATE, client=_client())
    assert failed.value.extra["backend_message"] == "name must be unique"


@pytest.mark.anyio
async def test_a_404_on_an_id_route_keeps_its_meaning() -> None:
    """Only a collection route's 404 means the feature is missing; an id
    route's 404 is the id."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.patch(f"{_PREFIX}/{AUTOMATION}").mock(return_value=httpx.Response(404))
        with pytest.raises(BackendError) as failed:
            await run_write(
                operation="automation.enable",
                data={"automation_id": AUTOMATION},
                client=_client(),
            )
    assert failed.value.extra["backend_error"] == {"status": 404}
    assert "check its ids" in failed.value.message


@pytest.mark.anyio
async def test_a_404_on_a_collection_route_names_the_missing_feature() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/delete").mock(return_value=httpx.Response(404))
        with pytest.raises(ValidationFailedError) as refused:
            await run_write(
                operation="automation.delete",
                data={"automation_id": AUTOMATION},
                client=_client(),
            )
    envelope = json.loads(refused.value.to_json())
    assert envelope["issues"][0]["code"] == "automations_unavailable"


@pytest.mark.anyio
async def test_a_create_hands_back_the_id_from_location() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        location = f"{OPIK_BASE}{_PREFIX}/{AUTOMATION}"
        mock.post(_PREFIX).mock(return_value=httpx.Response(201, headers={"Location": location}))
        out = await run_write(operation="automation.create", data=_CREATE, client=_client())
    assert out["backend_body"] == {"id": AUTOMATION}
    assert "read('automation', id)" in out["note"]


@pytest.mark.anyio
async def test_the_subject_of_a_shadow_test_is_one_trace_or_one_thread() -> None:
    with pytest.raises(ValidationFailedError) as neither:
        await run_write(
            operation="automation.test", data={"automation_id": AUTOMATION}, client=_client()
        )
    assert json.loads(neither.value.to_json())["issues"][0]["code"] == "test_subject_missing"
    with pytest.raises(ValidationFailedError) as both:
        await run_write(
            operation="automation.test",
            data={"automation_id": AUTOMATION, "trace_id": AUTOMATION, "thread_id": "conv-1"},
            client=_client(),
        )
    assert json.loads(both.value.to_json())["issues"][0]["code"] == "test_subject_conflict"


@pytest.mark.anyio
async def test_a_create_without_a_project_is_refused_with_the_scope_code() -> None:
    with pytest.raises(ValidationFailedError) as refused:
        await run_write(
            operation="automation.create",
            data={k: v for k, v in _CREATE.items() if k != "project_id"},
            client=_client(),
        )
    codes = {issue["code"] for issue in json.loads(refused.value.to_json())["issues"]}
    assert "project_scope_missing" in codes


@pytest.mark.anyio
async def test_a_typo_inside_a_step_is_refused() -> None:
    data = {**_CREATE, "steps": [{"id": "keep", "type": "test_suite.add", "per_dya": 3}]}
    with pytest.raises(ValidationFailedError) as refused:
        await run_write(operation="automation.create", data=data, client=_client())
    fields = {issue["field"] for issue in json.loads(refused.value.to_json())["issues"]}
    assert "steps.0.per_dya" in fields


@pytest.mark.anyio
async def test_managing_needs_the_automation_scope_and_testing_only_the_view_scope() -> None:
    with pytest.raises(AuthorizationDeniedError) as denied:
        await run_write(
            operation="automation.create",
            data=_CREATE,
            scopes=frozenset({SCOPE_PROJECT_DATA_VIEW}),
            client=_client(),
        )
    assert denied.value.extra["required_scope"] == SCOPE_AUTOMATION_MANAGE
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/{AUTOMATION}/test").mock(
            return_value=httpx.Response(200, json={"kind": "single", "steps": []})
        )
        out = await run_write(
            operation="automation.test",
            data={"automation_id": AUTOMATION, "thread_id": "conv-1"},
            scopes=frozenset({SCOPE_PROJECT_DATA_VIEW}),
            client=_client(),
        )
    assert out["ok"] is True
