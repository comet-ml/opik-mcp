"""``list('project_metric', …)`` — one metric over time, as a table.

The project overview answers "how is it going" with four numbers. This answers
"when did it change", which is the question that follows. One backend call
returns one metric bucketed over a window, and it arrives as a table of time
buckets because the same data as nested JSON costs four times the tokens
(measured: a 30-day daily series with ten groups is 3,649 tokens as JSON and
871 as a table).

It rides on ``list`` rather than a tool of its own: the query language, the
window vocabulary, the project scope and the table renderer already live
there, and a sixth tool would sit in the context of every request forever —
including the ones that never touch Opik. That follows ADR 0004's "one schema
covers N entities, small tools/list footprint".

But a time series is not a collection, so the collection machinery does not
apply to it: rows are buckets rather than records, ``page``/``size``/``sort``
are meaningless, and the filter fields depend on which entity the *metric* is
about rather than on the entity type named in the call. Hence this module,
which ``list`` delegates to whole instead of growing six special cases in the
path the other ten entities share.

**Size is bounded before the request, not after.** The backend emits every
bucket in the window including the empty ones, so an hourly month is 721
buckets per series — enough to swallow a context window in one call, paid for
by the user. The bucket count follows from the interval and the window alone,
so an over-large request is refused without contacting the backend at all, and
the refusal names the narrower requests that would fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import (
    SOURCE_DEFAULTED_ENTITIES,
    compile_filters,
    render_filters,
)
from opik_mcp.read_list.project_scope import require_project_id
from opik_mcp.read_list.window import resolve_window as window_bounds

# --- what can be asked for ------------------------------------------------ #


@dataclass(frozen=True)
class Metric:
    """One chartable metric: its backend name and the entity it is about.

    ``entity`` decides which filter fields apply and which of the backend's
    three filter arrays the compiled filter goes into — a span metric is
    filtered by span fields, not trace fields.
    """

    backend: str
    entity: str
    unit: str


# Agent-facing names are consistently prefixed. The backend's own enum has a
# bare DURATION and COST next to SPAN_COST and THREAD_COST, which reads as a
# trap: "cost" looks global and is not. The frontend renames the same two for
# the same reason.
METRICS: Final[dict[str, Metric]] = {
    "trace_count": Metric("TRACE_COUNT", "trace", "traces"),
    "trace_duration": Metric("DURATION", "trace", "ms"),
    "trace_average_duration": Metric("TRACE_AVERAGE_DURATION", "trace", "ms"),
    "trace_error_rate": Metric("TRACE_ERROR_RATE", "trace", "%"),
    "trace_cost": Metric("COST", "trace", "USD"),
    "trace_token_usage": Metric("TOKEN_USAGE", "trace", "tokens"),
    "trace_feedback_scores": Metric("FEEDBACK_SCORES", "trace", "score"),
    "guardrails_failed_count": Metric("GUARDRAILS_FAILED_COUNT", "trace", "failures"),
    "span_count": Metric("SPAN_COUNT", "span", "spans"),
    "span_duration": Metric("SPAN_DURATION", "span", "ms"),
    "span_average_duration": Metric("SPAN_AVERAGE_DURATION", "span", "ms"),
    "span_error_rate": Metric("SPAN_ERROR_RATE", "span", "%"),
    "span_cost": Metric("SPAN_COST", "span", "USD"),
    "span_token_usage": Metric("SPAN_TOKEN_USAGE", "span", "tokens"),
    "span_feedback_scores": Metric("SPAN_FEEDBACK_SCORES", "span", "score"),
    "thread_count": Metric("THREAD_COUNT", "thread", "threads"),
    "thread_duration": Metric("THREAD_DURATION", "thread", "ms"),
    "thread_average_duration": Metric("THREAD_AVERAGE_DURATION", "thread", "ms"),
    "thread_cost": Metric("THREAD_COST", "thread", "USD"),
    "thread_feedback_scores": Metric("THREAD_FEEDBACK_SCORES", "thread", "score"),
}

_FILTER_ARRAY: Final = {
    "trace": "trace_filters",
    "span": "span_filters",
    "thread": "thread_filters",
}

INTERVALS: Final[dict[str, timedelta | None]] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    # TOTAL collapses the window to one bucket, so it has no width.
    "total": None,
}

DEFAULT_INTERVAL: Final = "daily"
DEFAULT_WINDOW_DAYS: Final = 7

MAX_BUCKETS: Final = 200
"""Refusal threshold, in buckets.

200 buckets of one series is roughly 1,500 tokens — a large answer but a
usable one. Above that the caller is asking for a picture they cannot read
and did not mean to pay for: an hourly month is 721 buckets. The cap is on
buckets rather than tokens because buckets are knowable before the call, and
a request refused before the call costs nothing at all.
"""


# --- how big would the answer be ------------------------------------------ #


def bucket_count(interval: str, since: str, until: str) -> int:
    """Rows the backend will emit for this interval and window.

    Every bucket in the window is returned, empty ones included, so this is
    exact rather than an estimate. Verified live: a daily week is 8 rows and an
    hourly week is 169 — the window's spans plus the bucket the end falls in.
    """
    width = INTERVALS[interval]
    if width is None:
        return 1
    span = _instant(until) - _instant(since)
    return int(span // width) + 1


def _fitting_alternatives(interval: str, since: str, until: str) -> list[str]:
    """Concrete narrower requests, each with the row count it would produce.

    A refusal that only says "too big" leaves the agent guessing which of three
    knobs to turn and by how much; these are the turns that actually fit.
    """
    out: list[str] = []
    for name in INTERVALS:
        rows = bucket_count(name, since, until)
        if name != interval and rows <= MAX_BUCKETS:
            out.append(f"interval='{name}' → {_rows(rows)}")
    width = INTERVALS[interval]
    if width is not None:
        fits = width * (MAX_BUCKETS - 1)
        days = fits // timedelta(days=1)
        if days >= 1:
            out.append(f"since='{days}d' at interval='{interval}' → {_rows(MAX_BUCKETS)}")
    return out


def _rows(count: int) -> str:
    return "1 row" if count == 1 else f"{count} rows"


def check_size(interval: str, since: str, until: str) -> None:
    """Raise before the request when the answer would be too large to be useful."""
    rows = bucket_count(interval, since, until)
    if rows <= MAX_BUCKETS:
        return
    alternatives = _fitting_alternatives(interval, since, until) or [
        "narrow the window with since/until"
    ]
    raise EntityArgValidationError(
        f"interval='{interval}' over {since} → {until} is {rows} time buckets, "
        f"over the {MAX_BUCKETS}-bucket limit — that answer would cost more "
        f"context than it can inform. Narrow one of:\n"
        + "\n".join(f"  {line}" for line in alternatives)
    )


# --- window --------------------------------------------------------------- #


def resolve_window(
    since: str | None, until: str | None, *, now: datetime | None = None
) -> tuple[str, str]:
    """``(since, until)`` instants from the caller's forms, closed and ordered.

    Takes the same vocabulary as every other list — a relative span or an
    ISO-8601 instant — through the shared resolver, then fills whichever bound
    was left open. One clock reading for both, so a relative ``since`` and an
    implied ``until`` cannot land a second apart: that drift silently cost a
    30-day window its day count once already (see ``project_summary.window``).
    """
    anchor = now or datetime.now(UTC)
    resolved_since, resolved_until = window_bounds(since, until, now=anchor)
    end = _second(_instant(resolved_until) if resolved_until else anchor)
    start = (
        _second(_instant(resolved_since))
        if resolved_since
        else end - timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    return _iso(start), _iso(end)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _second(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return _second(moment).isoformat().replace("+00:00", "Z")


# --- the request ---------------------------------------------------------- #


def request_body(
    metric: Metric,
    *,
    interval: str,
    since: str,
    until: str,
    clauses: list[dict[str, str]],
    breakdown: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Keyword arguments for ``get_project_metrics``.

    The compiled filter goes into the array for the metric's own entity: the
    backend applies ``span_filters`` to spans and ``trace_filters`` to traces,
    so putting a span filter in the trace array silently filters nothing.
    """
    body: dict[str, Any] = {
        "metric_type": metric.backend,
        "interval": interval.upper(),
        "interval_start": since,
        "interval_end": until,
    }
    if clauses:
        body[_FILTER_ARRAY[metric.entity]] = clauses
    if breakdown is not None:
        body["breakdown"] = breakdown
    return body


# --- the answer ----------------------------------------------------------- #


def _number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return f"{round(float(value), 4):g}"


def _time_label(raw: Any, interval: str) -> str:
    """Buckets are labelled at the precision they mean.

    A daily bucket labelled with a time implies a precision it does not have,
    and it costs eleven characters a row to imply it.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    return raw[:16].replace("T", " ") if interval == "hourly" else raw[:10]


def render(body: dict[str, Any], *, metric_name: str, interval: str) -> str:
    """The series as ``time | <series> …``, one row per bucket.

    Series are columns rather than repeated blocks, which is the whole reason
    this is a table: a bucket's timestamp is printed once for every series
    instead of once per series per point.
    """
    raw = body.get("results")
    series = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    series = [s for s in series if isinstance(s.get("data"), list)]
    if not series:
        return f"No {metric_name} data in this window."

    # Buckets come back identical across series, so the first defines the rows.
    times = [point.get("time") for point in series[0]["data"] if isinstance(point, dict)]
    columns = [str(s.get("name") or metric_name) for s in series]

    lines = [" | ".join(["time", *columns])]
    for row, when in enumerate(times):
        cells = [_time_label(when, interval)]
        for one in series:
            points = one["data"]
            point = points[row] if row < len(points) and isinstance(points[row], dict) else {}
            cells.append(_number(point.get("value")))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def parse_metric(metric_type: str | None) -> Metric:
    """Resolve the agent-facing metric name, or say what the choices are."""
    if metric_type is None:
        raise EntityArgValidationError(
            "list('project_metric') needs metric_type — which metric to chart. "
            f"One of: {', '.join(METRICS)}."
        )
    metric = METRICS.get(metric_type.strip().lower())
    if metric is None:
        raise EntityArgValidationError(
            f"Unknown metric_type {metric_type!r}. One of: {', '.join(METRICS)}."
        )
    return metric


def parse_interval(interval: str | None) -> str:
    if interval is None:
        return DEFAULT_INTERVAL
    name = interval.strip().lower()
    if name not in INTERVALS:
        raise EntityArgValidationError(
            f"Unknown interval {interval!r}. One of: {', '.join(INTERVALS)}."
        )
    return name


def reference() -> dict[str, Any]:
    """What ``schema('list.project_metric')`` answers.

    The metric table and the filter fields live here rather than in the tool
    description: the description is billed on every request the host makes,
    this is billed only when asked for.
    """
    return {
        "entity": "project_metric",
        "shape": "a time series, not a collection — rows are time buckets",
        "required": ["project_id or project_name", "metric_type"],
        "metric_types": {
            name: {"about": metric.entity, "unit": metric.unit} for name, metric in METRICS.items()
        },
        "intervals": {
            "hourly": "one row per hour",
            "daily": f"one row per day (default; {DEFAULT_WINDOW_DAYS}-day window by default)",
            "weekly": "one row per week",
            "total": "one row for the whole window",
        },
        "window": "since/until, same forms as every other list; defaults to the last 7 days",
        "filters": (
            "OQL, same language as list('trace'). The fields are those of the entity the "
            "metric is about (see `about` above), and trace/span/thread metrics default to "
            'source = "sdk" like the other lists.'
        ),
        "limits": {
            "buckets": MAX_BUCKETS,
            "why": (
                "every bucket in the window is returned including empty ones, so an hourly "
                "month is 721 rows; a request over the limit is refused before the backend "
                "is called, naming the narrower requests that fit"
            ),
        },
        "not_supported": {
            "page, size": "rows are time buckets, not records",
            "sort": "rows are ordered by time",
        },
    }


SDK_SOURCE_CLAUSE: Final = {"field": "source", "operator": "=", "key": "", "value": "sdk"}


async def run_project_metric(
    client: OpikReadClient,
    *,
    project_id: str | None,
    project_name: str | None,
    metric_type: str | None,
    interval: str | None,
    since: str | None,
    until: str | None,
    filters: str | None,
    page: int | None = None,
    size: int | None = None,
    sort: str | None = None,
) -> str:
    """``list('project_metric', …)`` end to end: validate, ask, render.

    Everything that can be rejected is rejected before the backend is called —
    an unknown metric, an unknown interval, a filter field that does not exist
    on the metric's entity, an answer too wide to be worth reading. The
    backend's own refusals for these are unusable (a bad filter comes back as
    ``Invalid filters query parameter`` with no field named), so local
    validation is not an optimisation, it is the only readable error.
    """
    _refuse_collection_args(page=page, size=size, sort=sort)
    metric = parse_metric(metric_type)
    interval_name = parse_interval(interval)
    window_since, window_until = resolve_window(since, until)
    check_size(interval_name, window_since, window_until)

    clauses = compile_filters(metric.entity, filters or "")
    if metric.entity in SOURCE_DEFAULTED_ENTITIES and not any(
        clause["field"] == "source" for clause in clauses
    ):
        # Same default as the Logs page and the other lists. Echoed as part of
        # the filter rather than called out separately: the filter line already
        # reads `source = "sdk"`, and saying it twice is two claims where the
        # agent has to check they agree.
        clauses.append(dict(SDK_SOURCE_CLAUSE))

    resolved = await require_project_id(
        client,
        project_id=project_id,
        project_name=project_name,
        caller="list('project_metric')",
    )
    body = await client.get_project_metrics(
        resolved,
        **request_body(
            metric,
            interval=interval_name,
            since=window_since,
            until=window_until,
            clauses=clauses,
        ),
    )

    applied = [
        parse_metric_name(metric),
        interval_name,
        f"{window_since} → {window_until}",
    ]
    if clauses:
        applied.append(f"filters: {render_filters(metric.entity, clauses)}")
    header = f"[list: project_metric | {' | '.join(applied)}]"
    return (
        f"{header}\n{render(body, metric_name=parse_metric_name(metric), interval=interval_name)}"
    )


def parse_metric_name(metric: Metric) -> str:
    """The agent-facing name for a resolved metric — for echoing back."""
    for name, candidate in METRICS.items():
        if candidate is metric:
            return name
    return metric.backend.lower()


def _refuse_collection_args(*, page: int | None, size: int | None, sort: str | None) -> None:
    """``page``/``size``/``sort`` mean nothing here, so they are refused.

    Silently ignoring them would let an agent believe it had paged through a
    series it actually re-read from the start, or ordered rows that are ordered
    by time by definition.
    """
    named = [
        name
        for name, value in (("page", page), ("size", size), ("sort", sort))
        if value is not None
    ]
    if not named:
        return
    raise EntityArgValidationError(
        f"list('project_metric') does not take {', '.join(named)}: rows are time "
        "buckets, not records — they are ordered by time and the window and "
        "interval decide how many there are. Use since/until and interval instead."
    )


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_WINDOW_DAYS",
    "INTERVALS",
    "MAX_BUCKETS",
    "METRICS",
    "Metric",
    "bucket_count",
    "check_size",
    "parse_interval",
    "parse_metric",
    "reference",
    "render",
    "request_body",
    "resolve_window",
]
