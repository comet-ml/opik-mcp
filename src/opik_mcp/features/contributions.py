"""What the features that are on add to the surface.

Apart from ``FeatureToggles``, which only says which features are on: a toggle is
configuration, this is the material a concern renders. One function per concern,
and a concern reads only its own — so a later toggle that touches none of them
adds nothing here, and none of the concerns learns a feature's name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from opik_mcp.cost_intelligence import feature as cost_intelligence
from opik_mcp.features.toggles import FeatureToggles


def tool_sentences(toggles: FeatureToggles) -> tuple[tuple[str, str], ...]:
    """``(tool, sentence)`` to put at the front of that tool's description."""
    if not toggles.cost_intelligence_enabled:
        return ()
    return (
        ("read", cost_intelligence.READ_SENTENCE),
        ("list", cost_intelligence.LIST_SENTENCE),
    )


def instructions_paragraphs(toggles: FeatureToggles) -> tuple[str, ...]:
    """Paragraphs to add to the ``initialize`` instructions."""
    if not toggles.cost_intelligence_enabled:
        return ()
    return (cost_intelligence.INSTRUCTIONS_PARAGRAPH,)


def extra_skills(toggles: FeatureToggles) -> Mapping[str, Callable[[], str]]:
    """Skills ``read_skill`` serves beyond the bundled ones, by name."""
    if not toggles.cost_intelligence_enabled:
        return {}
    return {cost_intelligence.GUIDE_NAME: cost_intelligence.read_guide}
