"""The feature toggle config: one named boolean per feature, resolved once.

Shaped after opik-backend's ``ServiceTogglesConfig``: an explicit field per
feature, asked as a named question. It answers only which features are on. What
an enabled feature *adds* is ``features/contributions.py``, so configuration and
material stay apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import is_cost_intelligence_enabled


@dataclass(frozen=True)
class FeatureToggles:
    """Every feature's on/off. Frozen, so a resolved answer cannot drift mid-process."""

    cost_intelligence_enabled: bool = False

    @classmethod
    def resolve(cls, settings: Settings) -> FeatureToggles:
        """The one place a toggle is resolved: at startup, from settings."""
        return cls(cost_intelligence_enabled=is_cost_intelligence_enabled(settings))

    def __bool__(self) -> bool:
        return self.cost_intelligence_enabled


NO_FEATURES: Final = FeatureToggles()
