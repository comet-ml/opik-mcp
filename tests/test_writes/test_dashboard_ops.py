"""The ``dashboard.*`` write operations, end to end through the dispatcher.

These operations do two things no other write does — they resolve names
against the backend, and they read a config before writing it back — so the
assertions here are on the exact request that reaches Opik, with the backend
mocked by ``respx``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from opik_mcp.charts.config import build_config
from opik_mcp.charts.spec import ChartSpec
from opik_mcp.opik_client import OpikClient
from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import ValidationFailedError
from opik_mcp.writes.registry import WRITE_REGISTRY
from opik_mcp.writes.scopes import ALL_WRITE_SCOPES, SCOPE_DASHBOARD_EDIT

OPIK_BASE = "https://opik.test"
PROJECT_ID = "01a02549-d318-72fa-bbc5-efb80ba30486"
#: Same project, spelled the way each side wants it: ChartSpec takes a UUID,
#: the stored widget and every API payload carry the string.
PROJECT_UUID = UUID(PROJECT_ID)
DASHBOARD_ID = "01a062db-06af-77fb-b12d-319b265a8800"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


def _project_page(*names: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": [
                {"id": PROJECT_ID if i == 0 else f"id-{i}", "name": n} for i, n in enumerate(names)
            ],
            "total": len(names),
        },
    )


def _stored_dashboard(charts: list[ChartSpec], **overrides: Any) -> dict[str, Any]:
    return {
        "id": DASHBOARD_ID,
        "name": "Chatbot health",
        "type": "multi_project",
        "config": build_config(charts),
        **overrides,
    }


def _body(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


# --- create --------------------------------------------------------------- #


@pytest.mark.anyio
async def test_create_resolves_the_project_and_compiles_the_charts() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(return_value=_project_page("demo"))
        route = mock.post("/v1/private/dashboards").mock(
            return_value=httpx.Response(201, json={"id": DASHBOARD_ID, "name": "Chatbot health"})
        )
        result = await run_write(
            operation="dashboard.create",
            data={
                "name": "Chatbot health",
                "project_name": "demo",
                "charts": [
                    {"kind": "stat", "metric": "trace_count", "title": "Traces"},
                    {"kind": "metric", "metric": "cost"},
                ],
            },
            client=_client(),
        )

    body = _body(route.calls[0].request)
    assert body["name"] == "Chatbot health"
    assert body["type"] == "multi_project"
    assert body["project_id"] == PROJECT_ID
    widgets = body["config"]["sections"][0]["widgets"]
    assert [w["type"] for w in widgets] == ["project_stats_card", "project_metrics"]
    # Charts inherit the dashboard's project: a widget with no project scope
    # renders as "not configured" in the UI.
    assert all(w["config"]["projectIds"] == [PROJECT_ID] for w in widgets)
    assert result["status"] == 201
    assert [c["title"] for c in result["details"]["charts_added"]] == ["Traces", "Cost"]
    assert result["details"]["dashboard_id"] == DASHBOARD_ID


@pytest.mark.anyio
async def test_a_chart_keeps_its_own_project_over_the_dashboards() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(return_value=_project_page("other"))
        route = mock.post("/v1/private/dashboards").mock(
            return_value=httpx.Response(201, json={"id": DASHBOARD_ID})
        )
        await run_write(
            operation="dashboard.create",
            data={
                "name": "Cross-project",
                "charts": [{"kind": "metric", "metric": "cost", "project_name": "other"}],
            },
            client=_client(),
        )
    body = _body(route.calls[0].request)
    assert "project_id" not in body
    assert body["config"]["sections"][0]["widgets"][0]["config"]["projectIds"] == [PROJECT_ID]


@pytest.mark.anyio
async def test_an_unknown_project_is_refused_rather_than_created() -> None:
    """opik-backend would create a project for an unknown ``project_name`` —
    which turns a typo into a permanent empty project charting nothing."""
    # assert_all_called=False: the POST route exists to be asserted UNcalled.
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(200, json={"content": [], "total": 0})
        )
        create = mock.post("/v1/private/dashboards")
        with pytest.raises(ValidationFailedError) as exc:
            await run_write(
                operation="dashboard.create",
                data={"name": "x", "project_name": "typo"},
                client=_client(),
            )
    assert not create.called
    issue = exc.value.to_dict()["issues"][0]
    assert issue["code"] == "project_not_found"


@pytest.mark.anyio
async def test_an_ambiguous_project_lists_the_candidates() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(return_value=_project_page("demo-a", "demo-b"))
        with pytest.raises(ValidationFailedError) as exc:
            await run_write(
                operation="dashboard.create",
                data={"name": "x", "project_name": "demo"},
                client=_client(),
            )
    issue = exc.value.to_dict()["issues"][0]
    assert issue["code"] == "project_ambiguous"
    assert PROJECT_ID in issue["message"]


@pytest.mark.anyio
async def test_an_invalid_chart_fails_before_any_backend_call() -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        create = mock.post("/v1/private/dashboards")
        with pytest.raises(ValidationFailedError) as exc:
            await run_write(
                operation="dashboard.create",
                data={"name": "x", "charts": [{"metric": "cost", "breakdown": "model"}]},
                client=_client(),
            )
    assert not create.called
    assert "chart_spec_invalid" in json.dumps(exc.value.to_dict())


# --- add / remove --------------------------------------------------------- #


@pytest.mark.anyio
async def test_add_charts_reads_the_live_config_and_writes_it_back_whole() -> None:
    stored = _stored_dashboard([ChartSpec(metric="trace_count", project_id=PROJECT_UUID)])
    existing_id = stored["config"]["sections"][0]["widgets"][0]["id"]
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=stored)
        )
        route = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json={"id": DASHBOARD_ID})
        )
        result = await run_write(
            operation="dashboard.add_charts",
            data={
                "dashboard": DASHBOARD_ID,
                "section": "Cost",
                "charts": [{"kind": "metric", "metric": "cost", "project_id": PROJECT_ID}],
            },
            client=_client(),
        )

    sections = _body(route.calls[0].request)["config"]["sections"]
    assert [s["title"] for s in sections] == ["Overview", "Cost"]
    # The chart that was already there survives untouched.
    assert sections[0]["widgets"][0]["id"] == existing_id
    assert len(sections[1]["widgets"]) == 1
    assert result["details"]["charts_added"][0]["title"] == "Cost"
    assert result["details"]["dashboard_id"] == DASHBOARD_ID


@pytest.mark.anyio
async def test_a_dashboard_named_rather_than_id_d_is_looked_up() -> None:
    stored = _stored_dashboard([])
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/dashboards/Chatbot%20health").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        mock.get("/v1/private/dashboards").mock(
            return_value=httpx.Response(
                200, json={"content": [{"id": DASHBOARD_ID, "name": "Chatbot health"}], "total": 1}
            )
        )
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=stored)
        )
        route = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json={"id": DASHBOARD_ID})
        )
        await run_write(
            operation="dashboard.add_charts",
            data={
                "dashboard": "Chatbot health",
                "charts": [{"kind": "text", "text": "## Notes"}],
            },
            client=_client(),
        )
    assert route.called


@pytest.mark.anyio
async def test_an_unknown_dashboard_points_at_the_listing() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/dashboards/nope").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        mock.get("/v1/private/dashboards").mock(
            return_value=httpx.Response(200, json={"content": [], "total": 0})
        )
        with pytest.raises(ValidationFailedError) as exc:
            await run_write(
                operation="dashboard.remove_charts",
                data={"dashboard": "nope", "widget_ids": ["w1"]},
                client=_client(),
            )
    issue = exc.value.to_dict()["issues"][0]
    assert issue["code"] == "dashboard_not_found"
    assert "list('dashboard')" in issue["message"]


@pytest.mark.anyio
async def test_remove_charts_drops_the_widget_and_its_layout_entry() -> None:
    stored = _stored_dashboard(
        [
            ChartSpec(metric="trace_count", project_id=PROJECT_UUID),
            ChartSpec(metric="cost", project_id=PROJECT_UUID),
        ]
    )
    victim = stored["config"]["sections"][0]["widgets"][0]["id"]
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=stored)
        )
        route = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json={"id": DASHBOARD_ID})
        )
        result = await run_write(
            operation="dashboard.remove_charts",
            data={"dashboard": DASHBOARD_ID, "widget_ids": [victim]},
            client=_client(),
        )
    section = _body(route.calls[0].request)["config"]["sections"][0]
    assert victim not in [w["id"] for w in section["widgets"]]
    assert victim not in [item["i"] for item in section["layout"]]
    assert result["details"]["charts_removed"][0]["widget_id"] == victim


@pytest.mark.anyio
async def test_removing_an_unknown_widget_writes_nothing() -> None:
    stored = _stored_dashboard([ChartSpec(metric="cost", project_id=PROJECT_UUID)])
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=stored)
        )
        patch = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}")
        with pytest.raises(ValidationFailedError) as exc:
            await run_write(
                operation="dashboard.remove_charts",
                data={"dashboard": DASHBOARD_ID, "widget_ids": ["ghost"]},
                client=_client(),
            )
    assert not patch.called
    assert exc.value.to_dict()["issues"][0]["code"] == "widget_not_found"


# --- update --------------------------------------------------------------- #


@pytest.mark.anyio
async def test_update_sends_only_what_changed() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=_stored_dashboard([]))
        )
        route = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json={"id": DASHBOARD_ID})
        )
        await run_write(
            operation="dashboard.update",
            data={"dashboard": DASHBOARD_ID, "name": "Chatbot health (prod)"},
            client=_client(),
        )
    assert _body(route.calls[0].request) == {"name": "Chatbot health (prod)"}


@pytest.mark.anyio
async def test_update_with_charts_replaces_the_whole_config() -> None:
    stored = _stored_dashboard([ChartSpec(metric="trace_count", project_id=PROJECT_UUID)])
    old_id = stored["config"]["sections"][0]["widgets"][0]["id"]
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json=stored)
        )
        route = mock.patch(f"/v1/private/dashboards/{DASHBOARD_ID}").mock(
            return_value=httpx.Response(200, json={"id": DASHBOARD_ID})
        )
        result = await run_write(
            operation="dashboard.update",
            data={
                "dashboard": DASHBOARD_ID,
                "charts": [{"kind": "metric", "metric": "cost", "project_id": PROJECT_ID}],
            },
            client=_client(),
        )
    widgets = _body(route.calls[0].request)["config"]["sections"][0]["widgets"]
    assert len(widgets) == 1
    assert widgets[0]["id"] != old_id
    assert result["details"]["charts_replaced"][0]["title"] == "Cost"


@pytest.mark.anyio
async def test_an_update_that_changes_nothing_is_refused() -> None:
    with pytest.raises(ValidationFailedError) as exc:
        await run_write(
            operation="dashboard.update", data={"dashboard": DASHBOARD_ID}, client=_client()
        )
    assert "dashboard_update_empty" in json.dumps(exc.value.to_dict())


# --- dry run and scopes --------------------------------------------------- #


@pytest.mark.anyio
async def test_dry_run_touches_no_backend_and_says_what_it_omits() -> None:
    result = await run_write(
        operation="dashboard.create",
        data={
            "name": "Chatbot health",
            "project_name": "demo",
            "charts": [{"kind": "metric", "metric": "trace_count"}],
        },
        dry_run=True,
    )
    call = result["would_call"]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/private/dashboards"
    assert call["body"]["config"]["version"] == 4
    # The preview is honest about the part it could not compute.
    assert "resolved to project ids at execution" in call["note"]


@pytest.mark.anyio
async def test_dry_run_of_an_edit_previews_the_change_not_a_fabricated_config() -> None:
    result = await run_write(
        operation="dashboard.add_charts",
        data={"dashboard": "Chatbot health", "charts": [{"kind": "metric", "metric": "cost"}]},
        dry_run=True,
    )
    call = result["would_call"]
    assert call["method"] == "PATCH"
    assert "config" not in call["body"], "a preview must not imply it read the live config"
    assert call["body"]["charts_to_add"][0]["config"]["metricType"] == "COST"
    assert "merged config" in call["note"]


@pytest.mark.anyio
async def test_dashboard_operations_require_the_dashboard_scope() -> None:
    from opik_mcp.writes.errors import AuthorizationDeniedError

    without = ALL_WRITE_SCOPES - {SCOPE_DASHBOARD_EDIT}
    with pytest.raises(AuthorizationDeniedError):
        await run_write(
            operation="dashboard.create",
            data={"name": "x"},
            scopes=without,
            client=_client(),
        )


def test_every_dashboard_operation_is_registered_with_the_dashboard_scope() -> None:
    from opik_mcp.charts.dashboard_ops import DASHBOARD_OPERATIONS

    assert set(WRITE_REGISTRY) >= DASHBOARD_OPERATIONS
    for name in DASHBOARD_OPERATIONS:
        op = WRITE_REGISTRY[name]
        assert op.oauth_scope == SCOPE_DASHBOARD_EDIT
        # Config documents are large and each edit is a read-modify-write;
        # batching them would multiply that, not amortise it.
        assert op.supports_batch is False
