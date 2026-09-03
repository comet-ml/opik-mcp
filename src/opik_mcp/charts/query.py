"""``chart_data`` tool — run a chart's query and get its numbers back.

The read half of the dashboards surface. It answers the question a chart
answers ("what has trace volume done this week, split by trace name?")
without a dashboard having to exist, and it replays a chart that DOES exist
(``dashboard=…, widget=…``) so an agent can analyse what a user is looking at
in the UI.

Two design choices worth stating:

*Series come back summarised.* A raw ``/metrics`` response is a wall of
timestamped points, and the questions asked of a chart are almost always
"what is it now, where was it, and which bucket is worst" — so each series
carries ``first/last/min/max/avg/total/change_pct`` alongside its points.
The summary is computed over the FULL series even when points are trimmed to
fit the budget, so trimming can never change the answer to those questions.

*The window is a phrase, not a pair of timestamps.* ``window="7d"`` with an
auto-chosen bucket size is what a caller means nine times in ten, and an
LLM computing two ISO timestamps by hand gets timezones wrong. Explicit
``start``/``end`` remain available for a fixed window.

Only project-scoped time-series metrics are queryable here — that is the one
family opik-backend exposes as a single addressable query. Replaying a stat
card or an experiment widget returns a message naming what to use instead,
rather than a half-answer.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.charts.config import flatten_charts
from opik_mcp.charts.vocabulary import (
    METRICS,
    ChartVocabularyError,
    MetricDef,
    check_breakdown,
    check_sub_metric,
    request_filter_key,
    resolve_interval,
    resolve_metric,
)
from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
    make_opik_client,
)
from opik_mcp.read_list.compression import compact_json, estimate_tokens

logger = logging.getLogger("opik_mcp.charts.query")

#: Ceiling on the fan-out. Each project is one backend query, and a chart over
#: more than a handful of projects is a workspace question, not a chart.
MAX_PROJECTS = 10

#: Defaults for the response budget. A daily series over a quarter is ~90
#: points; 20 series x 90 points is already a large tool result, so both are
#: capped and the caller is told when a cap bit.
DEFAULT_MAX_SERIES = 20
DEFAULT_MAX_POINTS = 90

_WINDOW_RE = re.compile(r"^(\d+)\s*([hdwm])$", re.IGNORECASE)
_WINDOW_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


class OpikChartClient(Protocol):
    """The client surface ``chart_data`` needs.

    A Protocol rather than the concrete client so the tests exercise the real
    query/summary logic against a fake that returns canned metric responses.
    """

    async def list_projects(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def get_project(self, project_id: str, /) -> dict[str, Any]: ...

    async def get_dashboard(self, dashboard_id: str, /) -> dict[str, Any]: ...

    async def list_dashboards(
        self,
        *,
        name: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def get_project_metrics(
        self, project_id: str, body: dict[str, Any], /
    ) -> dict[str, Any]: ...


# --- window / interval ---------------------------------------------------- #


def parse_window(window: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """``"7d"`` → (now - 7 days, now). Accepts h/d/w/m (m = 30 days)."""
    end = now or datetime.now(UTC)
    match = _WINDOW_RE.match(window.strip())
    if match is None:
        raise ChartVocabularyError(
            f"window {window!r} is not a duration. Use e.g. '24h', '7d', '4w', "
            "'3m' — or pass explicit start/end timestamps."
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount <= 0:
        raise ChartVocabularyError("window must be a positive duration, e.g. '7d'.")
    delta = (
        timedelta(days=30 * amount) if unit == "m" else timedelta(**{_WINDOW_UNITS[unit]: amount})
    )
    return end - delta, end


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChartVocabularyError(
            f"{field}={value!r} is not an ISO-8601 timestamp, e.g. '2026-09-01T00:00:00Z'."
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def auto_interval(start: datetime, end: datetime) -> str:
    """Bucket size that keeps a window readable: ~24-120 points.

    Hourly past two days is hundreds of points for no extra insight; weekly
    inside a fortnight is two bars. The thresholds mirror the ranges the Opik
    UI's own date presets use.
    """
    span = end - start
    if span <= timedelta(days=2):
        return "HOURLY"
    if span <= timedelta(days=60):
        return "DAILY"
    return "WEEKLY"


def resolve_window(
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    interval: str | None,
) -> tuple[datetime, datetime, str]:
    """Resolve (start, end, wire interval) from the caller's time arguments."""
    if start is not None or end is not None:
        started = _parse_timestamp(start, "start") if start else None
        ended = _parse_timestamp(end, "end") if end else datetime.now(UTC)
        if started is None:
            raise ChartVocabularyError("pass `start` when passing `end`, or use `window`.")
        if started >= ended:
            raise ChartVocabularyError("start must be before end.")
    else:
        started, ended = parse_window(window or "7d")
    wire_interval = (
        auto_interval(started, ended)
        if interval is None or interval.strip().lower() in ("", "auto")
        else resolve_interval(interval)
    )
    return started, ended, wire_interval


def _iso(moment: datetime) -> str:
    """UTC ISO-8601 with a ``Z`` suffix — the form opik-backend parses."""
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- request building ----------------------------------------------------- #


def build_metric_request(
    *,
    metric: MetricDef,
    start: datetime,
    end: datetime,
    interval: str,
    breakdown: str | None = None,
    breakdown_key: str | None = None,
    sub_metric: str | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``POST /v1/private/projects/{id}/metrics`` body for one chart."""
    body: dict[str, Any] = {
        "metric_type": metric.wire,
        "interval": interval,
        "interval_start": _iso(start),
        "interval_end": _iso(end),
    }
    if filters:
        body[request_filter_key(metric.family)] = filters
    if breakdown is not None:
        check_breakdown(metric, breakdown, metadata_key=breakdown_key)
        check_sub_metric(metric, breakdown, sub_metric)
        config: dict[str, Any] = {"field": breakdown}
        if breakdown_key is not None:
            config["metadata_key"] = breakdown_key
        if sub_metric is not None:
            config["sub_metric"] = sub_metric
        body["breakdown"] = config
    return body


def query_from_widget(widget: dict[str, Any]) -> dict[str, Any]:
    """Translate a stored widget's config into ``chart_data`` arguments.

    The inverse of ``ChartSpec.to_widget`` for the one widget type that maps
    to a metric query. Returns the kwargs (``metric``, ``breakdown``,
    ``filters``, ``project_ids``, …); raises ``ChartVocabularyError`` for a
    widget type whose data does not come from this endpoint, because a plotted
    number from the wrong source is worse than a refusal.
    """
    widget_type = widget.get("type")
    if widget_type != "project_metrics":
        raise ChartVocabularyError(
            f"widget {widget.get('id')!r} is a {widget_type!r} widget — chart_data "
            "runs project_metrics (time-series) widgets. Read the dashboard to see "
            "a stat card's or experiment widget's configuration."
        )
    config = widget.get("config")
    if not isinstance(config, dict):
        raise ChartVocabularyError(f"widget {widget.get('id')!r} has no config to run.")
    wire = str(config.get("metricType") or "")
    metric = next((m for m in METRICS.values() if m.wire == wire), None)
    if metric is None:
        raise ChartVocabularyError(
            f"widget {widget.get('id')!r} charts metric type {wire!r}, which this "
            "server does not know how to query."
        )
    raw_breakdown = config.get("breakdown")
    breakdown_config: dict[str, Any] = raw_breakdown if isinstance(raw_breakdown, dict) else {}
    field = breakdown_config.get("field")
    project_ids = config.get("projectIds")
    if not isinstance(project_ids, list):
        single = config.get("projectId")
        project_ids = [single] if isinstance(single, str) and single else []
    filters = config.get(
        {"trace": "traceFilters", "span": "spanFilters", "thread": "threadFilters"}[metric.family]
    )
    return {
        "metric": metric.name,
        "breakdown": field if isinstance(field, str) and field != "none" else None,
        "breakdown_key": breakdown_config.get("metadataKey"),
        "sub_metric": breakdown_config.get("subMetric"),
        "filters": [f for f in filters if isinstance(f, dict)]
        if isinstance(filters, list)
        else None,
        "project_ids": [p for p in project_ids if isinstance(p, str)],
        "all_projects": bool(config.get("allProjects")),
        "title": widget.get("title"),
    }


# --- summarising ---------------------------------------------------------- #


def summarize(points: list[tuple[str, float | None]]) -> dict[str, Any]:
    """Per-series stats over the full point list.

    ``None`` points are real signal — "no data in this bucket" is different
    from zero for an average — so they are counted, excluded from the maths,
    and never coerced.
    """
    values = [v for _t, v in points if isinstance(v, int | float)]
    summary: dict[str, Any] = {"points": len(points), "with_data": len(values)}
    if not values:
        return summary
    first, last = values[0], values[-1]
    summary |= {
        "first": first,
        "last": last,
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 6),
        "total": round(sum(values), 6),
    }
    summary["change"] = round(last - first, 6)
    if first:
        summary["change_pct"] = round((last - first) / abs(first) * 100, 2)
    return summary


def _points_of(series: dict[str, Any]) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for point in series.get("data") or []:
        if not isinstance(point, dict):
            continue
        value = point.get("value")
        out.append((str(point.get("time")), value if isinstance(value, int | float) else None))
    return out


def _series_rank(summary: dict[str, Any]) -> float:
    """Ordering key — biggest series first, so a capped breakdown keeps the
    buckets that matter. ``total`` is the right magnitude for counts and cost;
    for rates and percentiles it still orders by "most present"."""
    total = summary.get("total")
    return abs(float(total)) if isinstance(total, int | float) else 0.0


# --- the tool ------------------------------------------------------------- #


async def _resolve_projects(
    client: OpikChartClient,
    *,
    project_id: str | None,
    project_name: str | None,
    project_ids: list[str] | None,
) -> list[dict[str, str]]:
    """Resolve the caller's project arguments to ``[{id, name}]``.

    A name is resolved with the same rules as ``read``: exactly one match
    resolves, several ask which, none is an error naming the filter used —
    charts are worth failing loudly for, because a silently-wrong project
    produces a plausible chart of the wrong thing.
    """
    if project_ids:
        if len(project_ids) > MAX_PROJECTS:
            raise ToolError(
                f"chart_data takes at most {MAX_PROJECTS} projects; got {len(project_ids)}. "
                "Chart the top projects individually, or use all-projects widgets in the UI."
            )
        resolved: list[dict[str, str]] = []
        for pid in project_ids:
            try:
                project = await client.get_project(pid)
            except OpikNotFoundError:
                raise ToolError(f"project {pid!r} not found in this workspace.") from None
            resolved.append({"id": pid, "name": str(project.get("name") or pid)})
        return resolved

    if project_id is not None:
        try:
            project = await client.get_project(project_id)
        except OpikNotFoundError:
            raise ToolError(
                f"project {project_id!r} not found in this workspace. "
                "list('project') shows the projects you can chart."
            ) from None
        return [{"id": project_id, "name": str(project.get("name") or project_id)}]

    if project_name is None:
        raise ToolError(
            "chart_data needs a project: pass project_name, project_id, or "
            "project_ids (or dashboard+widget to replay a saved chart)."
        )

    page = await client.list_projects(name=project_name, size=10)
    candidates = [
        item
        for item in (page.get("content") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    exact = [c for c in candidates if c.get("name") == project_name]
    if len(exact) == 1:
        return [{"id": str(exact[0]["id"]), "name": project_name}]
    if len(candidates) == 1:
        return [{"id": str(candidates[0]["id"]), "name": str(candidates[0].get("name") or "")}]
    if not candidates:
        raise ToolError(
            f"no project matching {project_name!r} in this workspace. "
            "list('project') shows what is there."
        )
    listed = ", ".join(f"{c.get('name')!r} ({c['id']})" for c in candidates[:10])
    raise ToolError(
        f"{len(candidates)} projects match {project_name!r} — pass project_id for the "
        f"one you mean: {listed}"
    )


async def _resolve_widget(
    client: OpikChartClient, dashboard: str, widget: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(dashboard_record, widget)`` for a saved chart named by id or title."""
    record: dict[str, Any] | None = None
    try:
        record = await client.get_dashboard(dashboard)
    except (OpikNotFoundError, OpikValidationError):
        record = None
    if record is None:
        page = await client.list_dashboards(name=dashboard, size=10)
        matches = [
            item
            for item in (page.get("content") or [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        exact = [m for m in matches if m.get("name") == dashboard]
        pick = exact[0] if len(exact) == 1 else (matches[0] if len(matches) == 1 else None)
        if pick is None:
            if not matches:
                raise ToolError(
                    f"no dashboard matching {dashboard!r}. list('dashboard') shows "
                    "the dashboards in this workspace."
                )
            listed = ", ".join(f"{m.get('name')!r} ({m['id']})" for m in matches[:10])
            raise ToolError(
                f"{len(matches)} dashboards match {dashboard!r} — pass the id: {listed}"
            )
        record = await client.get_dashboard(str(pick["id"]))

    charts = flatten_charts(record.get("config"))
    if not charts:
        raise ToolError(
            f"dashboard {record.get('name')!r} has no widgets yet — add one with "
            "write('dashboard.add_chart', …)."
        )
    if widget is None:
        if len(charts) > 1:
            listed = ", ".join(f"{c['title']!r} ({c['widget_id']})" for c in charts[:20])
            raise ToolError(
                f"dashboard {record.get('name')!r} has {len(charts)} widgets — name one "
                f"via `widget`: {listed}"
            )
        chosen = charts[0]
    else:
        pool = [c for c in charts if c["widget_id"] == widget or c["title"] == widget]
        if not pool:
            listed = ", ".join(f"{c['title']!r} ({c['widget_id']})" for c in charts[:20])
            raise ToolError(f"no widget {widget!r} on that dashboard. Widgets: {listed}")
        chosen = pool[0]
    return record, {
        "id": chosen["widget_id"],
        "title": chosen["title"],
        "type": chosen["type"],
        "config": chosen["config"],
    }


async def run_chart_data(
    *,
    metric: str | None = None,
    project_name: str | None = None,
    project_id: str | None = None,
    project_ids: list[str] | None = None,
    window: str | None = None,
    start: str | None = None,
    end: str | None = None,
    interval: str | None = None,
    breakdown: str | None = None,
    breakdown_key: str | None = None,
    sub_metric: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    dashboard: str | None = None,
    widget: str | None = None,
    max_series: int = DEFAULT_MAX_SERIES,
    max_points: int = DEFAULT_MAX_POINTS,
    settings: Settings | None = None,
    client: OpikChartClient | None = None,
) -> str:
    """``chart_data`` entrypoint. See ``server.py`` for the registered tool."""
    opik = client if client is not None else make_opik_client(settings or get_settings())

    source = "spec"
    widget_record: dict[str, Any] | None = None
    dashboard_record: dict[str, Any] | None = None
    if dashboard is not None:
        source = "widget"
        try:
            dashboard_record, widget_record = await _resolve_widget(opik, dashboard, widget)
            replay = query_from_widget(widget_record)
        except ChartVocabularyError as exc:
            raise ToolError(str(exc)) from exc
        # Explicit arguments win over the saved config: replaying a chart over a
        # different window ("what did this chart look like last month?") is the
        # main reason to replay one at all.
        metric = metric or replay["metric"]
        breakdown = breakdown or replay["breakdown"]
        breakdown_key = breakdown_key or replay["breakdown_key"]
        sub_metric = sub_metric or replay["sub_metric"]
        filters = filters if filters is not None else replay["filters"]
        if project_id is None and project_name is None and not project_ids:
            project_ids = replay["project_ids"] or None
            if not project_ids and replay["all_projects"]:
                raise ToolError(
                    f"widget {widget_record['id']!r} charts ALL projects in the "
                    "workspace, which this tool cannot aggregate. Pass project_ids "
                    f"(up to {MAX_PROJECTS}) to chart specific projects instead."
                )
    elif widget is not None:
        raise ToolError("`widget` needs `dashboard` — pass the dashboard holding it.")

    if metric is None:
        raise ToolError(
            "chart_data needs `metric` (e.g. 'trace_count'), or dashboard+widget "
            "to replay a saved chart."
        )

    try:
        metric_def = resolve_metric(metric)
        window_start, window_end, wire_interval = resolve_window(
            window=window, start=start, end=end, interval=interval
        )
        body = build_metric_request(
            metric=metric_def,
            start=window_start,
            end=window_end,
            interval=wire_interval,
            breakdown=breakdown,
            breakdown_key=breakdown_key,
            sub_metric=sub_metric,
            filters=filters,
        )
    except ChartVocabularyError as exc:
        raise ToolError(str(exc)) from exc

    projects = await _resolve_projects(
        opik, project_id=project_id, project_name=project_name, project_ids=project_ids
    )

    series: list[dict[str, Any]] = []
    for project in projects:
        try:
            response = await opik.get_project_metrics(project["id"], body)
        except (OpikAuthError, OpikNotFoundError, OpikValidationError, OpikServerError) as exc:
            raise ToolError(
                f"failed to read metric {metric_def.name!r} for project {project['name']!r}: {exc}"
            ) from exc
        for raw in response.get("results") or []:
            if not isinstance(raw, dict):
                continue
            points = _points_of(raw)
            entry: dict[str, Any] = {
                "name": str(raw.get("name") or metric_def.name),
                "summary": summarize(points),
                "points": points,
            }
            if len(projects) > 1:
                entry["project"] = project["name"]
            series.append(entry)

    series.sort(key=lambda s: _series_rank(s["summary"]), reverse=True)
    notes: list[str] = []
    if len(series) > max_series:
        notes.append(
            f"{len(series) - max_series} of {len(series)} series omitted (largest "
            f"{max_series} kept, ranked by total)."
        )
        series = series[:max_series]
    for entry in series:
        points = entry["points"]
        if len(points) > max_points:
            entry["points_omitted"] = len(points) - max_points
            entry["points"] = points[-max_points:]
        entry["points"] = [[t, v] for t, v in entry["points"]]
    if any("points_omitted" in s for s in series):
        notes.append(
            "Some series show only their most recent points; each `summary` still "
            "covers the whole window."
        )
    if metric_def.multi_series and breakdown is None:
        notes.append(f"metric {metric_def.name}: {metric_def.multi_series}.")

    payload: dict[str, Any] = {
        "metric": metric_def.name,
        "metric_type": metric_def.wire,
        "interval": wire_interval,
        "interval_start": _iso(window_start),
        "interval_end": _iso(window_end),
        "projects": projects,
        "series": series,
    }
    if breakdown is not None:
        payload["breakdown"] = {"field": breakdown, "key": breakdown_key, "sub_metric": sub_metric}
    if filters:
        payload["filters"] = filters
    if widget_record is not None and dashboard_record is not None:
        payload["replayed"] = {
            "dashboard": dashboard_record.get("name"),
            "dashboard_id": dashboard_record.get("id"),
            "widget_id": widget_record["id"],
            "widget_title": widget_record.get("title"),
        }
    if not series:
        notes.append(
            "No series returned — the projects have no matching data in this window "
            "(a breakdown only returns buckets that have data)."
        )
    if notes:
        payload["notes"] = notes

    text = compact_json(payload)
    header = (
        f"[chart_data: metric={metric_def.name} source={source} "
        f"projects={len(projects)} interval={wire_interval} "
        f"window={_iso(window_start)}/{_iso(window_end)} series={len(series)} "
        f"tokens~{estimate_tokens(text)}]"
    )
    return f"{header}\n{text}"


__all__ = [
    "DEFAULT_MAX_POINTS",
    "DEFAULT_MAX_SERIES",
    "MAX_PROJECTS",
    "OpikChartClient",
    "auto_interval",
    "build_metric_request",
    "parse_window",
    "query_from_widget",
    "resolve_window",
    "run_chart_data",
    "summarize",
]
