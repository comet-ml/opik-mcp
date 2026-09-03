"""The chart vocabulary — metrics, breakdowns, intervals, stat metrics.

Single source of truth for "what can a chart be about", shared by the three
surfaces that must agree on it:

- ``charts.spec`` validates an agent's ``ChartSpec`` against it,
- ``charts.query`` builds the ``/metrics`` request body from it,
- ``charts.config`` emits the widget JSON the Opik UI renders from it.

Every table here mirrors a concrete opik-backend rule, cited per entry:
``MetricType``, ``BreakdownField.isCompatibleWith`` and
``BreakdownConfigValidator``. The point of duplicating them is that a bad
combination fails locally with a sentence the model can act on, instead of
round-tripping to a 422 whose message names Java field paths
(``breakdown.subMetric``) the agent has never seen.

The MCP-facing names are the backend enum values lowercased (``trace_count``,
``feedback_scores``), plus a small alias table for the words people actually
use (``latency`` → ``DURATION``). Lowercase because every other identifier on
this tool surface is lowercase; the wire form is upper-cased on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

MetricFamily = Literal["trace", "span", "thread"]
"""Which entity a metric aggregates over. Decides three things: the filter
key on the request (``trace_filters`` / ``span_filters`` / ``thread_filters``),
the filter key in the stored widget config (``traceFilters`` / …), and which
breakdown fields the backend will accept."""

SubMetricKind = Literal["percentile", "feedback_score", "usage_key"]
"""What ``sub_metric`` means for a metric that requires one under a breakdown
(``BreakdownConfigValidator``). ``None`` on a metric means it takes none."""


@dataclass(frozen=True)
class MetricDef:
    """One entry of the metric vocabulary."""

    name: str
    """MCP-facing name (lowercase)."""

    wire: str
    """opik-backend ``MetricType`` enum value."""

    family: MetricFamily

    summary: str
    """One line for the tool description / error messages."""

    breakdownable: bool
    """Whether opik-backend accepts ANY breakdown for this metric.

    Mirrors membership of ``BreakdownField``'s ``TRACE_METRICS`` /
    ``SPAN_METRICS`` / ``THREAD_METRICS`` sets — which are narrower than the
    metric families: ``trace_error_rate`` is a trace metric but is in none of
    those sets, so every breakdown but ``none`` is rejected for it.
    """

    sub_metric: SubMetricKind | None = None
    """Set when a breakdown on this metric requires ``sub_metric``."""

    multi_series: str = ""
    """Noted when the backend returns more than one series for the metric
    (e.g. duration returns p50/p90/p99), so a caller reading the result knows
    the series split is the backend's, not a breakdown."""


_METRIC_LIST: Final[tuple[MetricDef, ...]] = (
    # --- trace metrics --- #
    MetricDef("trace_count", "TRACE_COUNT", "trace", "Number of traces per bucket.", True),
    MetricDef(
        "duration",
        "DURATION",
        "trace",
        "Trace latency percentiles (ms).",
        True,
        sub_metric="percentile",
        multi_series="returns three series: duration.p50, duration.p90, duration.p99",
    ),
    MetricDef(
        "trace_average_duration",
        "TRACE_AVERAGE_DURATION",
        "trace",
        "Mean trace latency (ms).",
        False,
    ),
    MetricDef(
        "trace_error_rate", "TRACE_ERROR_RATE", "trace", "Share of traces that errored.", False
    ),
    MetricDef("cost", "COST", "trace", "Estimated LLM spend (USD) attributed to traces.", True),
    MetricDef(
        "token_usage",
        "TOKEN_USAGE",
        "trace",
        "Token counts on traces.",
        True,
        sub_metric="usage_key",
        multi_series="one series per usage key (prompt_tokens, completion_tokens, …)",
    ),
    MetricDef(
        "feedback_scores",
        "FEEDBACK_SCORES",
        "trace",
        "Average trace feedback score.",
        True,
        sub_metric="feedback_score",
        multi_series="one series per feedback-score name",
    ),
    MetricDef(
        "guardrails_failed_count",
        "GUARDRAILS_FAILED_COUNT",
        "trace",
        "Guardrail failures on traces.",
        True,
    ),
    # --- span metrics --- #
    MetricDef("span_count", "SPAN_COUNT", "span", "Number of spans per bucket.", True),
    MetricDef(
        "span_duration",
        "SPAN_DURATION",
        "span",
        "Span latency percentiles (ms).",
        True,
        sub_metric="percentile",
        multi_series="returns three series: duration.p50, duration.p90, duration.p99",
    ),
    MetricDef(
        "span_average_duration",
        "SPAN_AVERAGE_DURATION",
        "span",
        "Mean span latency (ms).",
        False,
    ),
    MetricDef("span_error_rate", "SPAN_ERROR_RATE", "span", "Share of spans that errored.", False),
    MetricDef("span_cost", "SPAN_COST", "span", "Estimated spend (USD) per span.", False),
    MetricDef(
        "span_token_usage",
        "SPAN_TOKEN_USAGE",
        "span",
        "Token counts on spans — the one to break down by model or provider.",
        True,
        sub_metric="usage_key",
        multi_series="one series per usage key (prompt_tokens, completion_tokens, …)",
    ),
    MetricDef(
        "span_feedback_scores",
        "SPAN_FEEDBACK_SCORES",
        "span",
        "Average span feedback score.",
        True,
        sub_metric="feedback_score",
        multi_series="one series per feedback-score name",
    ),
    # --- thread metrics --- #
    MetricDef("thread_count", "THREAD_COUNT", "thread", "Number of conversation threads.", True),
    MetricDef(
        "thread_duration",
        "THREAD_DURATION",
        "thread",
        "Thread duration percentiles (ms).",
        True,
        sub_metric="percentile",
        multi_series="returns three series: duration.p50, duration.p90, duration.p99",
    ),
    MetricDef(
        "thread_average_duration",
        "THREAD_AVERAGE_DURATION",
        "thread",
        "Mean thread duration (ms).",
        False,
    ),
    MetricDef("thread_cost", "THREAD_COST", "thread", "Estimated spend (USD) per thread.", False),
    MetricDef(
        "thread_feedback_scores",
        "THREAD_FEEDBACK_SCORES",
        "thread",
        "Average thread feedback score.",
        True,
        sub_metric="feedback_score",
        multi_series="one series per feedback-score name",
    ),
)

METRICS: Final[dict[str, MetricDef]] = {m.name: m for m in _METRIC_LIST}
METRIC_NAMES: Final[tuple[str, ...]] = tuple(METRICS)

#: Words people use for a metric that is not what the backend calls it. Kept
#: deliberately small — an alias table is a second vocabulary to maintain, so
#: it earns its place only for the handful of terms an LLM reaches for first.
METRIC_ALIASES: Final[dict[str, str]] = {
    "latency": "duration",
    "trace_duration": "duration",
    "traces": "trace_count",
    "spans": "span_count",
    "threads": "thread_count",
    "error_rate": "trace_error_rate",
    "errors": "trace_error_rate",
    "tokens": "token_usage",
    "spend": "cost",
    "quality": "feedback_scores",
    "scores": "feedback_scores",
}


class ChartVocabularyError(ValueError):
    """A metric / breakdown / interval the vocabulary doesn't have.

    Carries the valid alternatives in the message: a wrong guess should cost
    one turn, not a fishing expedition (same contract as ``UnknownSkillError``).
    """


def resolve_metric(name: str) -> MetricDef:
    """MCP-facing metric name (or alias, any case) → its definition."""
    key = name.strip().lower()
    key = METRIC_ALIASES.get(key, key)
    metric = METRICS.get(key)
    if metric is None:
        raise ChartVocabularyError(
            f"unknown metric {name!r}. Valid metrics: {', '.join(METRIC_NAMES)}"
        )
    return metric


# --- breakdowns ----------------------------------------------------------- #

#: ``BreakdownField`` values, minus ``none`` which is spelled by omitting the
#: breakdown entirely on this surface.
BREAKDOWN_FIELDS: Final[tuple[str, ...]] = (
    "tags",
    "metadata",
    "name",
    "error_info",
    "error_type",
    "model",
    "provider",
    "type",
    "guardrail_name",
)

#: Which families each breakdown field applies to — a direct transcription of
#: ``BreakdownField.isCompatibleWith``. ``guardrail_name`` is the one field
#: pinned to a single metric rather than a family, handled below.
_BREAKDOWN_FAMILIES: Final[dict[str, frozenset[str]]] = {
    "tags": frozenset({"trace", "span", "thread"}),
    "metadata": frozenset({"trace", "span"}),
    "name": frozenset({"trace", "span"}),
    "error_info": frozenset({"trace", "span"}),
    "error_type": frozenset({"trace", "span"}),
    "model": frozenset({"span"}),
    "provider": frozenset({"span"}),
    "type": frozenset({"span"}),
    "guardrail_name": frozenset(),  # metric-pinned, see check_breakdown
}


def check_breakdown(metric: MetricDef, field: str, *, metadata_key: str | None) -> None:
    """Raise ``ChartVocabularyError`` unless the backend would accept the pair.

    Reproduces ``BreakdownField.isCompatibleWith`` plus the ``METADATA``
    requires-a-key rule, so an incompatible combination is refused here with a
    message naming what WOULD work, rather than as a backend 422.
    """
    if field not in _BREAKDOWN_FAMILIES:
        raise ChartVocabularyError(
            f"unknown breakdown {field!r}. Valid breakdowns: {', '.join(BREAKDOWN_FIELDS)}"
        )
    if not metric.breakdownable:
        allowed = ", ".join(m.name for m in _METRIC_LIST if m.breakdownable)
        raise ChartVocabularyError(
            f"metric {metric.name!r} does not support a breakdown. Metrics that do: {allowed}"
        )
    if field == "guardrail_name":
        if metric.name != "guardrails_failed_count":
            raise ChartVocabularyError(
                "breakdown 'guardrail_name' only applies to metric 'guardrails_failed_count'."
            )
    elif metric.family not in _BREAKDOWN_FAMILIES[field]:
        usable = ", ".join(f for f in BREAKDOWN_FIELDS if metric.family in _BREAKDOWN_FAMILIES[f])
        raise ChartVocabularyError(
            f"breakdown {field!r} does not apply to {metric.family} metrics "
            f"like {metric.name!r}. Usable breakdowns here: {usable or 'none'}"
        )
    if field == "metadata" and not metadata_key:
        raise ChartVocabularyError(
            "breakdown 'metadata' needs breakdown_key — the metadata field to "
            "group by, e.g. breakdown_key='environment'."
        )


VALID_PERCENTILES: Final[tuple[str, ...]] = ("p50", "p90", "p99")


def check_sub_metric(metric: MetricDef, field: str, sub_metric: str | None) -> None:
    """Enforce ``BreakdownConfigValidator``'s ``sub_metric`` rules locally.

    Under a breakdown, duration metrics need a percentile, feedback-score
    metrics need the score name, and token-usage metrics need the usage key —
    because a breakdown collapses the multi-series response to one series and
    the backend has no default for which one that is.
    """
    if metric.sub_metric is None:
        return
    if not sub_metric:
        hint = {
            "percentile": "a percentile — one of p50, p90, p99",
            "feedback_score": "the feedback-score name to plot, e.g. 'hallucination'",
            "usage_key": "the usage key, e.g. 'completion_tokens'",
        }[metric.sub_metric]
        raise ChartVocabularyError(
            f"breakdown {field!r} on metric {metric.name!r} needs sub_metric: {hint}."
        )
    if metric.sub_metric == "percentile" and sub_metric.lower() not in VALID_PERCENTILES:
        raise ChartVocabularyError(
            f"sub_metric {sub_metric!r} is not a percentile. Valid values: "
            f"{', '.join(VALID_PERCENTILES)}"
        )


# --- intervals ------------------------------------------------------------ #

INTERVALS: Final[tuple[str, ...]] = ("hourly", "daily", "weekly", "total")


def resolve_interval(interval: str) -> str:
    """MCP-facing interval → opik-backend ``TimeInterval`` enum value."""
    key = interval.strip().lower()
    if key not in INTERVALS:
        raise ChartVocabularyError(
            f"unknown interval {interval!r}. Valid intervals: {', '.join(INTERVALS)}"
        )
    return key.upper()


# --- stat-card metrics ---------------------------------------------------- #
#
# Stat cards read the trace/span STATS aggregates, a different vocabulary from
# the time-series metrics above (they are stat names, not MetricType values).
# Transcribed from the frontend's ProjectStatsCardWidget metric table, which is
# what the widget's `metric` field is validated against when the UI renders it.

_SHARED_STAT_METRICS: Final[tuple[str, ...]] = (
    "duration.p50",
    "duration.p90",
    "duration.p99",
    "input",
    "output",
    "metadata",
    "tags",
    "total_estimated_cost_sum",
    "usage.completion_tokens",
    "usage.prompt_tokens",
    "usage.total_tokens",
    "error_count",
)

STAT_METRICS: Final[dict[str, tuple[str, ...]]] = {
    "traces": (
        "trace_count",
        "thread_count",
        "llm_span_count",
        "span_count",
        "total_estimated_cost",
        "guardrails_failed_count",
        *_SHARED_STAT_METRICS,
    ),
    "spans": (
        "span_count",
        "total_estimated_cost",
        *_SHARED_STAT_METRICS,
    ),
}

#: Prefix that turns a stat metric into "average of this feedback score", the
#: one open-ended member of the stat vocabulary (the score name is workspace
#: data, so it cannot be enumerated here).
FEEDBACK_SCORE_STAT_PREFIX: Final = "feedback_scores."


def check_stat_metric(metric: str, source: str) -> None:
    valid = STAT_METRICS.get(source)
    if valid is None:
        raise ChartVocabularyError(
            f"unknown stat source {source!r}. Valid sources: {', '.join(STAT_METRICS)}"
        )
    if metric.startswith(FEEDBACK_SCORE_STAT_PREFIX) and len(metric) > len(
        FEEDBACK_SCORE_STAT_PREFIX
    ):
        return
    if metric not in valid:
        raise ChartVocabularyError(
            f"unknown stat metric {metric!r} for source {source!r}. Valid: "
            f"{', '.join(valid)}, or '{FEEDBACK_SCORE_STAT_PREFIX}<score name>'"
        )


# --- filters -------------------------------------------------------------- #


def request_filter_key(family: MetricFamily) -> str:
    """Filter field on the ``/metrics`` request body for this metric family."""
    return {"trace": "trace_filters", "span": "span_filters", "thread": "thread_filters"}[family]


def widget_filter_key(family: MetricFamily) -> str:
    """Filter field inside the stored widget config (the UI reads camelCase)."""
    return {"trace": "traceFilters", "span": "spanFilters", "thread": "threadFilters"}[family]


__all__ = [
    "BREAKDOWN_FIELDS",
    "FEEDBACK_SCORE_STAT_PREFIX",
    "INTERVALS",
    "METRICS",
    "METRIC_ALIASES",
    "METRIC_NAMES",
    "STAT_METRICS",
    "VALID_PERCENTILES",
    "ChartVocabularyError",
    "MetricDef",
    "MetricFamily",
    "check_breakdown",
    "check_stat_metric",
    "check_sub_metric",
    "request_filter_key",
    "resolve_interval",
    "resolve_metric",
    "widget_filter_key",
]
