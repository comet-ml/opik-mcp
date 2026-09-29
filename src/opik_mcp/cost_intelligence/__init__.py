"""Cost intelligence: a local server pointed at an AI Spend workspace.

The toggle is ``Settings.features`` in ``config.py``; ``feature.py`` declares
what the feature adds and ``opik_mcp.features.registry`` is the only way in.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.config import AI_SPEND_FEATURE

FIXED_PROJECT: Final = "claude-code"

__all__ = ["AI_SPEND_FEATURE", "FIXED_PROJECT"]
