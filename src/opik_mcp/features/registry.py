from __future__ import annotations

from collections.abc import Mapping

from opik_mcp.config import Settings
from opik_mcp.cost_intelligence.feature import FEATURE as AI_SPEND
from opik_mcp.features.feature import Feature

FEATURE_REGISTRY: Mapping[str, Feature] = {feature.name: feature for feature in (AI_SPEND,)}


def enabled_features(settings: Settings) -> tuple[Feature, ...]:
    return tuple(feature for name, feature in FEATURE_REGISTRY.items() if name in settings.features)
