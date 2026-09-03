"""``chart_data`` — window handling, summarising, and replaying saved charts.

The client is a fake returning canned metric responses, so every assertion
is on the logic this server owns: which request body it builds, what it
reports about a series, and what it refuses to guess at.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.charts.config import build_config
from opik_mcp.charts.query import (
    MAX_PROJECTS,
    auto_interval,
    build_metric_request,
    parse_window,
    query_from_widget,
    resolve_window,
    run_chart_data,
    summarize,
)
from opik_mcp.charts.spec import ChartSpec
from opik_mcp.charts.vocabulary import METRICS
from opik_mcp.opik_client import OpikNotFoundError

PROJECT_ID = "01a02549-d318-72fa-bbc5-efb80ba30486"
#: Same project, spelled the way each side wants it: ChartSpec takes a UUID,
#: the stored widget and every API payload carry the string.
PROJECT_UUID = UUID(PROJECT_ID)
OTHER_ID = "01a010be-d29f-7453-a460-53d5f9ff6aae"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClient:
    """Records the metric requests it was asked to run."""

    def __init__(
        self,
        *,
        results: dict[str, list[dict[str, Any]]] | None = None,
        projects: list[dict[str, Any]] | None = None,
        dashboards: list[dict[str, Any]] | None = None,
    ) -> None:
        self._results = results or {}
        self._projects = projects if projects is not None else [{"id": PROJECT_ID, "name": "demo"}]
        self._dashboards = dashboards or []
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def list_projects(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]:
        content = [p for p in self._projects if name is None or name.lower() in p["name"].lower()]
        return {"content": content, "total": len(content)}

    async def get_project(self, project_id: str, /) -> dict[str, Any]:
        for project in self._projects:
            if project["id"] == project_id:
                return project
        raise OpikNotFoundError(f"project {project_id} not found (404).")

    async def get_dashboard(self, dashboard_id: str, /) -> dict[str, Any]:
        for dashboard in self._dashboards:
            if dashboard["id"] == dashboard_id:
                return dashboard
        raise OpikNotFoundError(f"dashboard {dashboard_id} not found (404).")

    async def list_dashboards(
        self,
        *,
        name: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        content = [d for d in self._dashboards if name is None or name.lower() in d["name"].lower()]
        return {"content": content, "total": len(content)}

    async def get_project_metrics(self, project_id: str, body: dict[str, Any], /) -> dict[str, Any]:
        self.requests.append((project_id, body))
        return {"results": self._results.get(project_id, [])}


def _series(name: str, values: list[float | None], start: str = "2026-09-01") -> dict[str, Any]:
    day = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return {
        "name": name,
        "data": [
            {"time": (day + timedelta(days=i)).isoformat(), "value": v}
            for i, v in enumerate(values)
        ],
    }


def _payload(output: str) -> dict[str, Any]:
    header, _, body = output.partition("\n")
    assert header.startswith("[chart_data:")
    payload: dict[str, Any] = json.loads(body)
    return payload


# --- window and interval -------------------------------------------------- #


def test_window_phrases_beat_hand_computed_timestamps() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    start, end = parse_window("7d", now=now)
    assert end == now
    assert start == now - timedelta(days=7)
    assert parse_window("3m", now=now)[0] == now - timedelta(days=90)


def test_a_window_that_is_not_a_duration_says_what_one_looks_like() -> None:
    with pytest.raises(Exception) as exc:
        parse_window("last week")
    assert "'7d'" in str(exc.value)


@pytest.mark.parametrize(
    ("days", "expected"),
    [(1, "HOURLY"), (14, "DAILY"), (200, "WEEKLY")],
)
def test_bucket_size_follows_the_window(days: int, expected: str) -> None:
    end = datetime(2026, 9, 2, tzinfo=UTC)
    assert auto_interval(end - timedelta(days=days), end) == expected


def test_explicit_timestamps_win_over_the_default_window() -> None:
    start, end, interval = resolve_window(
        window=None, start="2026-08-01T00:00:00Z", end="2026-08-10T00:00:00Z", interval=None
    )
    assert (start.day, end.day, interval) == (1, 10, "DAILY")


def test_an_end_without_a_start_is_refused() -> None:
    with pytest.raises(Exception) as exc:
        resolve_window(window=None, start=None, end="2026-08-10T00:00:00Z", interval=None)
    assert "pass `start`" in str(exc.value)


def test_an_explicit_interval_overrides_the_automatic_one() -> None:
    _s, _e, interval = resolve_window(window="30d", start=None, end=None, interval="hourly")
    assert interval == "HOURLY"


# --- request building ----------------------------------------------------- #


def test_request_body_matches_the_backend_dto() -> None:
    body = build_metric_request(
        metric=METRICS["span_token_usage"],
        start=datetime(2026, 8, 1, tzinfo=UTC),
        end=datetime(2026, 8, 8, tzinfo=UTC),
        interval="DAILY",
        breakdown="model",
        sub_metric="completion_tokens",
        filters=[{"field": "name", "operator": "=", "value": "chat"}],
    )
    assert body == {
        "metric_type": "SPAN_TOKEN_USAGE",
        "interval": "DAILY",
        "interval_start": "2026-08-01T00:00:00Z",
        "interval_end": "2026-08-08T00:00:00Z",
        "span_filters": [{"field": "name", "operator": "=", "value": "chat"}],
        "breakdown": {"field": "model", "sub_metric": "completion_tokens"},
    }


def test_the_request_refuses_a_breakdown_the_backend_would_reject() -> None:
    with pytest.raises(Exception) as exc:
        build_metric_request(
            metric=METRICS["cost"],
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 8, tzinfo=UTC),
            interval="DAILY",
            breakdown="model",
        )
    assert "trace metrics" in str(exc.value)


# --- summarising ---------------------------------------------------------- #


def test_summary_answers_what_a_chart_is_asked() -> None:
    summary = summarize([("t1", 10.0), ("t2", 20.0), ("t3", 15.0)])
    assert summary["first"] == 10.0
    assert summary["last"] == 15.0
    assert summary["max"] == 20.0
    assert summary["avg"] == 15.0
    assert summary["change_pct"] == 50.0


def test_missing_buckets_are_counted_not_zeroed() -> None:
    """ "No data in this bucket" is not zero — averaging it as zero would
    understate every sparse metric."""
    summary = summarize([("t1", None), ("t2", 4.0)])
    assert summary == {
        "points": 2,
        "with_data": 1,
        "first": 4.0,
        "last": 4.0,
        "min": 4.0,
        "max": 4.0,
        "avg": 4.0,
        "total": 4.0,
        "change": 0.0,
        "change_pct": 0.0,
    }


def test_a_series_with_no_data_has_no_statistics() -> None:
    assert summarize([("t1", None)]) == {"points": 1, "with_data": 0}


def test_change_pct_is_omitted_when_the_baseline_is_zero() -> None:
    assert "change_pct" not in summarize([("t1", 0.0), ("t2", 5.0)])


# --- the tool ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_metric_over_a_named_project() -> None:
    client = FakeClient(results={PROJECT_ID: [_series("traces", [1.0, 3.0])]})
    payload = _payload(
        await run_chart_data(metric="latency", project_name="demo", window="7d", client=client)
    )
    assert payload["metric"] == "duration"  # alias resolved
    assert payload["projects"] == [{"id": PROJECT_ID, "name": "demo"}]
    assert payload["series"][0]["summary"]["last"] == 3.0
    assert payload["series"][0]["points"] == [
        ["2026-09-01T00:00:00+00:00", 1.0],
        ["2026-09-02T00:00:00+00:00", 3.0],
    ]


@pytest.mark.anyio
async def test_several_projects_are_labelled_per_series() -> None:
    client = FakeClient(
        projects=[{"id": PROJECT_ID, "name": "demo"}, {"id": OTHER_ID, "name": "other"}],
        results={PROJECT_ID: [_series("traces", [5.0])], OTHER_ID: [_series("traces", [9.0])]},
    )
    payload = _payload(
        await run_chart_data(
            metric="trace_count", project_ids=[PROJECT_ID, OTHER_ID], client=client
        )
    )
    assert [s["project"] for s in payload["series"]] == ["other", "demo"]  # largest first


@pytest.mark.anyio
async def test_an_ambiguous_project_name_is_refused_with_the_candidates() -> None:
    """A silently-wrong project produces a plausible chart of the wrong thing."""
    client = FakeClient(
        projects=[{"id": PROJECT_ID, "name": "demo-a"}, {"id": OTHER_ID, "name": "demo-b"}]
    )
    with pytest.raises(ToolError) as exc:
        await run_chart_data(metric="trace_count", project_name="demo", client=client)
    assert PROJECT_ID in str(exc.value) and OTHER_ID in str(exc.value)


@pytest.mark.anyio
async def test_a_chart_without_any_project_says_what_to_pass() -> None:
    with pytest.raises(ToolError) as exc:
        await run_chart_data(metric="trace_count", client=FakeClient())
    assert "project_name" in str(exc.value)


@pytest.mark.anyio
async def test_too_many_projects_is_refused_before_the_fan_out() -> None:
    client = FakeClient()
    with pytest.raises(ToolError):
        await run_chart_data(
            metric="trace_count", project_ids=[PROJECT_ID] * (MAX_PROJECTS + 1), client=client
        )
    assert not client.requests


@pytest.mark.anyio
async def test_series_are_capped_by_total_and_the_cap_is_reported() -> None:
    client = FakeClient(
        results={PROJECT_ID: [_series(f"s{i}", [float(i)]) for i in range(6)]},
    )
    payload = _payload(
        await run_chart_data(
            metric="trace_count", project_id=PROJECT_ID, max_series=2, client=client
        )
    )
    assert [s["name"] for s in payload["series"]] == ["s5", "s4"]
    assert any("omitted" in note for note in payload["notes"])


@pytest.mark.anyio
async def test_trimmed_points_keep_a_summary_over_the_whole_window() -> None:
    """The summary is the answer; trimming points must not change it."""
    client = FakeClient(results={PROJECT_ID: [_series("traces", [1.0, 2.0, 3.0, 4.0])]})
    payload = _payload(
        await run_chart_data(
            metric="trace_count", project_id=PROJECT_ID, max_points=2, client=client
        )
    )
    series = payload["series"][0]
    assert series["summary"]["points"] == 4
    assert series["summary"]["first"] == 1.0
    assert len(series["points"]) == 2
    assert series["points_omitted"] == 2


@pytest.mark.anyio
async def test_no_data_is_stated_rather_than_left_as_an_empty_list() -> None:
    payload = _payload(
        await run_chart_data(metric="trace_count", project_id=PROJECT_ID, client=FakeClient())
    )
    assert payload["series"] == []
    assert any("No series" in note for note in payload["notes"])


# --- replaying a saved chart ---------------------------------------------- #


def _dashboard(charts: list[ChartSpec], name: str = "Health") -> dict[str, Any]:
    return {"id": "dash-1", "name": name, "config": build_config(charts)}


def test_query_from_widget_inverts_the_spec_compilation() -> None:
    widget = ChartSpec(
        metric="span_token_usage",
        project_id=PROJECT_UUID,
        breakdown="model",
        sub_metric="completion_tokens",
    ).to_widget("w1")
    replay = query_from_widget(widget)
    assert replay["metric"] == "span_token_usage"
    assert replay["breakdown"] == "model"
    assert replay["sub_metric"] == "completion_tokens"
    assert replay["project_ids"] == [PROJECT_ID]


def test_a_widget_this_endpoint_cannot_run_is_refused_not_approximated() -> None:
    widget = ChartSpec(kind="stat", metric="trace_count").to_widget("w1")
    with pytest.raises(Exception) as exc:
        query_from_widget(widget)
    assert "project_metrics" in str(exc.value)


@pytest.mark.anyio
async def test_replaying_a_widget_by_dashboard_name() -> None:
    dashboard = _dashboard([ChartSpec(metric="cost", project_id=PROJECT_UUID)])
    client = FakeClient(dashboards=[dashboard], results={PROJECT_ID: [_series("cost", [0.5, 0.9])]})
    payload = _payload(await run_chart_data(dashboard="Health", window="14d", client=client))
    assert payload["metric"] == "cost"
    assert payload["replayed"]["dashboard"] == "Health"
    assert (
        payload["replayed"]["widget_id"] == dashboard["config"]["sections"][0]["widgets"][0]["id"]
    )


@pytest.mark.anyio
async def test_an_explicit_window_overrides_the_saved_chart() -> None:
    """Replaying over a different window is the main reason to replay."""
    dashboard = _dashboard([ChartSpec(metric="cost", project_id=PROJECT_UUID)])
    client = FakeClient(dashboards=[dashboard], results={PROJECT_ID: [_series("cost", [1.0])]})
    payload = _payload(
        await run_chart_data(dashboard="dash-1", window="24h", interval="hourly", client=client)
    )
    assert payload["interval"] == "HOURLY"


@pytest.mark.anyio
async def test_a_multi_widget_dashboard_asks_which_chart() -> None:
    dashboard = _dashboard(
        [
            ChartSpec(metric="cost", project_id=PROJECT_UUID),
            ChartSpec(metric="trace_count", project_id=PROJECT_UUID),
        ]
    )
    client = FakeClient(dashboards=[dashboard])
    with pytest.raises(ToolError) as exc:
        await run_chart_data(dashboard="dash-1", client=client)
    assert "widget" in str(exc.value)


@pytest.mark.anyio
async def test_widget_without_dashboard_is_refused() -> None:
    with pytest.raises(ToolError) as exc:
        await run_chart_data(metric="cost", widget="w1", client=FakeClient())
    assert "needs `dashboard`" in str(exc.value)


@pytest.mark.anyio
async def test_an_all_projects_widget_says_what_to_pass_instead() -> None:
    """The workspace aggregate is a different backend endpoint; a per-project
    fan-out labelled as one would be a wrong answer, not a partial one."""
    dashboard = _dashboard([ChartSpec(metric="cost", all_projects=True)])
    client = FakeClient(dashboards=[dashboard])
    with pytest.raises(ToolError) as exc:
        await run_chart_data(dashboard="dash-1", client=client)
    assert "project_ids" in str(exc.value)
