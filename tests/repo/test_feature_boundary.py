"""Feature specifics reach the framework only through the toggle config.

Same rule as entities and the entity registry (docs/decisions/0004): a generic
module that imports a feature package has started to know that feature.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opik_mcp"
FEATURE_PACKAGE = "opik_mcp.cost_intelligence"
TOGGLES = SRC / "features" / "toggles.py"
CONTRIBUTIONS = SRC / "features" / "contributions.py"
#: The two modules that name a feature on purpose: the toggle config (is it on) and
#: the contributions (what it adds). Every other module reads one of their functions.
ALLOWED = (
    TOGGLES,
    CONTRIBUTIONS,
    SRC / "cost_intelligence",
    SRC / "read_list" / "entities" / "spend",
)


def _imports_feature_package(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            if any(a.name.startswith(FEATURE_PACKAGE) for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module.startswith(FEATURE_PACKAGE):
                return True
            if module == "opik_mcp" and any(a.name == "cost_intelligence" for a in node.names):
                return True
    return False


def test_only_the_toggle_config_and_the_feature_import_the_feature_package() -> None:
    sources = sorted(SRC.rglob("*.py"))
    assert TOGGLES in sources
    assert CONTRIBUTIONS in sources
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in sources
        if not any(path == allowed or allowed in path.parents for allowed in ALLOWED)
        and _imports_feature_package(path)
    ]
    assert not offenders, (
        f"{offenders} import opik_mcp.cost_intelligence: feature specifics reach the "
        "framework only through src/opik_mcp/features/ — toggles.py for whether a "
        "feature is on, contributions.py for what it adds. Add a function there and "
        "call it instead."
    )


def test_the_guard_sees_the_imports_that_are_allowed() -> None:
    assert _imports_feature_package(TOGGLES)
    assert _imports_feature_package(CONTRIBUTIONS)


def test_no_root_module_spells_a_feature_name_out() -> None:
    """The import guard above only sees imports, so a root module that *defines* a
    feature's name or its workspace prefix slips past it. That is what config.py did:
    it held the feature name and the ``__ai_spend_`` prefix, so nothing imported the
    feature package and the guard stayed green while a root module named a feature."""
    from opik_mcp.features.toggles import FeatureToggles

    #: ``cost_intelligence_enabled`` -> ``cost_intelligence``.
    names = [f.name.removesuffix("_enabled") for f in fields(FeatureToggles)]
    assert names, "FeatureToggles declares no toggle; this guard would pass vacuously."
    roots = [
        SRC / "config.py",
        SRC / "instructions.py",
        SRC / "skills_catalog.py",
        SRC / "read_list" / "visibility.py",
        SRC / "read_list" / "handler.py",
        SRC / "server" / "tools" / "feature_surface.py",
        SRC / "server" / "tools" / "__init__.py",
    ]
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()} spells out {name!r}"
        for path in roots
        for name in names
        if name in path.read_text()
    ]
    assert not offenders, (
        f"{offenders}: a root module names a feature. The name, what turns it on and "
        "what it contributes all belong to the feature's own package; the framework "
        "reads them through an accessor on FeatureToggles."
    )
