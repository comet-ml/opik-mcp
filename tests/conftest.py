from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _reset_analytics_wrappers_state() -> Generator[None]:
    from opik_mcp.analytics.wrappers import _reset_seen_sessions_for_tests

    _reset_seen_sessions_for_tests()
    yield
    _reset_seen_sessions_for_tests()
