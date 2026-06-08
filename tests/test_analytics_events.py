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


# --- allowlist <-> classifier sync (privacy contract, events.py:7-19) ----- #


def test_mcp_host_literal_covers_all_classifier_buckets() -> None:
    """Every value ``classify_mcp_host`` can return must be a declared McpHost.

    events.py promises the allowlist stays in sync with the classifier in
    mcp_client_info.py. Drift means a real bucket value (e.g. ``"codex"``)
    ships in BI without being declared in the privacy allowlist.

    Drives the classifier FUNCTION (not just the pattern table) so the test
    also pins the hardcoded ``"other"`` fallback — renaming it without updating
    the Literal would otherwise pass silently.
    """
    from typing import get_args

    from opik_mcp.analytics.events import McpHost
    from opik_mcp.analytics.mcp_client_info import _MCP_HOST_PATTERNS, classify_mcp_host

    allowed = set(get_args(McpHost))
    produced = {classify_mcp_host(pattern) for pattern, _bucket in _MCP_HOST_PATTERNS}
    produced.add(classify_mcp_host(""))  # empty clientInfo.name -> fallback
    produced.add(classify_mcp_host("totally-unrecognized-host"))  # no match -> fallback
    missing = produced - allowed
    assert not missing, f"McpHost Literal missing classifier values: {sorted(missing)}"


def test_host_llm_family_literal_covers_all_classifier_values() -> None:
    """Every value ``classify_host_llm_family`` can return must be a declared
    HostLlmFamily value (same sync contract as McpHost).

    The classifier's only inputs are McpHost buckets, so we drive it over every
    declared McpHost value plus an unmapped one to pin the ``"unknown"`` fallback.
    """
    from typing import get_args

    from opik_mcp.analytics.events import HostLlmFamily, McpHost
    from opik_mcp.analytics.mcp_client_info import classify_host_llm_family

    allowed = set(get_args(HostLlmFamily))
    produced = {classify_host_llm_family(host) for host in get_args(McpHost)}
    produced.add(classify_host_llm_family("unmapped-bucket"))  # -> "unknown" fallback
    missing = produced - allowed
    assert not missing, f"HostLlmFamily Literal missing classifier values: {sorted(missing)}"


# --- auth_rejected event constant + path bucketing (GAP#3 foundation) ----- #


def test_auth_rejected_event_name() -> None:
    # Wire-shape contract: the receiver and dashboards key off this literal
    # string, so a rename is a breaking BI change. Imported via the package
    # surface to also pin that it's exported from analytics/__init__.
    from opik_mcp.analytics import EVENT_AUTH_REJECTED

    assert EVENT_AUTH_REJECTED == "opik_mcp_auth_rejected"


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/mcp", "mcp"),
        ("/mcp/", "mcp"),
        ("/mcp/messages", "mcp"),
        ("/health", "health"),
        ("/health/ready", "health"),
        ("/.well-known/oauth-protected-resource", "well_known"),
        ("/.well-known/oauth-authorization-server", "well_known"),
        ("/authorize", "other"),
        ("/register", "other"),
        ("/", "other"),
        ("", "other"),
        # Sibling paths must NOT collide with the real endpoints (exact-or-subpath).
        ("/mcpfoo", "other"),
        ("/healthz", "other"),
        ("/.well-knownfoo", "other"),
    ],
)
def test_bucket_path(path: str, expected: str) -> None:
    from opik_mcp.analytics.events import bucket_path

    assert bucket_path(path) == expected


def test_bucket_path_respects_custom_mcp_path() -> None:
    # The MCP transport path is configurable (OPIK_MCP_HTTP_PATH). A request to
    # the configured path buckets to "mcp"; the hardcoded "/mcp" default must
    # NOT match when the operator has remapped it.
    from opik_mcp.analytics.events import bucket_path

    assert bucket_path("/api/v1/mcp", mcp_http_path="/api/v1/mcp") == "mcp"
    assert bucket_path("/mcp", mcp_http_path="/api/v1/mcp") == "other"


def test_path_bucket_literal_matches_bucket_path_outputs() -> None:
    # bucket_path never emits a raw path — only these four buckets — and the
    # PathBucket Literal must declare exactly them (privacy + BI contract).
    from typing import get_args

    from opik_mcp.analytics.events import PathBucket, bucket_path

    allowed = set(get_args(PathBucket))
    assert allowed == {"mcp", "health", "well_known", "other"}
    # Samples chosen to exercise every bucket, so this is a bidirectional check:
    # the Literal declares exactly what bucket_path can produce (no dead buckets,
    # no undeclared outputs).
    samples = ["/mcp", "/health", "/.well-known/x", "/anything-else", ""]
    assert {bucket_path(p) for p in samples} == allowed
