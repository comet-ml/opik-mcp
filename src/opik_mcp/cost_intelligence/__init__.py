"""Cost intelligence (the AI Spend workspaces): what turns the feature on, and its name.

The name matches opik-backend's ``serviceToggles.costIntelligenceEnabled``. The
predicate lives here, not in ``config.py`` or in the toggle config, so the
framework never learns what an AI Spend workspace looks like.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from opik_mcp.config import Settings

if TYPE_CHECKING:
    from opik_mcp.features.toggles import FeatureToggles

#: The toggle's name; an entity declares it as ``feature=``.
COST_INTELLIGENCE_FEATURE: Final = "cost_intelligence"

#: opik-backend provisions a cost intelligence workspace with this name prefix, so
#: the workspace the caller already points at is what selects the feature. There is
#: deliberately no environment variable of our own.
AI_SPEND_WORKSPACE_PREFIX: Final = "__ai_spend_"

FIXED_PROJECT: Final = "claude-code"


def is_cost_intelligence_enabled(settings: Settings) -> bool:
    """A local stdio install pointed at an AI Spend workspace, and nothing else.

    The hosted HTTP server never turns this on: there the workspace arrives per
    request from the caller's bearer, so one process answers for many workspaces
    and a process-wide toggle would be wrong.
    """
    return settings.opik_mcp_transport.lower() == "stdio" and (
        settings.comet_workspace or ""
    ).startswith(AI_SPEND_WORKSPACE_PREFIX)


def shows_spend_types(toggles: FeatureToggles) -> bool:
    """``EntityHandler.shown_when`` for the spend entities."""
    return toggles.cost_intelligence_enabled


__all__ = [
    "AI_SPEND_WORKSPACE_PREFIX",
    "COST_INTELLIGENCE_FEATURE",
    "FIXED_PROJECT",
    "is_cost_intelligence_enabled",
    "shows_spend_types",
]
