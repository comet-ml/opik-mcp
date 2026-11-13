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
_GATE_CREATE = {
    "project_id": PROJECT,
    "name": "gate",
    "triggers": [
        {"event": "ci:run", "repository": "example-org/checkout-agent", "branch": "main"},
        {"event": "experiment:finished", "test_suite_name": "regressions"},
    ],
    "steps": [
        {
            "id": "run",
            "type": "test_suite.run",
            "test_suite_name": "regressions",
            "model": "gpt-4o-mini",
            "prompt_name": "refund-answer",
        },
        {"id": "check", "type": "github.check", "context": "opik/gate"},
        {
            "id": "notify",
            "type": "notify",
            "url": "https://example.test/hook",
            "kind": "webhook",
            "message": "regressions",
            "only_if": {
                "groups": [
                    {
                        "conditions": [
                            {"step_output": "run.regressions", "operator": ">", "value": 0}
                        ]
                    }
                ]
            },
        },
    ],
    "limits": {"max_cost_usd_per_day": 5},
}
_GAIN = {
    "groups": [{"conditions": [{"step_output": "optimize.gain", "operator": ">=", "value": 0.05}]}]
}
_IMPROVE_CREATE = {
    "project_id": PROJECT,
    "name": "improve",
    "triggers": [{"event": "test_suite:grew", "test_suite_name": "regressions", "n": 50}],
    "steps": [
        {
            "id": "optimize",
            "type": "optimizer.run",
            "prompt_name": "refund-answer",
            "model": "gpt-4o-mini",
            "test_suite_name": "regressions",
            "metric": "levenshtein_ratio",
            "optimizer": "metaprompt",
        },
        {
            "id": "save",
            "type": "prompt_version.save",
            "prompt_name": "refund-answer",
            "only_if": _GAIN,
        },
        {
            "id": "notify",
            "type": "notify",
            "url": "https://example.test/hook",
            "kind": "webhook",
            "only_if": _GAIN,
        },
    ],
    "limits": {"max_cost_usd_per_day": 10},
}
_ALIGN_CHECK_CREATE = {
    "project_id": PROJECT,
    "name": "check the judge",
    "triggers": [
        {"event": "annotation_queue:submitted", "annotation_queue_name": "review", "n": 20}
    ],
    "steps": [
        {
            "id": "agreement",
            "type": "judge.agreement",
            "score_name": "resolution",
            "annotation_queue_name": "review",
            "threshold": 0.5,
        },
        {
            "id": "revise",
            "type": "judge.revise",
            "url": "https://hooks.slack.com/services/T000/B000/XXXX",
            "kind": "slack",
            "score_name": "resolution",
            "only_if": {
                "groups": [
                    {
                        "conditions": [
                            {"step_output": "agreement.agreement", "operator": "<", "value": 0.8}
                        ]
                    }
                ]
            },
        },
    ],
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
        route = mock.post(f"{_PREFIX}/workflows/enable", params={"dry_run": "true"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "created": [],
                    "would_create": ["Test new versions"],
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
    assert body == {"recipe": "gate", "project_id": PROJECT, "created_via": "mcp"}
    assert out["would_create"] == ["Test new versions"]
    assert out["setup"] == {"github_action": "- uses: comet-ml/opik-ci@v1"}
    assert ".github/workflows/" in out["note"]
    assert "review" in out["note"]
    assert "note" not in out["would_call"], "the project was resolved, so nothing is left to say"


@pytest.mark.anyio
async def test_a_dry_run_without_a_setup_block_says_nothing_about_one() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/workflows/enable", params={"dry_run": "true"}).mock(
            return_value=httpx.Response(
                200, json={"created": [], "would_create": ["x"], "estimate": {}, "setup": None}
            )
        )
        out = await run_write(
            operation="workflow.enable",
            data={"recipe": "collect", "project_id": PROJECT},
            dry_run=True,
            client=_client(),
        )
    assert "setup" not in out
    assert "note" not in out


@pytest.mark.anyio
async def test_a_live_gate_enable_lifts_the_setup_beside_the_note() -> None:
    """The live answer reads like the preview: ``setup`` at the top, and the
    note says the step is written for review, not merged."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/workflows/enable").mock(
            return_value=httpx.Response(
                200,
                json={
                    "created": [{"id": AUTOMATION, "name": "Test new versions"}],
                    "would_create": [],
                    "estimate": {"fired": 11},
                    "setup": {"github_action": "- uses: comet-ml/opik-ci@v1"},
                },
            )
        )
        out = await run_write(
            operation="workflow.enable",
            data={
                "recipe": "gate",
                "project_id": PROJECT,
                "overrides": {"prompt_name": "refund-answer", "model": "gpt-4o-mini"},
            },
            client=_client(),
        )
    assert out["setup"] == {"github_action": "- uses: comet-ml/opik-ci@v1"}
    assert ".github/workflows/" in out["note"]
    assert "never merge" in out["note"]
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/workflows/enable").mock(
            return_value=httpx.Response(200, json={"created": [], "estimate": {}, "setup": None})
        )
        out = await run_write(
            operation="workflow.enable",
            data={"recipe": "collect", "project_id": PROJECT},
            client=_client(),
        )
    assert "setup" not in out
    assert "merge" not in out["note"]


@pytest.mark.anyio
@pytest.mark.parametrize("recipe", ["gate", "improve"])
async def test_an_enable_without_a_prompt_or_model_is_refused_with_what_to_ask(
    recipe: str,
) -> None:
    """The backend refuses; the refusal names the override and says to ask,
    so an agent never fills the prompt or the model in on its own."""
    for code, field in (
        ("prompt_name_required", "overrides.prompt_name"),
        ("model_required", "overrides.model"),
    ):
        with respx.mock(base_url=OPIK_BASE) as mock:
            mock.post(f"{_PREFIX}/workflows/enable", params={"dry_run": "true"}).mock(
                return_value=httpx.Response(400, json={"errors": [f"{code}: {recipe} needs it"]})
            )
            with pytest.raises(ValidationFailedError) as refused:
                await run_write(
                    operation="workflow.enable",
                    data={"recipe": recipe, "project_id": PROJECT},
                    dry_run=True,
                    client=_client(),
                )
        envelope = json.loads(refused.value.to_json())
        assert envelope["issues"] == [{"field": field, "code": code}]
        assert "ask the user" in envelope["message"]


@pytest.mark.anyio
async def test_an_align_enable_hands_back_every_automation_it_created() -> None:
    created = [
        {"id": AUTOMATION, "name": "Random resolution traces to review"},
        {"id": PROJECT, "name": "Check resolution against reviewers"},
    ]
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(f"{_PREFIX}/workflows/enable").mock(
            return_value=httpx.Response(
                200, json={"created": created, "estimate": {"fired": 3}, "setup": None}
            )
        )
        out = await run_write(
            operation="workflow.enable",
            data={
                "recipe": "align",
                "project_id": PROJECT,
                "overrides": {"score_name": "resolution", "n": 20, "threshold": 0.5},
            },
            client=_client(),
        )
    assert out["backend_body"]["created"] == created
    assert "list('automation'" in out["note"]


@pytest.mark.anyio
async def test_a_counted_trigger_and_the_improve_steps_send_their_fields() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(_PREFIX, params={"dry_run": "true"}).mock(
            return_value=httpx.Response(200, json={"automation": {}, "estimate": {}})
        )
        await run_write(
            operation="automation.create", data=_IMPROVE_CREATE, dry_run=True, client=_client()
        )
    body = json.loads(route.calls.last.request.content)
    assert body["triggers"] == [
        {"event": "test_suite:grew", "scope": "trace", "test_suite_name": "regressions", "n": 50}
    ]
    assert body["steps"][0] == _IMPROVE_CREATE["steps"][0]
    assert body["steps"][1]["only_if"]["groups"][0]["conditions"][0]["step_output"] == (
        "optimize.gain"
    )
    assert body["steps"][2]["only_if"] == _GAIN


@pytest.mark.anyio
async def test_a_review_trigger_and_the_judge_steps_send_their_fields() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(_PREFIX, params={"dry_run": "true"}).mock(
            return_value=httpx.Response(200, json={"automation": {}, "estimate": {}})
        )
        await run_write(
            operation="automation.create",
            data=_ALIGN_CHECK_CREATE,
            dry_run=True,
            client=_client(),
        )
    body = json.loads(route.calls.last.request.content)
    assert body["triggers"] == [
        {
            "event": "annotation_queue:submitted",
            "scope": "trace",
            "annotation_queue_name": "review",
            "n": 20,
        }
    ]
    assert body["steps"][0] == _ALIGN_CHECK_CREATE["steps"][0]
    assert body["steps"][1]["score_name"] == "resolution"
    assert body["steps"][1]["only_if"]["groups"][0]["conditions"][0]["step_output"] == (
        "agreement.agreement"
    )


@pytest.mark.anyio
async def test_a_trigger_without_its_count_or_a_zero_count_is_refused_unsent() -> None:
    data = {**_IMPROVE_CREATE, "triggers": [{"event": "test_suite:grew", "n": 0}]}
    with pytest.raises(ValidationFailedError) as refused:
        await run_write(operation="automation.create", data=data, client=_client())
    (issue,) = json.loads(refused.value.to_json())["issues"]
    assert issue["field"] == "triggers.0.n"


@pytest.mark.anyio
async def test_a_github_check_without_the_workspace_token_names_the_put_the_user_runs() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post(_PREFIX).mock(
            return_value=httpx.Response(
                400, json={"errors": ["integration_required: no GitHub token"]}
            )
        )
        with pytest.raises(ValidationFailedError) as refused:
            await run_write(operation="automation.create", data=_GATE_CREATE, client=_client())
    envelope = json.loads(refused.value.to_json())
    assert envelope["issues"] == [{"field": "steps", "code": "integration_required"}]
    assert "PUT /v1/private/automations/integrations/github" in envelope["message"]
    assert "never goes through the MCP" in envelope["message"]


@pytest.mark.anyio
async def test_a_custom_gate_automation_sends_its_trigger_params_and_step_fields() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(_PREFIX, params={"dry_run": "true"}).mock(
            return_value=httpx.Response(200, json={"automation": {}, "estimate": {}})
        )
        await run_write(
            operation="automation.create", data=_GATE_CREATE, dry_run=True, client=_client()
        )
    body = json.loads(route.calls.last.request.content)
    assert body["triggers"] == [
        {
            "event": "ci:run",
            "scope": "trace",
            "repository": "example-org/checkout-agent",
            "branch": "main",
        },
        {"event": "experiment:finished", "scope": "trace", "test_suite_name": "regressions"},
    ]
    assert body["steps"][0] == {
        "id": "run",
        "type": "test_suite.run",
        "test_suite_name": "regressions",
        "model": "gpt-4o-mini",
        "prompt_name": "refund-answer",
    }
    assert body["steps"][1] == {"id": "check", "type": "github.check", "context": "opik/gate"}
    assert body["steps"][2]["kind"] == "webhook"
    assert body["steps"][2]["message"] == "regressions"
    assert body["steps"][2]["only_if"]["groups"][0]["conditions"][0]["step_output"] == (
        "run.regressions"
    )


@pytest.mark.anyio
async def test_a_notify_kind_outside_webhook_or_slack_is_refused_unsent() -> None:
    steps = [{"id": "n", "type": "notify", "url": "https://example.test/hook", "kind": "email"}]
    with pytest.raises(ValidationFailedError) as refused:
        await run_write(
            operation="automation.create", data={**_CREATE, "steps": steps}, client=_client()
        )
    (issue,) = json.loads(refused.value.to_json())["issues"]
    assert issue["field"] == "steps.0.kind"
    assert issue["code"] == "literal_error"


@pytest.mark.anyio
async def test_a_recipe_enable_accepts_every_override_a_recommendation_sends() -> None:
    overrides = {
        "score_name": "resolution",
        "threshold": 0.5,
        "test_suite_name": "Regression · demo",
        "annotation_queue_name": "Judge review",
        "per_day": 10,
        "max_items_per_day": 200,
        "prompt_name": "refund-answer",
        "model": "gpt-4o-mini",
        "repository": "example-org/demo",
        "notify_url": "https://hooks.slack.com/services/T000/B000/XXXX",
        "metric": "levenshtein_ratio",
        "optimizer": "metaprompt",
        "n": 50,
        "min_gain": 0.05,
    }
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(
                200, json={"content": [{"id": PROJECT, "name": "demo"}], "total": 1}
            )
        )
        route = mock.post(f"{_PREFIX}/workflows/enable", params={"dry_run": "true"}).mock(
            return_value=httpx.Response(200, json={"created": [], "estimate": {"fired": 3}})
        )
        await run_write(
            operation="workflow.enable",
            data={"recipe": "collect", "project_name": "demo", "overrides": overrides},
            dry_run=True,
            client=_client(),
        )
    body = json.loads(route.calls.last.request.content)
    assert body["overrides"] == overrides


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
async def test_a_create_keeps_the_backends_warnings_beside_the_id() -> None:
    """A cycle is written and flagged: the flag must survive the id being put in."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        location = f"{OPIK_BASE}{_PREFIX}/{AUTOMATION}"
        mock.post(_PREFIX).mock(
            return_value=httpx.Response(
                201, headers={"Location": location}, json={"warnings": ["cycle_warning: …"]}
            )
        )
        out = await run_write(operation="automation.create", data=_CREATE, client=_client())
    assert out["backend_body"] == {"warnings": ["cycle_warning: …"], "id": AUTOMATION}


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
