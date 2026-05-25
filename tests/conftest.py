from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _reset_analytics_wrappers_state() -> Generator[None]:
    """Reset every process-wide analytics flag between tests.

    Each test file used to reset only the globals it touched, leaving a
    cross-file pollution foot-gun: a new test that calls (e.g.)
    ``_maybe_emit_tools_listed`` without its own autouse fixture would
    inherit ``_tools_listed_fired_processwide=True`` from a previous file
    and silently no-op. Centralising the reset here keeps every test
    independent regardless of which globals it ends up touching.
    """
    from opik_mcp.analytics import transport_probe
    from opik_mcp.analytics.wrappers import (
        _reset_seen_sessions_for_tests,
        _reset_seen_tools_listed_for_tests,
    )

    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()
    yield
    _reset_seen_sessions_for_tests()
    _reset_seen_tools_listed_for_tests()
    transport_probe.reset_for_tests()
