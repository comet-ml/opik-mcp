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

from asyncio import gather
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import (
    SDK_SOURCE_CLAUSE,
    SOURCE_DEFAULTED_ENTITIES,
    compile_filters,
    render_filters,
)
from opik_mcp.read_list.project_scope import require_project_id
from opik_mcp.read_list.window import (
    floor_to_second,
    parse_bound,
    second_precision,
)
from opik_mcp.read_list.window import resolve_window as window_bounds

# --- what can be asked for ------------------------------------------------ #


@dataclass(frozen=True)
class Metric:
    """One chartable metric.

    ``entity`` decides which filter fields apply and which of the backend's
    three filter arrays the compiled filter goes into — a span metric is
    filtered by span fields, not trace fields.

    ``name`` and ``family`` are carried rather than recovered from the dict
    key: the name was being found by scanning ``METRICS`` for the value, and
    the family by ``name.split("_", 1)[-1]``, which worked only because the
    naming happens to be regular. Both are facts about the metric, so the
    metric holds them.
    """

    name: str
    backend: str
    entity: str
    unit: str
    family: str
    """What the metric measures, independent of the entity — ``count``,
    ``cost``, ``error_rate``. Two metrics of the same family answer the same
    question about different entities, which is what lets a refused grouping
    point at the one that accepts it."""


def _metric(name: str, backend: str, entity: str, unit: str, family: str) -> Metric:
    return Metric(name=name, backend=backend, entity=entity, unit=unit, family=family)


# Agent-facing names are consistently prefixed. The backend's own enum has a
# bare DURATION and COST next to SPAN_COST and THREAD_COST, which reads as a
# trap: "cost" looks global and is not. The frontend renames the same two for
# the same reason.
METRICS: Final[dict[str, Metric]] = {
    metric.name: metric
    for metric in (
        _metric("trace_count", "TRACE_COUNT", "trace", "traces", "count"),
        _metric("trace_duration", "DURATION", "trace", "ms", "duration"),
        _metric(
            "trace_average_duration", "TRACE_AVERAGE_DURATION", "trace", "ms", "average_duration"
        ),
        _metric("trace_error_rate", "TRACE_ERROR_RATE", "trace", "%", "error_rate"),
        _metric("trace_cost", "COST", "trace", "USD", "cost"),
        _metric("trace_token_usage", "TOKEN_USAGE", "trace", "tokens", "token_usage"),
        _metric("trace_feedback_scores", "FEEDBACK_SCORES", "trace", "score", "feedback_scores"),
        _metric(
            "guardrails_failed_count", "GUARDRAILS_FAILED_COUNT", "trace", "failures", "guardrails"
        ),
        _metric("span_count", "SPAN_COUNT", "span", "spans", "count"),
        _metric("span_duration", "SPAN_DURATION", "span", "ms", "duration"),
        _metric("span_average_duration", "SPAN_AVERAGE_DURATION", "span", "ms", "average_duration"),
        _metric("span_error_rate", "SPAN_ERROR_RATE", "span", "%", "error_rate"),
        _metric("span_cost", "SPAN_COST", "span", "USD", "cost"),
        _metric("span_token_usage", "SPAN_TOKEN_USAGE", "span", "tokens", "token_usage"),
        _metric("span_feedback_scores", "SPAN_FEEDBACK_SCORES", "span", "score", "feedback_scores"),
        _metric("thread_count", "THREAD_COUNT", "thread", "threads", "count"),
        _metric("thread_duration", "THREAD_DURATION", "thread", "ms", "duration"),
        _metric(
            "thread_average_duration", "THREAD_AVERAGE_DURATION", "thread", "ms", "average_duration"
        ),
        _metric("thread_cost", "THREAD_COST", "thread", "USD", "cost"),
        _metric(
            "thread_feedback_scores", "THREAD_FEEDBACK_SCORES", "thread", "score", "feedback_scores"
        ),
    )
}

_FILTER_ARRAY: Final = {
    "trace": "trace_filters",
    "span": "span_filters",
    "thread": "thread_filters",
}

# --- grouping ------------------------------------------------------------- #
#
# The compatibility rules are transcribed from ``BreakdownField`` rather than
# discovered by trial, because the backend's refusals are unusable: ask to
# group SPAN_COST by model and it answers "This field supports Span metrics
# only" — about a span metric. The cause is that its SPAN_METRICS set omits
# SPAN_COST, SPAN_AVERAGE_DURATION and SPAN_ERROR_RATE, and TRACE_METRICS omits
# TRACE_AVERAGE_DURATION and TRACE_ERROR_RATE, and THREAD_METRICS omits
# THREAD_AVERAGE_DURATION and THREAD_COST. Seven of the twenty metrics
# therefore support no grouping at all. Filed as a backend bug; until it is
# fixed, saying so plainly here beats a round trip for a self-contradiction.

_GROUPABLE_TRACE: Final = frozenset(
    {
        "trace_duration",
        "trace_count",
        "trace_token_usage",
        "trace_cost",
        "trace_feedback_scores",
        "guardrails_failed_count",
    }
)
_GROUPABLE_SPAN: Final = frozenset(
    {"span_count", "span_duration", "span_token_usage", "span_feedback_scores"}
)
_GROUPABLE_THREAD: Final = frozenset({"thread_count", "thread_duration", "thread_feedback_scores"})

BREAKDOWNS: Final[dict[str, str]] = {
    "tags": "TAGS",
    "name": "NAME",
    "error_info": "ERROR_INFO",
    "error_type": "ERROR_TYPE",
    "model": "MODEL",
    "provider": "PROVIDER",
    # The backend calls it TYPE; "type" alone would read as the metric's type.
    "span_type": "TYPE",
    "guardrail_name": "GUARDRAIL_NAME",
    "metadata": "METADATA",
}

_METADATA_PREFIX: Final = "metadata."

BACKEND_SERIES_CAP: Final = 10
"""The backend's own limit on groups (``BreakdownQueryBuilder.LIMIT``).

Documented rather than enforced here — the point is that a short list of
groups is the backend truncating, not the project having only that many.
"""


def _allows(breakdown: str, metric_name: str) -> bool:
    """Transcribed from ``BreakdownField.isCompatibleWith``."""
    if breakdown == "tags":
        return metric_name in _GROUPABLE_TRACE | _GROUPABLE_SPAN | _GROUPABLE_THREAD
    if breakdown in ("metadata", "name", "error_info", "error_type"):
        return metric_name in _GROUPABLE_TRACE | _GROUPABLE_SPAN
    if breakdown in ("model", "provider", "span_type"):
        return metric_name in _GROUPABLE_SPAN
    if breakdown == "guardrail_name":
        return metric_name == "guardrails_failed_count"
    return False


def groupable_by(metric_name: str) -> list[str]:
    """The breakdowns this metric accepts — possibly none."""
    return [name for name in BREAKDOWNS if _allows(name, metric_name)]


# --- picking one of a metric's series ------------------------------------- #
#
# Three families answer with several series at once rather than one number per
# bucket: a duration comes back as p50/p90/p99, a feedback-score metric as one
# series per score name, a token metric as one per usage key. The backend
# cannot fan those out *and* group them, so grouping one of them has to say
# which of its series is being grouped (``BreakdownConfigValidator``). The
# widget does the same — it hides the grouping control until exactly one is
# selected.
#
# Without a way to say it, grouping was a dead end for eight of the thirteen
# groupable metrics: the backend answered "sub_metric is required …" and there
# was no argument to comply with. Hence ``series`` — one argument rather than
# one per family, because at most one of the three can apply at a time.

SERIES_CHOOSING_FAMILIES: Final = frozenset({"duration", "feedback_scores", "token_usage"})

DURATION_PERCENTILES: Final = ("p50", "p90", "p99")

DEFAULT_SERIES: Final[dict[str, str]] = {
    "duration": "p50",
    # The sum over the other keys, so it is the series a "tokens by model"
    # question means. A project that does not record it charts empty rather
    # than wrong, and the header names the key that was charted either way.
    "token_usage": "total_tokens",
}
"""What a grouped metric charts when the caller did not choose.

Only where a default is honest. A feedback-score metric has none: the names
are the project's own, no one of them is the obvious subject, and choosing
silently would answer a different question than the one asked.
"""


def series_choices(metric: Metric) -> str:
    """Where the values for ``series`` come from, for this metric."""
    if metric.family == "duration":
        return f"one of {', '.join(DURATION_PERCENTILES)}"
    if metric.family == "token_usage":
        return "a usage key, as listed in read('project', …) under vocabulary"
    return "a feedback score name, as listed in read('project', …) under vocabulary"


def resolve_series(metric: Metric, series: str | None, *, grouped: bool) -> str | None:
    """The ``sub_metric`` to send, defaulted where a default is honest.

    Refuses rather than ignores in both directions: a ``series`` the metric has
    no use for would otherwise silently chart something else, and a grouping
    that needs one would come back as a backend 422 the agent cannot act on.
    """
    chosen = series.strip() if series else None
    needs = grouped and metric.family in SERIES_CHOOSING_FAMILIES

    if chosen and not needs:
        why = (
            f"{metric.name} is a single series — it has no sub-series to choose."
            if metric.family not in SERIES_CHOOSING_FAMILIES
            else (
                f"Ungrouped, {metric.name} already returns every series it has "
                "(one row per bucket, one column each). series only narrows a "
                "grouped chart, where the backend can return one series at a time."
            )
        )
        raise EntityArgValidationError(f"series={series!r} does not apply here: {why}")

    if not needs:
        return None

    if chosen is None:
        default = DEFAULT_SERIES.get(metric.family)
        if default is None:
            raise EntityArgValidationError(
                f"Grouping {metric.name} charts one score at a time, so it needs "
                f"series=<score name> — {series_choices(metric)}, or "
                "list('score_name', project_id=…)."
            )
        return default

    if metric.family == "duration" and chosen.lower() not in DURATION_PERCENTILES:
        raise EntityArgValidationError(
            f"Unknown series {series!r} for {metric.name}: a duration is grouped one "
            f"percentile at a time, {series_choices(metric)}."
        )
    return chosen.lower() if metric.family == "duration" else chosen


def _same_question_elsewhere(breakdown: str, metric_name: str) -> str | None:
    """A metric of the same family that does accept this grouping.

    "Cost per model" is the obvious ask and ``trace_cost`` cannot answer it;
    ``span_cost`` is where per-model cost lives — except that one is in the
    backend's omitted set too, so sometimes the honest answer is that the
    question is currently unanswerable. Only a candidate that works is
    suggested.

    Matched on ``Metric.family`` rather than on a suffix of the name. The
    suffix test made the suggestion depend on dict order: ``trace_count`` →
    ``model`` landed on ``span_count`` only because ``guardrails_failed_count``,
    which also ends in ``_count`` and comes earlier, happens not to be
    span-groupable.
    """
    asked = METRICS[metric_name]
    for candidate in METRICS.values():
        if (
            candidate.name != metric_name
            and candidate.family == asked.family
            and _allows(breakdown, candidate.name)
        ):
            return candidate.name
    return None


def _where_else(breakdown: str, metric_name: str) -> str:
    """Where the refused question *can* be asked — or that it cannot be.

    A refusal that only says no costs a second refusal: told `trace_cost`
    cannot be grouped by model, the obvious next move is `span_cost`, which is
    in the backend's omitted set and refuses too. Two round trips to learn one
    fact. So the refusal answers the follow-up as well — either naming the
    metric that does take this grouping, or saying plainly that no metric of
    this kind does and pointing at the ones that accept the field.
    """
    instead = _same_question_elsewhere(breakdown, metric_name)
    if instead:
        return f" For this grouping, use metric_type='{instead}'."
    kind = METRICS[metric_name].family.replace("_", " ")
    takers = [name for name in METRICS if _allows(breakdown, name)]
    where = f" Metrics that do accept {breakdown}: {', '.join(takers)}." if takers else ""
    return f" No {kind} metric can be grouped by {breakdown}, so there is nothing to retry.{where}"


def parse_breakdown(breakdown: str, metric_name: str) -> dict[str, str]:
    """Resolve ``breakdown`` for a metric, or refuse with the reason.

    Metadata takes its key inline — ``breakdown='metadata.environment'`` —
    rather than through a second parameter: one argument instead of two, and
    two arguments that only make sense together are two chances to pass one
    without the other.
    """
    asked = breakdown.strip().lower()
    key: str | None = None
    if asked.startswith(_METADATA_PREFIX):
        key = breakdown.strip()[len(_METADATA_PREFIX) :]
        asked = "metadata"
        if not key:
            raise EntityArgValidationError(
                "breakdown='metadata.<key>' needs the key to group by, "
                "e.g. breakdown='metadata.environment'."
            )
    elif asked == "metadata":
        raise EntityArgValidationError(
            "Grouping by metadata needs a key: breakdown='metadata.<key>', "
            "e.g. breakdown='metadata.environment'."
        )

    if asked not in BREAKDOWNS:
        raise EntityArgValidationError(
            f"Unknown breakdown {breakdown!r}. One of: "
            f"{', '.join(name for name in BREAKDOWNS if name != 'metadata')}, "
            "metadata.<key>."
        )

    if not _allows(asked, metric_name):
        accepted = groupable_by(metric_name)
        if not accepted:
            raise EntityArgValidationError(
                f"{metric_name} cannot be grouped at all — it is one of the seven "
                "metrics missing from the backend's grouping sets "
                f"({', '.join(_ungroupable())}). Chart it plain, or group a "
                "related metric that does support it."
            )
        raise EntityArgValidationError(
            f"{metric_name} cannot be grouped by {asked}. It accepts: "
            f"{', '.join(accepted)}.{_where_else(asked, metric_name)}"
        )

    resolved = {"field": BREAKDOWNS[asked]}
    if key is not None:
        resolved["metadata_key"] = key
    return resolved


def _ungroupable() -> list[str]:
    return [name for name in METRICS if not groupable_by(name)]


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
    span = parse_bound(until) - parse_bound(since)
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
        # Round the span DOWN to whole days so the suggestion is expressible as
        # `since='<n>d'`, then quote the row count that span really produces —
        # not MAX_BUCKETS. Saying "200 rows" for a request that returns 193 is
        # a number the agent would repeat back to a person.
        days = (width * (MAX_BUCKETS - 1)) // timedelta(days=1)
        if days >= 1:
            narrowed = second_precision(parse_bound(until) - timedelta(days=days))
            rows = bucket_count(interval, narrowed, until)
            out.append(f"since='{days}d' at interval='{interval}' → {_rows(rows)}")
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
    end = floor_to_second(parse_bound(resolved_until) if resolved_until else anchor)
    start = (
        floor_to_second(parse_bound(resolved_since))
        if resolved_since
        else end - timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    return second_precision(start), second_precision(end)


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


def _time_label(raw: Any, interval: str, *, until: str | None = None) -> str:
    """Buckets are labelled at the precision they mean.

    A daily bucket labelled with a time implies a precision it does not have,
    and it costs eleven characters a row to imply it.

    ``total`` is the case worth care: the backend labels its single bucket with
    the window's *start*, so passing that through reads as "on the 3rd there
    were 5" when the number is the whole week's. It gets the span instead.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    if interval == "total":
        return f"{raw[:10]} → {until[:10]}" if until else f"since {raw[:10]}"
    return raw[:16].replace("T", " ") if interval == "hourly" else raw[:10]


AMBIGUOUS_ZERO_FAMILIES: Final = frozenset({"error_rate", "average_duration"})
"""Families whose ``0`` does not mean zero.

An error rate over a bucket with no traces is not 0% — there was nothing to
fail. The backend sends 0 for both, so a quiet Tuesday and a perfect Tuesday
render identically, and "error rate 0" is the more dangerous of the two to
believe. ``read('project')`` already refuses to print a rate over a period
with no traces; the series is the same claim, one bucket at a time.

None of these five metrics can be grouped (they are in the backend's omitted
sets), so the companion count is always a single ungrouped series and lines up
with the rows bucket for bucket.
"""


@dataclass(frozen=True, slots=True)
class Presence:
    """How many of the metric's entity fell in each bucket."""

    entity: str
    counts: list[float]

    def empty(self, row: int) -> bool:
        return row < len(self.counts) and not self.counts[row]

    def nothing_at_all(self) -> bool:
        return not any(self.counts)


def companion_count(metric: Metric) -> Metric | None:
    """The count metric that says whether a bucket had anything in it."""
    if metric.family not in AMBIGUOUS_ZERO_FAMILIES:
        return None
    return METRICS.get(f"{metric.entity}_count")


def bucket_counts(body: dict[str, Any]) -> list[float]:
    """The count series' values, in bucket order."""
    raw = body.get("results")
    series = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    if not series or not isinstance(series[0].get("data"), list):
        return []
    return [
        float(point.get("value") or 0) for point in series[0]["data"] if isinstance(point, dict)
    ]


def _all_zero(series: list[dict[str, Any]]) -> bool:
    """True when not one point in any series carries a non-zero value.

    Thirty-one rows of ``| 0`` cost 148 tokens to say nothing happened, and a
    cost or error-rate question on a quiet project produces exactly that. The
    answer is the same either way; only one of the two is worth paying for.
    """
    return all(
        not point.get("value") for one in series for point in one["data"] if isinstance(point, dict)
    )


MAX_SERIES: Final = 10
"""Columns kept, matching the backend's own cap on breakdown groups.

Grouping is capped server-side, but the feedback-score and token-usage metrics
fan out into one series per score name or usage key with no grouping asked for
at all, and nothing bounds *that* — a project with sixty score names would
otherwise return sixty columns. Unlike the bucket count, this width cannot be
known before the call, so it is capped on the way out: the widest series are
kept (a change hides in the big ones), the true count is stated, and the read
says where the full list of names lives.
"""


def _point(one: dict[str, Any], row: int) -> dict[str, Any]:
    points = one["data"]
    return points[row] if row < len(points) and isinstance(points[row], dict) else {}


def _series_weight(one: dict[str, Any]) -> float:
    """Total magnitude across the window — the series most likely to matter."""
    return sum(
        abs(float(point["value"]))
        for point in one["data"]
        if isinstance(point, dict) and isinstance(point.get("value"), (int, float))
    )


def render(
    body: dict[str, Any],
    *,
    metric_name: str,
    interval: str,
    names_source: str | None = None,
    until: str | None = None,
    presence: Presence | None = None,
) -> str:
    """The series as ``time | <series> …``, one row per bucket.

    Series are columns rather than repeated blocks, which is the whole reason
    this is a table: a bucket's timestamp is printed once per row instead of
    once per point.

    ``presence`` blanks the buckets that had nothing in them, for the metrics
    whose zero would otherwise read as a measurement.
    """
    raw = body.get("results")
    series = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    series = [s for s in series if isinstance(s.get("data"), list)]
    if not series:
        return f"No {metric_name} data in this window."

    if presence is not None and presence.nothing_at_all():
        return (
            f"No {presence.entity}s in this window, so {metric_name} has no value in it. "
            "(The backend reports 0 for an empty bucket; there was nothing to measure.)"
        )

    if _all_zero(series):
        # The window is already on the header line, so the table would add
        # nothing but its own length. Seen live: 31 rows of "| 0" for a cost
        # question on a project with no cost.
        return f"No {metric_name} recorded in this window — every bucket is zero."

    total_series = len(series)
    if total_series > MAX_SERIES:
        series = sorted(series, key=_series_weight, reverse=True)[:MAX_SERIES]

    # Buckets come back identical across series, so the first defines the rows.
    times = [point.get("time") for point in series[0]["data"] if isinstance(point, dict)]
    columns = [str(s.get("name") or metric_name) for s in series]

    lines = [" | ".join(["time", *columns])]
    quiet_rows = 0
    empty_rows = 0
    for row, when in enumerate(times):
        if presence is not None and presence.empty(row):
            # Not blanked in place: a row of empty cells costs as much to print
            # as a real one and carries less. Seen live — a 14-day error rate
            # on a project used one day was 13 rows of "| " for 103 tokens.
            quiet_rows += 1
            continue
        values = [_point(one, row).get("value") for one in series]
        if all(value is None for value in values):
            # The backend's own "no data here" — a null, not a zero. Seen live:
            # nine days of `duration.p50 | duration.p90 | duration.p99` with one
            # day of numbers and eight rows of ` |  | `.
            empty_rows += 1
            continue
        lines.append(
            " | ".join([_time_label(when, interval, until=until), *(_number(v) for v in values)])
        )

    for skipped, why in ((quiet_rows, "quiet"), (empty_rows, "empty")):
        if not skipped:
            continue
        reason = (
            f"had no {presence.entity}s — nothing to measure, which is not the same as zero"
            if why == "quiet" and presence is not None
            else f"recorded no {metric_name}"
        )
        lines.append("")
        lines.append(f"{skipped} of {len(times)} buckets {reason}, and are not listed.")

    if total_series > MAX_SERIES:
        where = f" All {total_series} names: {names_source}." if names_source else ""
        lines.append("")
        lines.append(
            f"The {MAX_SERIES} largest of {total_series} series, by total over the window.{where}"
        )
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
        "multi_series": {
            "which": {
                "duration": f"one series per percentile ({', '.join(DURATION_PERCENTILES)})",
                "feedback_scores": "one series per score name",
                "token_usage": "one series per usage key",
            },
            "ungrouped": "every series is returned, one column each",
            "grouped": (
                "the backend charts one of them at a time, so series=<name> picks it: "
                "a percentile, a score name, or a usage key. Defaults where a default "
                f"is honest ({', '.join(f'{k}={v}' for k, v in DEFAULT_SERIES.items())}), "
                "and the chosen one is echoed in the header. A feedback-score metric "
                "has no default — the names are the project's own, and read('project') "
                "lists them."
            ),
        },
        "breakdowns": {
            "syntax": ("breakdown='<field>', or 'metadata.<key>' to group by a metadata key"),
            "by_metric": {name: groupable_by(name) or None for name in METRICS},
            "series_cap": MAX_SERIES,
            "backend_group_cap": BACKEND_SERIES_CAP,
            "note": (
                "seven metrics accept no grouping at all — they are missing from the "
                "backend's own compatibility sets (BreakdownField), which is a backend "
                "bug rather than a rule: asking anyway returns a message claiming the "
                "field 'supports Span metrics only' about a span metric. Refused "
                "locally instead. Grouped series are capped by the backend at "
                f"{BACKEND_SERIES_CAP}; a metric that fans out per score name or usage "
                f"key is capped here at {MAX_SERIES}, widest first, with the true count "
                "reported."
            ),
        },
        "not_supported": {
            "page, size": "rows are time buckets, not records",
            "sort": "rows are ordered by time",
        },
    }


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
    breakdown: str | None = None,
    series: str | None = None,
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

    name = metric.name
    grouping = parse_breakdown(breakdown, name) if breakdown else None
    chosen = resolve_series(metric, series, grouped=grouping is not None)
    if grouping is not None and chosen is not None:
        grouping["sub_metric"] = chosen

    resolved = await require_project_id(
        client,
        project_id=project_id,
        project_name=project_name,
        caller="list('project_metric')",
    )

    def ask(which: Metric, group: dict[str, str] | None) -> Coroutine[Any, Any, dict[str, Any]]:
        return client.get_project_metrics(
            resolved,
            **request_body(
                which,
                interval=interval_name,
                since=window_since,
                until=window_until,
                clauses=clauses,
                breakdown=group,
            ),
        )

    # A rate or an average needs to know which buckets were empty, and the
    # backend will not say — so the count rides along on the same connection,
    # concurrently. If it fails the series is still rendered: a chart with an
    # ambiguous zero beats no chart, and the note is simply absent.
    counting = companion_count(metric)
    answers = await gather(
        ask(metric, grouping),
        *([ask(counting, None)] if counting else []),
        return_exceptions=True,
    )
    body = answers[0]
    if isinstance(body, BaseException):
        raise body
    presence = None
    if counting is not None and not isinstance(answers[1], BaseException):
        presence = Presence(entity=counting.entity, counts=bucket_counts(answers[1]))

    applied = [name, interval_name, f"{window_since} → {window_until}"]
    if clauses:
        applied.append(f"filters: {render_filters(metric.entity, clauses)}")
    if breakdown:
        grouped_by = f"by {breakdown.strip().lower()}"
        # The chosen series is echoed with the grouping because it changes what
        # the numbers are, and one of the two is a default the caller never
        # typed.
        applied.append(f"{grouped_by} ({chosen})" if chosen else grouped_by)
    header = f"[list: project_metric | {' | '.join(applied)}]"
    # A score or usage metric fans out one series per name, so a truncated
    # width points at the list that enumerates them; a grouped one is already
    # capped by the backend and has no such list.
    names_source = (
        f"list('score_name', project_id='{resolved}')"
        if grouping is None and name.endswith("feedback_scores")
        else None
    )
    table = render(
        body,
        metric_name=name,
        interval=interval_name,
        names_source=names_source,
        until=window_until,
        presence=presence,
    )
    return f"{header}\n{table}"


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
    "BACKEND_SERIES_CAP",
    "BREAKDOWNS",
    "DEFAULT_INTERVAL",
    "DEFAULT_SERIES",
    "DEFAULT_WINDOW_DAYS",
    "DURATION_PERCENTILES",
    "INTERVALS",
    "MAX_BUCKETS",
    "MAX_SERIES",
    "METRICS",
    "Metric",
    "Presence",
    "bucket_count",
    "bucket_counts",
    "check_size",
    "companion_count",
    "groupable_by",
    "parse_breakdown",
    "parse_interval",
    "parse_metric",
    "reference",
    "render",
    "request_body",
    "resolve_series",
    "resolve_window",
    "series_choices",
]
