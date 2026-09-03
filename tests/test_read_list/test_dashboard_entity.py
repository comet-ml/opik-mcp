"""``read`` / ``list`` for the dashboard entity (OPIK-8210).

A dashboard's stored ``config`` is a nested frontend document. The read tool
returns it flattened — metadata plus one row per chart — because that is the
shape every follow-up call takes an id from (``chart_data``,
``dashboard.remove_charts``), and because handing an agent the raw nesting
spends tokens on grid geometry that answers no question.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

import pytest

from opik_mcp.charts.config import build_config
from opik_mcp.charts.spec import ChartSpec
from opik_mcp.opik_client import OpikListClient, OpikNotFoundError, OpikReadClient
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.registry import ENTITY_REGISTRY, LISTABLE_TYPES, READABLE_TYPES

PROJECT_ID = "01a02549-d318-72fa-bbc5-efb80ba30486"
#: Same project, spelled the way each side wants it: ChartSpec takes a UUID,
#: the stored widget and every API payload carry the string.
PROJECT_UUID = UUID(PROJECT_ID)
DASHBOARD_ID = "01a062db-06af-77fb-b12d-319b265a8800"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClient:
    def __init__(self, dashboards: list[dict[str, Any]]) -> None:
        self._dashboards = dashboards
        self.list_kwargs: dict[str, Any] | None = None

    async def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        for dashboard in self._dashboards:
            if dashboard["id"] == dashboard_id:
                return dashboard
        raise OpikNotFoundError(f"dashboard {dashboard_id!r} not found (404).")

    async def list_dashboards(
        self,
        *,
        name: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        self.list_kwargs = {"name": name, "project_id": project_id, "page": page, "size": size}
        content = [d for d in self._dashboards if name is None or name in d["name"]]
        return {"content": content, "page": page, "size": len(content), "total": len(content)}


def _as_read_client(fake: FakeClient) -> OpikReadClient:
    """The fake implements the two methods a dashboard read touches, not the
    whole Protocol — the cast says that deliberately, in one place."""
    return cast(OpikReadClient, fake)


def _dashboard(**overrides: Any) -> dict[str, Any]:
    return {
        "id": DASHBOARD_ID,
        "name": "Chatbot health",
        "type": "multi_project",
        "created_at": "2026-09-02T16:01:56Z",
        "config": build_config(
            [
                ChartSpec(
                    kind="stat", metric="trace_count", title="Traces", project_id=PROJECT_UUID
                ),
                ChartSpec(metric="cost", project_id=PROJECT_UUID),
            ],
            section_title="Overview",
        ),
        **overrides,
    }


def _payload(output: str) -> dict[str, Any]:
    header, _, body = output.partition("\n")
    assert header.startswith("[read: dashboard")
    payload: dict[str, Any] = json.loads(body)
    return payload


def test_dashboard_is_readable_and_listable() -> None:
    assert "dashboard" in READABLE_TYPES
    assert "dashboard" in LISTABLE_TYPES
    # Workspace-wide, unlike traces/threads: listing needs no parent id.
    assert ENTITY_REGISTRY["dashboard"].list_required_kwargs == ()
    assert ENTITY_REGISTRY["dashboard"].search_by_name_fn is not None


@pytest.mark.anyio
async def test_read_returns_metadata_and_a_flat_chart_list() -> None:
    dashboard = _dashboard()
    payload = _payload(
        await run_read(
            entity_type="dashboard",
            id=DASHBOARD_ID,
            client=_as_read_client(FakeClient([dashboard])),
        )
    )
    assert payload["dashboard"]["name"] == "Chatbot health"
    assert "config" not in payload["dashboard"], "the raw config is storage detail"
    assert payload["chart_count"] == 2
    titles = [c["title"] for c in payload["charts"]]
    assert titles == ["Traces", "Cost"]
    stored_ids = [w["id"] for w in dashboard["config"]["sections"][0]["widgets"]]
    assert [c["widget_id"] for c in payload["charts"]] == stored_ids
    assert payload["charts"][1]["config"]["metricType"] == "COST"
    assert payload["charts"][0]["section"] == "Overview"


@pytest.mark.anyio
async def test_read_resolves_a_dashboard_by_name() -> None:
    payload = _payload(
        await run_read(
            entity_type="dashboard",
            id="Chatbot health",
            client=_as_read_client(FakeClient([_dashboard()])),
        )
    )
    assert payload["dashboard"]["id"] == DASHBOARD_ID


@pytest.mark.anyio
async def test_reading_an_empty_dashboard_says_so_rather_than_failing() -> None:
    payload = _payload(
        await run_read(
            entity_type="dashboard",
            id=DASHBOARD_ID,
            client=_as_read_client(FakeClient([_dashboard(config={"version": 4, "sections": []})])),
        )
    )
    assert payload["charts"] == []
    assert payload["chart_count"] == 0


@pytest.mark.anyio
async def test_list_shows_the_type_column_and_passes_project_scope_through() -> None:
    client = FakeClient([_dashboard()])
    out = await run_list(
        entity_type="dashboard", project_id=PROJECT_ID, client=cast(OpikListClient, client)
    )
    assert "type" in out and "multi_project" in out
    assert DASHBOARD_ID in out
    assert client.list_kwargs is not None
    assert client.list_kwargs["project_id"] == PROJECT_ID
