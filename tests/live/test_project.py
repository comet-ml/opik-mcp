"""The project and what hangs off it: summary, metrics, score names, rules, issues."""

from __future__ import annotations

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
    for part in (scores, rules):
        names = part["names"]
        assert isinstance(names, list)
        assert len(names) <= int(str(part["total"]))


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
