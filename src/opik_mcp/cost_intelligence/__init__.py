"""Cost intelligence: a local server pointed at an AI Spend workspace.

A leaf package: it reads the settings and nothing else, so any layer may ask
which features the workspace turns on.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.config import Settings

WORKSPACE_PREFIX: Final = "__ai_spend_"
FIXED_PROJECT: Final = "claude-code"
AI_SPEND_FEATURE: Final = "ai_spend"


def is_ai_spend_workspace(settings: Settings) -> bool:
    """True for the local stdio server in an AI Spend workspace; never for hosted HTTP."""
    workspace = settings.comet_workspace or ""
    return settings.opik_mcp_transport.lower() == "stdio" and workspace.startswith(WORKSPACE_PREFIX)


def enabled_features(settings: Settings) -> frozenset[str]:
    return frozenset({AI_SPEND_FEATURE}) if is_ai_spend_workspace(settings) else frozenset()


__all__ = [
    "AI_SPEND_FEATURE",
    "FIXED_PROJECT",
    "WORKSPACE_PREFIX",
    "enabled_features",
    "is_ai_spend_workspace",
]
