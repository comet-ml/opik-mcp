"""The feature toggle config: one named boolean per feature, resolved once.

Shaped after opik-backend's ``ServiceTogglesConfig``: an explicit field per
feature, asked as a named question. This is the one generic module that names
features, so it is where a toggle is paired with whatever it contributes.

There is deliberately no "what a feature adds" contract. A toggle contributes to
whichever concerns it happens to touch, and a concern reads only its own
accessor — so a future toggle that has nothing to do with tools or skills adds a
boolean and, if it needs one at all, an accessor of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from opik_mcp.config import Settings
from opik_mcp.cost_intelligence import feature as cost_intelligence
from opik_mcp.cost_intelligence import is_cost_intelligence_enabled


@dataclass(frozen=True)
class FeatureToggles:
    """Every feature's on/off. Frozen, so a resolved answer cannot drift mid-process."""

    cost_intelligence_enabled: bool = False

    @classmethod
    def resolve(cls, settings: Settings) -> FeatureToggles:
        """The one place a toggle is resolved: at startup, from settings."""
        return cls(cost_intelligence_enabled=is_cost_intelligence_enabled(settings))

    # --- what the toggles that are on contribute, one accessor per concern --- #

    @property
    def tool_sentences(self) -> tuple[tuple[str, str], ...]:
        """``(tool, sentence)`` to put at the front of that tool's description."""
        if not self.cost_intelligence_enabled:
            return ()
        return (
            ("read", cost_intelligence.READ_SENTENCE),
            ("list", cost_intelligence.LIST_SENTENCE),
        )

    @property
    def instructions_paragraphs(self) -> tuple[str, ...]:
        """Paragraphs to add to the ``initialize`` instructions."""
        if not self.cost_intelligence_enabled:
            return ()
        return (cost_intelligence.INSTRUCTIONS_PARAGRAPH,)

    @property
    def extra_skills(self) -> Mapping[str, Callable[[], str]]:
        """Skills ``read_skill`` serves beyond the bundled ones, by name."""
        if not self.cost_intelligence_enabled:
            return {}
        return {cost_intelligence.GUIDE_NAME: cost_intelligence.read_guide}

    def __bool__(self) -> bool:
        return self.cost_intelligence_enabled


NO_FEATURES: Final = FeatureToggles()
