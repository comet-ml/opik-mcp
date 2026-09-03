"""The chart vocabulary must agree with opik-backend's own rules.

Every table in ``charts.vocabulary`` is a transcription of a backend
validator (``MetricType``, ``BreakdownField.isCompatibleWith``,
``BreakdownConfigValidator``). Transcriptions rot, and the failure is quiet:
a wrong entry either rejects a chart the backend would have accepted, or
sends one it rejects with a message naming Java field paths. These tests pin
the rules that matter and the sentence each error gives back.
"""

from __future__ import annotations

import pytest

from opik_mcp.charts.vocabulary import (
    BREAKDOWN_FIELDS,
    METRIC_NAMES,
    METRICS,
    ChartVocabularyError,
    check_breakdown,
    check_stat_metric,
    check_sub_metric,
    request_filter_key,
    resolve_interval,
    resolve_metric,
    widget_filter_key,
)


def test_every_metric_has_a_distinct_wire_name() -> None:
    """The wire name is what the backend enum takes and what
    ``query_from_widget`` maps back from — a duplicate would silently make one
    stored widget replay as a different metric."""
    wires = [m.wire for m in METRICS.values()]
    assert len(set(wires)) == len(wires)


def test_metric_names_are_lowercase_and_match_their_key() -> None:
    for name, metric in METRICS.items():
        assert name == metric.name == name.lower()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("trace_count", "trace_count"),
        ("TRACE_COUNT", "trace_count"),
        ("  duration ", "duration"),
        ("latency", "duration"),
        ("errors", "trace_error_rate"),
        ("spend", "cost"),
    ],
)
def test_aliases_and_casing_resolve(given: str, expected: str) -> None:
    assert resolve_metric(given).name == expected


def test_unknown_metric_names_the_alternatives() -> None:
    with pytest.raises(ChartVocabularyError) as exc:
        resolve_metric("cost_per_trace")
    message = str(exc.value)
    assert "cost_per_trace" in message
    # A wrong guess should cost one turn: the valid set is in the message.
    assert "trace_count" in message and "cost" in message


def test_model_breakdown_is_span_only() -> None:
    """``BreakdownField.isCompatibleWith``: MODEL/PROVIDER/TYPE are span-only.

    This is the mistake an LLM makes most — "cost by model" reads as a trace
    question — so the message has to name the span metric that works."""
    check_breakdown(METRICS["span_token_usage"], "model", metadata_key=None)
    with pytest.raises(ChartVocabularyError) as exc:
        check_breakdown(METRICS["cost"], "model", metadata_key=None)
    assert "trace metrics" in str(exc.value)


def test_metrics_outside_the_breakdown_sets_reject_every_breakdown() -> None:
    """``trace_error_rate`` is a trace metric but is in no BreakdownField set,
    so the backend rejects any breakdown on it."""
    assert METRICS["trace_error_rate"].breakdownable is False
    with pytest.raises(ChartVocabularyError) as exc:
        check_breakdown(METRICS["trace_error_rate"], "name", metadata_key=None)
    assert "does not support a breakdown" in str(exc.value)


def test_metadata_breakdown_requires_a_key() -> None:
    with pytest.raises(ChartVocabularyError) as exc:
        check_breakdown(METRICS["trace_count"], "metadata", metadata_key=None)
    assert "breakdown_key" in str(exc.value)
    check_breakdown(METRICS["trace_count"], "metadata", metadata_key="environment")


def test_guardrail_name_breakdown_is_pinned_to_its_metric() -> None:
    check_breakdown(METRICS["guardrails_failed_count"], "guardrail_name", metadata_key=None)
    with pytest.raises(ChartVocabularyError):
        check_breakdown(METRICS["trace_count"], "guardrail_name", metadata_key=None)


def test_unknown_breakdown_lists_the_valid_fields() -> None:
    with pytest.raises(ChartVocabularyError) as exc:
        check_breakdown(METRICS["trace_count"], "user_id", metadata_key=None)
    assert all(field in str(exc.value) for field in ("tags", "metadata", "name"))


@pytest.mark.parametrize(
    ("metric", "hint"),
    [
        ("duration", "percentile"),
        ("feedback_scores", "hallucination"),
        ("token_usage", "completion_tokens"),
    ],
)
def test_multi_series_metrics_need_a_sub_metric_under_a_breakdown(metric: str, hint: str) -> None:
    with pytest.raises(ChartVocabularyError) as exc:
        check_sub_metric(METRICS[metric], "name", None)
    assert hint in str(exc.value)


def test_single_series_metrics_need_no_sub_metric() -> None:
    check_sub_metric(METRICS["trace_count"], "name", None)


def test_percentile_sub_metric_is_validated() -> None:
    check_sub_metric(METRICS["duration"], "name", "p99")
    with pytest.raises(ChartVocabularyError) as exc:
        check_sub_metric(METRICS["duration"], "name", "p95")
    assert "p50, p90, p99" in str(exc.value)


def test_intervals_round_trip_to_the_backend_enum() -> None:
    assert resolve_interval("daily") == "DAILY"
    assert resolve_interval(" Weekly ") == "WEEKLY"
    with pytest.raises(ChartVocabularyError):
        resolve_interval("monthly")


def test_filter_keys_differ_between_the_request_and_the_stored_widget() -> None:
    """The request body is snake_case (a Java DTO); the widget config is
    camelCase (a frontend document). Mixing them up drops the filters
    silently — the chart renders, just over the wrong rows."""
    assert request_filter_key("trace") == "trace_filters"
    assert widget_filter_key("trace") == "traceFilters"
    assert request_filter_key("span") == "span_filters"
    assert widget_filter_key("thread") == "threadFilters"


def test_stat_metrics_accept_feedback_score_names() -> None:
    check_stat_metric("feedback_scores.hallucination", "traces")
    with pytest.raises(ChartVocabularyError):
        check_stat_metric("feedback_scores.", "traces")


def test_trace_only_stat_metrics_are_rejected_for_spans() -> None:
    check_stat_metric("trace_count", "traces")
    with pytest.raises(ChartVocabularyError) as exc:
        check_stat_metric("trace_count", "spans")
    assert "span_count" in str(exc.value)


def test_public_name_tuples_match_their_tables() -> None:
    assert set(METRIC_NAMES) == set(METRICS)
    assert "none" not in BREAKDOWN_FIELDS, "'none' is spelled by omitting the breakdown"
