"""The feature registry is a table; every entry declares what the framework reads."""

from __future__ import annotations

import ast
import pathlib

import pytest

from opik_mcp.config import AI_SPEND_FEATURE
from opik_mcp.cost_intelligence.feature import FEATURE as AI_SPEND
from opik_mcp.features import registry
from opik_mcp.features.registry import FEATURE_REGISTRY, enabled_features
from opik_mcp.skills_catalog import skill_names
from tests.factories import make_settings

ADVERTISED_TOOLS = {"read", "list", "write", "schema", "read_skill"}


def test_the_registry_is_a_table_and_not_an_implementation() -> None:
    tree = ast.parse(pathlib.Path(registry.__file__).read_text())
    defined = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert defined == {"enabled_features"}, (
        f"src/opik_mcp/features/registry.py defines {sorted(defined)}; it should only "
        "filter the table by settings. Feature logic belongs under the feature's package."
    )


def test_the_ai_spend_feature_is_registered() -> None:
    assert FEATURE_REGISTRY[AI_SPEND_FEATURE] is AI_SPEND


@pytest.mark.parametrize("name", sorted(FEATURE_REGISTRY))
def test_a_registered_feature_declares_what_the_framework_reads(name: str) -> None:
    feature = FEATURE_REGISTRY[name]
    assert feature.name == name
    assert name
    unknown_tools = sorted(set(feature.tool_sentences) - ADVERTISED_TOOLS)
    assert not unknown_tools, (
        f"feature {name!r} adds sentences to {unknown_tools}, "
        f"which are not advertised tools {sorted(ADVERTISED_TOOLS)}."
    )
    assert all(feature.tool_sentences.values())
    assert feature.instructions_paragraph.strip()
    assert not set(feature.skills) & set(skill_names()), (
        f"feature {name!r} names a skill that is also bundled; a feature skill lives outside "
        "src/opik_mcp/skills/."
    )
    assert all(load().strip() for load in feature.skills.values())


def test_enabled_features_follow_the_settings() -> None:
    spend = make_settings(opik_workspace="__ai_spend_org__", opik_mcp_transport="stdio")
    assert enabled_features(spend) == (AI_SPEND,)
    assert enabled_features(make_settings(opik_workspace="my-team")) == ()
