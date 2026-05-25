import pytest

from opik_mcp.analytics.events import (
    EVENT_STARTUP_ERROR,
    bucket_count,
    bucket_text_len,
    bucket_tokens,
)


def test_startup_error_event_name() -> None:
    # Wire shape is part of the public BI contract — receiver-side parsers
    # and dashboards key off the literal string, so a rename is a breaking
    # change that needs to be noticed in code review.
    assert EVENT_STARTUP_ERROR == "opik_mcp_startup_error"


def test_bucket_tokens_thresholds() -> None:
    assert bucket_tokens(0) == "<2k"
    assert bucket_tokens(1999) == "<2k"
    assert bucket_tokens(2000) == "2k-8k"
    assert bucket_tokens(7999) == "2k-8k"
    assert bucket_tokens(8000) == "8k-32k"
    assert bucket_tokens(31_999) == "8k-32k"
    assert bucket_tokens(32_000) == ">32k"
    assert bucket_tokens(10_000_000) == ">32k"


def test_bucket_text_len_thresholds() -> None:
    assert bucket_text_len("") == "<100"
    assert bucket_text_len("a" * 99) == "<100"
    assert bucket_text_len("a" * 100) == "100-1000"
    assert bucket_text_len("a" * 999) == "100-1000"
    assert bucket_text_len("a" * 1000) == ">1000"


def test_bucket_count_thresholds() -> None:
    assert bucket_count(0) == "0"
    assert bucket_count(1) == "1-10"
    assert bucket_count(10) == "1-10"
    assert bucket_count(11) == "11-100"
    assert bucket_count(100) == "11-100"
    assert bucket_count(101) == "101-1000"
    assert bucket_count(10_000) == ">1000"


# --- PR2 additions ------------------------------------------------------- #


def test_event_constants_exist() -> None:
    from opik_mcp.analytics import (
        EVENT_SERVER_SHUTDOWN,
        EVENT_TOOLS_LISTED,
    )
    assert EVENT_TOOLS_LISTED == "opik_mcp_tools_listed"
    assert EVENT_SERVER_SHUTDOWN == "opik_mcp_server_shutdown"


@pytest.mark.parametrize(
    "elapsed, expected",
    [
        (0.0, "<5s"),
        (4.9, "<5s"),
        (5.0, "5-60s"),
        (59.9, "5-60s"),
        (60.0, "1-10m"),
        (599.9, "1-10m"),
        (600.0, "10-60m"),
        (3599.0, "10-60m"),
        (3600.0, "1-24h"),
        (86399.0, "1-24h"),
        (86400.0, ">24h"),
        (1_000_000.0, ">24h"),
    ],
)
def test_bucket_seconds(elapsed: float, expected: str) -> None:
    from opik_mcp.analytics.events import bucket_seconds
    assert bucket_seconds(elapsed) == expected
