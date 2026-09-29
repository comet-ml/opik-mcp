"""Cost intelligence mode: a local server pointed at an AI Spend workspace.

A leaf package: it reads the settings and nothing else, so any layer may ask
which mode it is in.
"""

from __future__ import annotations

from typing import Final, Literal

from opik_mcp.config import Settings

WORKSPACE_PREFIX: Final = "__ai_spend_"
FIXED_PROJECT: Final = "claude-code"

Mode = Literal["default", "cost_intelligence"]
DEFAULT_MODE: Final[Mode] = "default"
COST_INTELLIGENCE_MODE: Final[Mode] = "cost_intelligence"
ALL_MODES: Final[frozenset[Mode]] = frozenset({DEFAULT_MODE, COST_INTELLIGENCE_MODE})


def is_cost_intelligence(settings: Settings) -> bool:
    """Only the local stdio server enters it; the hosted HTTP server never does."""
    workspace = settings.comet_workspace or ""
    return settings.opik_mcp_transport.lower() == "stdio" and workspace.startswith(WORKSPACE_PREFIX)


def mode_of(settings: Settings) -> Mode:
    return COST_INTELLIGENCE_MODE if is_cost_intelligence(settings) else DEFAULT_MODE


__all__ = [
    "ALL_MODES",
    "COST_INTELLIGENCE_MODE",
    "DEFAULT_MODE",
    "FIXED_PROJECT",
    "WORKSPACE_PREFIX",
    "Mode",
    "is_cost_intelligence",
    "mode_of",
]
