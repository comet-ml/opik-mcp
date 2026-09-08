"""OQL (Opik Query Language) parser — the ``filters`` string of the ``list`` tool.

Grammar cases are ported from the Opik Python SDK's parser tests so the MCP
accepts exactly what ``search_traces(filter_string=...)`` accepts. The
backend-alignment cases are ours: the field/operator tables come from
opik-backend's enums, not the SDK's, so a string that passes here does not
400 server-side.
"""

from __future__ import annotations

import pytest

from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import OQLError, compile_filters


def _clause(field: str, operator: str, value: str = "", key: str = "") -> dict[str, str]:
    return {"field": field, "operator": operator, "key": key, "value": value}


# --- grammar (ported from the SDK) ---------------------------------------- #


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('name = "test"', [_clause("name", "=", "test")]),
        ("usage.total_tokens > 100", [_clause("usage.total_tokens", ">", "100")]),
        ('tags contains "important"', [_clause("tags", "contains", "important")]),
        ('output not_contains "error"', [_clause("output", "not_contains", "error")]),
        (
            'feedback_scores."Answer Relevance" < 0.8',
            [_clause("feedback_scores", "<", "0.8", key="Answer Relevance")],
        ),
        (
            'feedback_scores."Escaped ""Quote""" < 0.8',
            [_clause("feedback_scores", "<", "0.8", key='Escaped "Quote"')],
        ),
        (
            'name = "test" AND tags contains "important"  ',
            [_clause("name", "=", "test"), _clause("tags", "contains", "important")],
        ),
        (
            'id starts_with "123456" and id ends_with "789012"',
            [_clause("id", "starts_with", "123456"), _clause("id", "ends_with", "789012")],
        ),
        ('thread_id = "thread_123"', [_clause("thread_id", "=", "thread_123")]),
        ("llm_span_count > 5", [_clause("llm_span_count", ">", "5")]),
        (
            "span_feedback_scores.accuracy > 0.9",
            [_clause("span_feedback_scores", ">", "0.9", key="accuracy")],
        ),
        (
            "span_feedback_scores.my_metric is_empty",
            [_clause("span_feedback_scores", "is_empty", key="my_metric")],
        ),
        (
            'annotation_queue_ids contains "queue_1"',
            [_clause("annotation_queue_ids", "contains", "queue_1")],
        ),
        (
            "total_estimated_cost > 0 AND duration > 100",
            [_clause("total_estimated_cost", ">", "0"), _clause("duration", ">", "100")],
        ),
        ('input_json.model = "gpt-4"', [_clause("input_json", "=", "gpt-4", key="model")]),
        (
            'output_json.result contains "success"',
            [_clause("output_json", "contains", "success", key="result")],
        ),
        (
            "feedback_scores.my_metric is_empty AND feedback_scores.other_metric > 0.5",
            [
                _clause("feedback_scores", "is_empty", key="my_metric"),
                _clause("feedback_scores", ">", "0.5", key="other_metric"),
            ],
        ),
        ("error_info is_empty", [_clause("error_info", "is_empty")]),
        ("error_info is_not_empty", [_clause("error_info", "is_not_empty")]),
        ("tags is_empty", [_clause("tags", "is_empty")]),
        ("tags is_not_empty", [_clause("tags", "is_not_empty")]),
        # negative numbers
        ("duration > -5", [_clause("duration", ">", "-5")]),
        ("total_estimated_cost < -0.1", [_clause("total_estimated_cost", "<", "-0.1")]),
        # escaped quotes in values
        ('name = "say ""hi"" there"', [_clause("name", "=", 'say "hi" there')]),
        ('name = """quoted"""', [_clause("name", "=", '"quoted"')]),
        # in / not_in take a parenthesised list, serialised comma-joined
        (
            'environment in ("prod", "staging")',
            [_clause("environment", "in", "prod,staging")],
        ),
        ('environment not_in ("debug")', [_clause("environment", "not_in", "debug")]),
        (
            'environment in ("a") AND input contains "hello"',
            [_clause("environment", "in", "a"), _clause("input", "contains", "hello")],
        ),
        # date-time values are quoted ISO-8601 instants
        (
            'start_time >= "2026-09-08T00:00:00Z"',
            [_clause("start_time", ">=", "2026-09-08T00:00:00Z")],
        ),
    ],
)
def test_trace_grammar_compiles_to_backend_filter_array(
    query: str, expected: list[dict[str, str]]
) -> None:
    assert compile_filters("trace", query) == expected


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_compiles_to_no_filters(query: str) -> None:
    assert compile_filters("trace", query) == []


@pytest.mark.parametrize(
    ("query", "kind", "fragment"),
    [
        ("name = test", "syntax", "double quotes"),
        ('name = "test" extra_stuff', "syntax", "trailing"),
        ('name = "test" OR name = "other"', "syntax", "OR is not supported"),
        ('feedback_scores."Unterminated Quote < 0.8', "syntax", "closing quote"),
        ('name = "hello', "syntax", "closing quote"),
        ("duration >", "syntax", "unexpected end"),
        ("name =", "syntax", "unexpected end"),
        ("duration > 5 and", "syntax", "unexpected end"),
        ("id", "syntax", "unexpected end"),
        ("duration > -", "syntax", "number after '-'"),
        ("duration > 5.", "syntax", "digits after decimal point"),
        ("environment in prod", "syntax", "'('"),
        ('environment in ("unterminated"', "syntax", "missing ')'"),
        ("environment in (unquoted)", "syntax", "quoted strings"),
        ("environment in ()", "syntax", "at least one item"),
        ("usage.invalid_metric = 100", "unknown_field", "usage.total_tokens"),
        ('invalid_field.key = "value"', "unknown_field", "invalid_field"),
        ('error_info = "something"', "bad_operator", "error_info"),
    ],
)
def test_sdk_rejections_are_kept(query: str, kind: str, fragment: str) -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", query)
    assert ei.value.kinds == (kind,)
    assert fragment in str(ei.value)


# --- self-healing: the message carries what is needed to fix the string --- #


def test_oql_error_is_a_typed_validation_error() -> None:
    with pytest.raises(EntityArgValidationError):
        compile_filters("trace", 'name = "test" extra')


def test_syntax_error_marks_the_position_and_shows_the_grammar() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", "error_info is_not_empty AND duration > five")
    message = str(ei.value)
    # The offending string is echoed with a caret under the failing position.
    assert "error_info is_not_empty AND duration > five" in message
    assert "\n" + " " * len("error_info is_not_empty AND duration > ") + "^" in message
    assert "<field>[.<key>] <op> <value> [AND ...]" in message


def test_unknown_field_suggests_the_closest_name_and_lists_the_fields() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", "durration > 5")
    message = str(ei.value)
    assert "Unknown field 'durration'" in message
    assert "Did you mean 'duration'?" in message
    # Names only — the full type/operator table lives behind schema("list.trace").
    assert "Fields: " in message
    assert "thread_id" in message
    assert "date_time" not in message


def test_bad_operator_names_the_type_and_the_valid_operators() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", "metadata.env >= 5")
    message = str(ei.value)
    assert "'>=' is not valid for 'metadata' (dictionary)" in message
    assert (
        "Valid: =, !=, contains, not_contains, starts_with, ends_with, >, <, is_empty, is_not_empty"
        in message
    )


def test_bad_date_value_shows_the_expected_format() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'start_time > "2026-09-08"')
    assert ei.value.kinds == ("bad_value",)
    assert 'ISO-8601 instant with a timezone, e.g. "2026-09-08T10:00:00Z"' in str(ei.value)


def test_bad_number_value_on_duration_mentions_milliseconds() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'duration > "5s"')
    message = str(ei.value)
    assert ei.value.kinds == ("bad_value",)
    assert "milliseconds" in message


def test_score_and_dictionary_fields_require_a_key() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'feedback_scores > 0.5 AND metadata = "x"')
    message = str(ei.value)
    assert ei.value.kinds == ("bad_value", "bad_value")
    assert "feedback_scores.<name>" in message
    assert "metadata.<key>" in message


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'durration > 5 AND error_info = "x" AND start_time > "yesterday"')
    assert ei.value.kinds == ("unknown_field", "bad_operator", "bad_value")
    message = str(ei.value)
    assert "1." in message and "2." in message and "3." in message


# --- backend alignment (where the SDK's tables are wrong or short) --------- #


def test_dictionary_fields_have_no_gte_lte_but_do_have_emptiness() -> None:
    with pytest.raises(OQLError):
        compile_filters("trace", "metadata.x <= 5")
    assert compile_filters("trace", "metadata.x is_empty") == [
        _clause("metadata", "is_empty", key="x")
    ]


def test_enum_fields_accept_emptiness_operators() -> None:
    assert compile_filters("trace", "environment is_not_empty") == [
        _clause("environment", "is_not_empty")
    ]


def test_source_accepts_only_equality() -> None:
    assert compile_filters("trace", 'source = "sdk"') == [_clause("source", "=", "sdk")]
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'source in ("sdk")')
    assert ei.value.kinds == ("bad_operator",)


def test_backend_only_trace_fields_are_known() -> None:
    assert compile_filters("trace", 'error_type = "TimeoutError" AND ttft > 200') == [
        _clause("error_type", "=", "TimeoutError"),
        _clause("ttft", ">", "200"),
    ]


def test_unknown_top_level_field_is_rejected_not_defaulted_to_string() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", 'model = "gpt-4"')
    assert ei.value.kinds == ("unknown_field",)


def test_usage_without_a_metric_names_the_usage_fields() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", "usage > 5")
    assert ei.value.kinds == ("unknown_field",)
    assert "Unknown field 'usage'" in str(ei.value)
    assert "usage.total_tokens" in str(ei.value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-08 10:00:00Z",
        "2026-09-08",
        "2026-09-08T10:00:00",
        "2026-09-08T10Z",
        "2026-09-08T10:00Z",
    ],
)
def test_dates_the_backend_would_refuse_are_rejected_locally(value: str) -> None:
    """``Instant.parse`` wants a ``T`` separator and a timezone; Python's
    ``fromisoformat`` is looser, so the local check must be stricter than it."""
    with pytest.raises(OQLError) as ei:
        compile_filters("trace", f'start_time > "{value}"')
    assert ei.value.kinds == ("bad_value",)


def test_unknown_entity_type_is_rejected() -> None:
    with pytest.raises(OQLError) as ei:
        compile_filters("project", 'name = "x"')
    assert "trace, span, thread, experiment" in str(ei.value)
