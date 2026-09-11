"""What can be asked of a project metric, and every refusal that needs no call.

The project overview answers "how is it going" with four numbers. The metric
series answers "when did it change", which is the question that follows. This
file is the vocabulary of that question: the twenty metrics, which entity each
one is about, the groupings the backend accepts for each, the intervals, the
windows, and the size a given request would come back as.

Everything here is a decision that can be made without contacting anything,
which is the point. The backend's own refusals for these are unusable: ask to
group ``span_cost`` by model and it answers "This field supports Span metrics
only", about a span metric. So the compatibility rules are transcribed from
``BreakdownField`` and applied here, and a request that cannot work is refused
with the reason and the nearest thing that does.

The three files next door take it from here: ``runner`` for the order of
operations, ``table`` for how an answer reads, ``reference`` for what
``schema()`` says when asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import (
    SOURCE_DEFAULTED_ENTITIES,
)
from opik_mcp.read_list.window import (
    closed_window,
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
                f"related metric.{_where_else(asked, metric_name)}"
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


INTERVALS: Final = ("hourly", "daily", "weekly", "total")
"""The backend's ``TimeInterval`` values, lowercased. ``total`` collapses the
window to one bucket."""

DEFAULT_WINDOW_DAYS: Final = 7


# --- which interval, when the caller names none --------------------------- #
#
# There is no cap on how many buckets an answer may have: the caller can see a
# large answer and narrow it, and a refusal they cannot see past reads as the
# server being broken. What keeps the default small is the UI's rule — the
# interval follows the window, so a default chart is a few dozen points at any
# range — and an explicit ``interval`` is the caller's decision, sent as given.


def interval_for_window(since: str, until: str) -> str:
    """The interval the Metrics tab would chart this window at.

    ``calculateIntervalType`` in the frontend: the difference in whole days —
    truncated, as dayjs's ``diff(…, 'days')`` truncates — is hourly up to 3,
    daily up to 30, weekly beyond. So ``since='1h'`` charts by the hour rather
    than as a single daily bucket, the 7-day default stays daily, and a year
    is 52 rows instead of 365.
    """
    days = (parse_bound(until) - parse_bound(since)).days
    if days <= 3:
        return "hourly"
    if days <= 30:
        return "daily"
    return "weekly"


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
    start, end = closed_window(resolved_since, resolved_until, days=DEFAULT_WINDOW_DAYS, now=anchor)
    return second_precision(start), second_precision(end)


# --- the request ---------------------------------------------------------- #
#
# The metrics endpoint filters a thread metric with ClickHouse's thread
# strategy, whose field set (``FilterQueryBuilder``) has neither ``source``
# nor ``environment`` — and an unsupported field is dropped from the query
# without a word. So the SDK default cannot be applied to a thread metric
# (the header would claim a filter that never reached the database, and the
# number would not reconcile with ``read('project')``, which really is
# SDK-filtered), and a caller who names one of the two fields is told rather
# than quietly given the unfiltered answer. Trace and span metrics use
# strategies that do carry both.

SOURCE_FILTERED_METRIC_ENTITIES: Final = tuple(
    entity for entity in SOURCE_DEFAULTED_ENTITIES if entity != "thread"
)

_DROPPED_BY_THREAD_METRICS: Final = ("source", "environment")


def refuse_dropped_fields(metric: Metric, clauses: list[dict[str, str]]) -> None:
    if metric.entity != "thread":
        return
    named = [clause["field"] for clause in clauses if clause["field"] in _DROPPED_BY_THREAD_METRICS]
    if not named:
        return
    raise EntityArgValidationError(
        f"{metric.name} cannot be filtered by {', '.join(named)}: the metrics endpoint "
        "applies thread filters with a field set that has neither, and it drops what it "
        "does not support instead of failing — so the answer would be unfiltered while "
        "the header claimed otherwise. Filter a trace metric by "
        f"{named[0]}, or filter this one by status, duration or tags."
    )


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


AMBIGUOUS_ZERO_FAMILIES: Final = frozenset({"error_rate", "average_duration"})
"""Families whose ``0`` does not mean zero.

An error rate over a bucket with no traces is not 0% — there was nothing to
fail. The backend sends 0 for both, so a quiet Tuesday and a perfect Tuesday
render identically, and "error rate 0" is the more dangerous of the two to
believe. ``read('project')`` already refuses to print a rate over a period
with no traces; the series is the same claim, one bucket at a time.

None of these five metrics can be grouped (they are in the backend's omitted
sets), so the companion count is always a single, filled, ungrouped series —
but it is still matched to the metric's rows by timestamp rather than by
position, because it is a separate query and nothing promises the two agree.
"""


def companion_count(metric: Metric) -> Metric | None:
    """The count metric that says whether a bucket had anything in it."""
    if metric.family not in AMBIGUOUS_ZERO_FAMILIES:
        return None
    return METRICS.get(f"{metric.entity}_count")


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


def parse_interval(interval: str | None, *, since: str, until: str) -> str:
    """The caller's interval, or the window's when they named none."""
    if interval is None:
        return interval_for_window(since, until)
    name = interval.strip().lower()
    if name not in INTERVALS:
        raise EntityArgValidationError(
            f"Unknown interval {interval!r}. One of: {', '.join(INTERVALS)}."
        )
    return name


__all__ = [
    "BREAKDOWNS",
    "DEFAULT_SERIES",
    "DURATION_PERCENTILES",
    "INTERVALS",
    "METRICS",
    "Metric",
    "companion_count",
    "groupable_by",
    "interval_for_window",
    "parse_breakdown",
    "parse_interval",
    "parse_metric",
    "refuse_dropped_fields",
    "request_body",
    "resolve_series",
    "resolve_window",
]
