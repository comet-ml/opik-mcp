"""Everything the MCP knows about Opik's Diagnostics (Agent Insights).

Two questions, one per module. ``availability``: can this deployment run
Diagnostics at all? ``state``: what is this project's Diagnostics doing, and
what does a page of its issues not say for itself?

They sit together because they are read together. An empty issue list asks
both, and neither is any use to an entity other than ``agent_insights_issue``.
The rest of ``read_list`` reaches them through this name, so the entity's own
concerns stay in one place as they grow.
"""

from opik_mcp.read_list.entities.agent_insights_issue.availability import (
    UNAVAILABLE_SENTENCE,
    diagnostics_available,
)
from opik_mcp.read_list.entities.agent_insights_issue.entity import HANDLER
from opik_mcp.read_list.entities.agent_insights_issue.state import (
    COVERAGE_GRACE,
    ENABLE_OP,
    STALE_AFTER,
    TRIGGER_OP,
    diagnostics_coverage_note,
    diagnostics_state_hint,
    issue_page_note,
)

__all__ = [
    "COVERAGE_GRACE",
    "ENABLE_OP",
    "HANDLER",
    "STALE_AFTER",
    "TRIGGER_OP",
    "UNAVAILABLE_SENTENCE",
    "diagnostics_available",
    "diagnostics_coverage_note",
    "diagnostics_state_hint",
    "issue_page_note",
]
