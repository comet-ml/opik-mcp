"""Public surface for opik-mcp analytics.

`get_analytics()` returns a process-wide singleton bound to the live `Settings`.
`track_event(event_type, properties)` is the convenience wrapper every call site
uses — never construct an `AnalyticsClient` directly outside this package.
"""

from __future__ import annotations

from functools import lru_cache

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.analytics.events import (
    EVENT_ASK_OLLIE_COMPLETED,
    EVENT_AUTO_APPROVAL,
    EVENT_SERVER_SHUTDOWN,
    EVENT_SERVER_STARTED,
    EVENT_SESSION_INITIALIZED,
    EVENT_STARTUP_ERROR,
    EVENT_TOOL_CALLED,
    EVENT_TOOLS_LISTED,
    bucket_count,
    bucket_seconds,
    bucket_text_len,
    bucket_tokens,
)
from opik_mcp.config import get_settings

__all__ = [
    "EVENT_ASK_OLLIE_COMPLETED",
    "EVENT_AUTO_APPROVAL",
    "EVENT_SERVER_SHUTDOWN",
    "EVENT_SERVER_STARTED",
    "EVENT_SESSION_INITIALIZED",
    "EVENT_STARTUP_ERROR",
    "EVENT_TOOL_CALLED",
    "EVENT_TOOLS_LISTED",
    "bucket_count",
    "bucket_seconds",
    "bucket_text_len",
    "bucket_tokens",
    "get_analytics",
    "reset_analytics_for_tests",
    "track_event",
]


@lru_cache(maxsize=1)
def get_analytics() -> AnalyticsClient:
    return AnalyticsClient(get_settings())


def track_event(event_type: str, properties: dict[str, str]) -> None:
    """Convenience wrapper around the process-wide singleton."""
    get_analytics().track_event(event_type, properties)


def reset_analytics_for_tests() -> None:
    """Drop the singleton so the next `get_analytics()` rebuilds with fresh Settings.

    Call sites: pytest fixtures that override env vars and need a fresh client.
    Never call from production code.
    """
    # Close the cached client first so its worker thread + http session shut
    # down cleanly. Without this, a test that resets mid-run leaves an orphan
    # daemon thread draining against a stale http client (harmless thanks to
    # daemon=True, but it muddies log output).
    if get_analytics.cache_info().currsize:
        get_analytics().close()
    get_analytics.cache_clear()
