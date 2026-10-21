"""The project and what hangs off it: summary, metrics, score names, rules, issues."""

from __future__ import annotations

from datetime import datetime

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import Live

pytestmark = [pytest.mark.live, pytest.mark.anyio]


def _part(record: dict[str, object], *path: str) -> dict[str, object]:
    node: object = record
    for key in path:
        assert isinstance(node, dict), f"no {key!r} under {path}"
        node = node.get(key)
    assert isinstance(node, dict), f"{'.'.join(path)} is not an object: {node!r}"
    return {str(k): v for k, v in node.items()}


async def test_a_project_is_found_by_its_name(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("project", name=manifest.project_name)
    assert answer.column("id") == [manifest.project_id]


async def test_the_summary_compares_the_window_with_the_one_before(
    mcp: Live, manifest: Manifest
) -> None:
    m = manifest
    record = (
        await mcp.read("project", m.project_name, since=m.recent_since, until=m.anchor)
    ).record()
    count = _part(record, "summary", "traces", "count")
    errors = _part(record, "summary", "traces", "errors")
    assert (count["current"], count["previous"]) == (m.recent_sdk_traces, m.previous_sdk_traces)
    rates = (
        100 * m.recent_errors / m.recent_sdk_traces,
        100 * m.previous_errors / m.previous_sdk_traces,
    )
    assert errors["current"] == pytest.approx(rates[0], abs=0.01)
    assert errors["previous"] == pytest.approx(rates[1], abs=0.01)


async def test_the_summary_counts_every_score_name_and_rule_it_names(
    mcp: Live, manifest: Manifest
) -> None:
    record = (await mcp.read("project", manifest.project_name)).record()
    scores = _part(record, "vocabulary", "score_names")
    rules = _part(record, "vocabulary", "online_rules")
    assert (scores["total"], rules["total"]) == (
        len(manifest.score_names),
        len(manifest.rule_names),
    )
    wanted = (set(manifest.score_names), set(manifest.rule_names))
    for part, known in zip((scores, rules), wanted, strict=True):
        names = part["names"]
        assert isinstance(names, list)
        assert set(names) <= known, f"names the project does not have: {set(names) - known}"


async def test_a_daily_metric_adds_up_to_the_window(mcp: Live, manifest: Manifest) -> None:
    m = manifest
    answer = await mcp.list(
        "project_metric",
        project_name=m.project_name,
        metric_type="trace_count",
        interval="daily",
        since=m.recent_since,
        until=m.anchor,
    )
    assert sum(int(row["traces"] or 0) for row in answer.rows()) == m.recent_sdk_traces


async def test_every_score_name_is_listed(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("score_name", project_name=manifest.project_name, size=100)
    assert set(answer.column("name")) == set(manifest.score_names)


async def test_every_online_rule_is_listed(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("online_rule", project_name=manifest.project_name, size=100)
    assert set(answer.column("name")) == set(manifest.rule_names)


async def test_the_open_diagnostics_issue_is_listed(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list("agent_insights_issue", project_name=manifest.project_name)
    assert answer.column("id") == [manifest.open_issue.id]


async def test_a_diagnostics_issue_reads_with_its_name_and_severity(
    mcp: Live, manifest: Manifest
) -> None:
    issue = manifest.open_issue
    record = (
        await mcp.read("agent_insights_issue", issue.id, project_name=manifest.project_name)
    ).record()
    body = _part(record, "issue")
    assert (body["name"], body["severity"]) == (issue.name, issue.severity)


async def test_the_summary_states_the_window_it_compared_against(
    mcp: Live, manifest: Manifest
) -> None:
    m = manifest
    record = (
        await mcp.read("project", m.project_name, since=m.recent_since, until=m.anchor)
    ).record()
    compared = _part(record, "summary", "window", "compared_to")

    def instant(value: object) -> datetime:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(microsecond=0)

    assert (instant(compared["since"]), instant(compared["until"])) == (
        instant(m.previous_since),
        instant(m.recent_since),
    )


async def test_an_error_rate_bucket_is_the_share_of_its_traces_that_errored(
    mcp: Live, manifest: Manifest
) -> None:
    """The rate is the backend's arithmetic: weighted by each bucket's trace
    count, the buckets add back up to the errors the fixture logged, and a
    bucket with no traces is left out rather than charted as 0%."""
    m = manifest
    window = {
        "project_name": m.project_name,
        "interval": "daily",
        "since": m.recent_since,
        "until": m.anchor,
    }
    rates = await mcp.list("project_metric", metric_type="trace_error_rate", **window)
    counts = await mcp.list("project_metric", metric_type="trace_count", **window)
    traces = {row["time"]: float(row["traces"] or 0) for row in counts.rows()}
    rate_rows = rates.rows()
    (rate_column,) = (column for column in rate_rows[0] if column != "time")
    errored = sum(float(row[rate_column]) / 100 * traces[row["time"]] for row in rate_rows)
    assert errored == pytest.approx(m.recent_errors, abs=0.5), rates.text
    listed = {row["time"] for row in rate_rows}
    assert listed == {day for day, count in traces.items() if count}, rates.text


async def test_a_sub_cent_cost_survives_the_table(mcp: Live, manifest: Manifest) -> None:
    """A cheap model costs fractions of a cent a day. Rounding it would read
    as no cost; the daily buckets must add up to the summary's total."""
    m = manifest
    window = {
        "project_name": m.project_name,
        "interval": "daily",
        "since": m.recent_since,
        "until": m.anchor,
    }
    answer = await mcp.list("project_metric", metric_type="trace_cost", **window)
    counts = await mcp.list("project_metric", metric_type="trace_count", **window)
    traces = {row["time"]: float(row["traces"] or 0) for row in counts.rows()}
    rows = answer.rows()
    (cost_column,) = (column for column in rows[0] if column != "time")
    costs = {row["time"]: float(row[cost_column] or 0) for row in rows}
    busy = {day for day, count in traces.items() if count}
    assert busy, f"the fixture logged no traces in the window: {counts.text[:500]}"
    assert all(0 < costs.get(day, 0) < 0.01 for day in busy), answer.text
    record = (
        await mcp.read("project", m.project_name, since=m.recent_since, until=m.anchor)
    ).record()
    total = _part(record, "summary", "traces", "total_cost")["current"]
    assert isinstance(total, float)
    # Each cell is printed to a few significant digits, so the sum is as close
    # as that rounding allows and no closer.
    assert sum(costs.values()) == pytest.approx(total, rel=0.1), answer.text
