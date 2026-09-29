"""The stub's AI Spend routes: ``POST /v1/private/ai-spend/...`` for cost intelligence mode.

Each route answers a fixture from ``fixtures/ai_spend_*.json``. A test may hand
the stub a different body for a fixture (``StubBackend.spend_payloads``), keyed
by the fixture's name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from tests.hermetic.fixtures import load

PREFIX = "/v1/private/ai-spend"

#: Route -> fixture. Order matters only for the two prefixes with a variable part.
_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(f"{PREFIX}{pattern}"), fixture)
    for pattern, fixture in (
        ("/summary", "ai_spend_summary"),
        ("/composition", "ai_spend_composition"),
        ("/composition/[^/]+/breakdown", "ai_spend_lane_breakdown"),
        ("/users", "ai_spend_users"),
        ("/(mcp-servers|skills|built-in-tools)/users", "ai_spend_item_users"),
        ("/sessions", "ai_spend_sessions"),
        ("/sessions/[^/]+/narrative", "ai_spend_narrative"),
        ("/agents", "ai_spend_agents"),
    )
)


def spend_answer(
    method: str, path: str, overrides: Mapping[str, object]
) -> tuple[int, object] | None:
    """The answer for a spend route, or ``None`` when ``path`` is not one."""
    if not path.startswith(PREFIX):
        return None
    if method != "POST":
        return 405, {"message": f"spend routes take POST, not {method}"}
    for pattern, fixture in _ROUTES:
        if pattern.fullmatch(path):
            return 200, overrides[fixture] if fixture in overrides else load(fixture)
    return 404, {"message": f"stub has no route for POST {path}"}
