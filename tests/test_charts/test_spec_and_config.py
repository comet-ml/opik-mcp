"""ChartSpec validation, and the widget/config documents it compiles to.

The compiled config is read by the Opik frontend, not by us: a config that
the API stores happily can still render as an empty dashboard. These tests
pin the shapes against the frontend's own sources (``lib/dashboard/*``,
``types/dashboard.ts``) — camelCase keys, ``version`` 4, sections owning
both their widgets and a parallel layout array keyed by widget id.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from opik_mcp.charts.config import (
    DASHBOARD_VERSION,
    GRID_COLUMNS,
    DashboardConfigError,
    add_chart,
    build_config,
    flatten_charts,
    remove_widget,
)
from opik_mcp.charts.spec import ChartSpec

PROJECT_A = "01a02549-d318-72fa-bbc5-efb80ba30486"
#: The spec takes a UUID for ``project_id``; the widget it compiles stores the
#: string form, so both spellings appear here for the same project.
PROJECT_UUID = UUID(PROJECT_A)


# --- validation ----------------------------------------------------------- #


def test_a_metric_chart_needs_a_metric() -> None:
    with pytest.raises(ValidationError) as exc:
        ChartSpec(kind="metric")
    assert "kind='metric' needs `metric`" in str(exc.value)


def test_vocabulary_failures_arrive_as_field_validation() -> None:
    """A bad metric/breakdown pair must fail as a Pydantic error, so the write
    tool folds it into the same ``validation_failed`` envelope as a missing
    field — one error shape for "this chart cannot exist"."""
    with pytest.raises(ValidationError) as exc:
        ChartSpec(metric="cost", breakdown="model")
    assert "chart_spec_invalid" in str(exc.value)


def test_unknown_keys_are_refused() -> None:
    """``metricType`` / ``chartType`` are the stored widget's spelling; accepting
    them here would drop the value and render an unconfigured widget."""
    with pytest.raises(ValidationError):
        ChartSpec(metric="trace_count", metricType="TRACE_COUNT")  # type: ignore[call-arg]


def test_one_project_scope_at_a_time() -> None:
    with pytest.raises(ValidationError) as exc:
        ChartSpec(metric="trace_count", project_ids=[PROJECT_UUID], all_projects=True)
    assert "pick ONE project scope" in str(exc.value)


def test_stat_cards_cannot_be_broken_down() -> None:
    with pytest.raises(ValidationError) as exc:
        ChartSpec(kind="stat", metric="trace_count", breakdown="name")
    assert "one number" in str(exc.value)


def test_text_widgets_take_text_and_no_metric() -> None:
    ChartSpec(kind="text", text="## Notes")
    with pytest.raises(ValidationError):
        ChartSpec(kind="text")


def test_breakdown_key_without_a_breakdown_is_refused() -> None:
    with pytest.raises(ValidationError):
        ChartSpec(metric="trace_count", breakdown_key="environment")


# --- compilation to a widget --------------------------------------------- #


def test_metric_widget_uses_the_frontends_key_casing() -> None:
    widget = ChartSpec(
        metric="span_token_usage",
        project_id=PROJECT_UUID,
        breakdown="model",
        sub_metric="completion_tokens",
        chart_type="bar",
    ).to_widget("w1")
    assert widget == {
        "id": "w1",
        "title": "Span token usage by model",
        "type": "project_metrics",
        "config": {
            "metricType": "SPAN_TOKEN_USAGE",
            "chartType": "bar",
            "projectIds": [PROJECT_A],
            "spanFilters": [],
            "breakdown": {"field": "model", "subMetric": "completion_tokens"},
        },
    }


def test_filters_land_under_the_metrics_own_entity() -> None:
    trace_chart = ChartSpec(
        metric="trace_count", filters=[{"field": "name", "operator": "=", "value": "x"}]
    )
    thread_chart = ChartSpec(
        metric="thread_count", filters=[{"field": "status", "operator": "=", "value": "y"}]
    )
    assert "traceFilters" in trace_chart.to_widget("w")["config"]
    assert "threadFilters" in thread_chart.to_widget("w")["config"]


def test_all_projects_is_stored_as_the_dynamic_signal() -> None:
    """``allProjects`` resolves at render time, so a project added tomorrow is
    charted; freezing today's ids into ``projectIds`` would not."""
    config = ChartSpec(metric="trace_count", all_projects=True).to_widget("w")["config"]
    assert config["allProjects"] is True
    assert "projectIds" not in config


def test_stat_widget_carries_its_source_and_stat_name() -> None:
    widget = ChartSpec(kind="stat", metric="duration.p99", project_id=PROJECT_UUID).to_widget("w")
    assert widget["type"] == "project_stats_card"
    assert widget["config"]["source"] == "traces"
    assert widget["config"]["metric"] == "duration.p99"


def test_with_project_id_replaces_the_name() -> None:
    resolved = ChartSpec(metric="trace_count", project_name="demo").with_project_id(PROJECT_A)
    assert resolved.project_name is None
    assert resolved.to_widget("w")["config"]["projectIds"] == [PROJECT_A]


# --- the dashboard config ------------------------------------------------- #


def _spec(metric: str = "trace_count", kind: str = "metric") -> ChartSpec:
    return ChartSpec(kind=kind, metric=metric, project_id=PROJECT_UUID)  # type: ignore[arg-type]


def test_build_config_emits_the_current_version_and_one_section() -> None:
    config = build_config([_spec()], section_title="Volume")
    assert config["version"] == DASHBOARD_VERSION
    assert isinstance(config["lastModified"], int)
    assert len(config["sections"]) == 1
    assert config["sections"][0]["title"] == "Volume"


def test_an_empty_dashboard_still_gets_a_section() -> None:
    """The UI drops widgets onto sections; zero sections means no target."""
    assert len(build_config([])["sections"]) == 1


def test_every_widget_has_exactly_one_layout_entry() -> None:
    config = build_config([_spec(), _spec("cost"), _spec("trace_count", kind="stat")])
    section = config["sections"][0]
    assert [w["id"] for w in section["widgets"]] == [item["i"] for item in section["layout"]]


def test_layout_stays_inside_the_grid() -> None:
    config = build_config([_spec() for _ in range(6)])
    for item in config["sections"][0]["layout"]:
        assert item["x"] + item["w"] <= GRID_COLUMNS
        assert item["y"] >= 0


def test_charts_fill_a_row_before_starting_the_next() -> None:
    """``findFirstAvailablePosition``: a chart is 2 columns wide, so three fit
    on a row and the fourth wraps."""
    layout = build_config([_spec() for _ in range(4)])["sections"][0]["layout"]
    assert [(i["x"], i["y"]) for i in layout] == [(0, 0), (2, 0), (4, 0), (0, 4)]


def test_add_chart_preserves_everything_already_there() -> None:
    config = build_config([_spec()])
    original_widget = config["sections"][0]["widgets"][0]
    updated, added = add_chart(config, _spec("cost"))
    assert updated["sections"][0]["widgets"][0] == original_widget
    assert updated["sections"][0]["widgets"][1] == added
    # The fetched config must be left alone: a failed PATCH cannot be allowed
    # to leave the caller holding an edited copy of a dashboard it never wrote.
    assert len(config["sections"][0]["widgets"]) == 1


def test_add_chart_creates_a_named_section_that_does_not_exist_yet() -> None:
    config = build_config([_spec()], section_title="Overview")
    updated, added = add_chart(config, _spec("cost"), section="Cost")
    assert [s["title"] for s in updated["sections"]] == ["Overview", "Cost"]
    assert updated["sections"][1]["widgets"] == [added]


def test_add_chart_targets_a_section_by_id_too() -> None:
    config = build_config([_spec()])
    section_id = config["sections"][0]["id"]
    updated, _added = add_chart(config, _spec("cost"), section=section_id)
    assert len(updated["sections"]) == 1


def test_remove_widget_drops_its_layout_entry() -> None:
    """An orphaned layout entry leaves a hole in the grid the UI cannot fill."""
    config = build_config([_spec(), _spec("cost")])
    victim = config["sections"][0]["widgets"][0]["id"]
    updated, removed = remove_widget(config, victim)
    assert removed["id"] == victim
    section = updated["sections"][0]
    assert victim not in [w["id"] for w in section["widgets"]]
    assert victim not in [item["i"] for item in section["layout"]]


def test_removing_an_unknown_widget_lists_the_real_ids() -> None:
    config = build_config([_spec()])
    known = config["sections"][0]["widgets"][0]["id"]
    with pytest.raises(DashboardConfigError) as exc:
        remove_widget(config, "nope")
    assert known in str(exc.value)


def test_a_config_we_cannot_read_is_refused_rather_than_overwritten() -> None:
    with pytest.raises(DashboardConfigError):
        add_chart("not a config", _spec())
    with pytest.raises(DashboardConfigError):
        add_chart({"sections": "nope"}, _spec())


def test_flatten_charts_is_what_a_reader_needs() -> None:
    config = build_config([_spec()], section_title="Volume")
    (chart,) = flatten_charts(config)
    assert chart["section"] == "Volume"
    assert chart["type"] == "project_metrics"
    assert chart["widget_id"] == config["sections"][0]["widgets"][0]["id"]
    assert chart["config"]["metricType"] == "TRACE_COUNT"


def test_flatten_charts_never_raises_on_a_foreign_config() -> None:
    """It runs on every ``read('dashboard', …)``: a config authored by a newer
    frontend must degrade to "no charts", never fail the read."""
    assert flatten_charts(None) == []
    assert flatten_charts({"sections": [{"widgets": [{"no": "id"}]}]}) == []
