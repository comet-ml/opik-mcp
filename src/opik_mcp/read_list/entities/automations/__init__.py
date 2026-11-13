"""Automations: the automation, its runs, the recipe states, the recommendations
and the loop they make.

Five entities in one namespace because they share one backend feature and the
sentence for a backend that lacks it, and entities may not import each other.
"""

from __future__ import annotations

from opik_mcp.read_list.entities.automations.automation import HANDLER as AUTOMATION_HANDLER
from opik_mcp.read_list.entities.automations.automation_run import HANDLER as RUN_HANDLER
from opik_mcp.read_list.entities.automations.loop import HANDLER as LOOP_HANDLER
from opik_mcp.read_list.entities.automations.recommendation import (
    HANDLER as RECOMMENDATION_HANDLER,
)
from opik_mcp.read_list.entities.automations.workflow import HANDLER as WORKFLOW_HANDLER

HANDLERS = (
    AUTOMATION_HANDLER,
    RUN_HANDLER,
    WORKFLOW_HANDLER,
    RECOMMENDATION_HANDLER,
    LOOP_HANDLER,
)

__all__ = ["HANDLERS"]
