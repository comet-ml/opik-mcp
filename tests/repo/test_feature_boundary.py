"""Feature specifics reach the framework only through the feature registry.

Same rule as entities and the entity registry (docs/decisions/0004): a generic
module that imports a feature package has started to know that feature.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "opik_mcp"
FEATURE_PACKAGE = "opik_mcp.cost_intelligence"
ALLOWED = (
    SRC / "features" / "registry.py",
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


def test_only_the_registry_and_the_feature_import_the_feature_package() -> None:
    sources = sorted(SRC.rglob("*.py"))
    assert (SRC / "features" / "registry.py") in sources
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in sources
        if not any(path == allowed or allowed in path.parents for allowed in ALLOWED)
        and _imports_feature_package(path)
    ]
    assert not offenders, (
        f"{offenders} import opik_mcp.cost_intelligence: feature specifics reach the "
        "framework only through opik_mcp.features.registry. Declare it on the feature's "
        "Feature in src/opik_mcp/cost_intelligence/feature.py and read it from the registry."
    )


def test_the_guard_sees_the_one_import_that_is_allowed() -> None:
    assert _imports_feature_package(SRC / "features" / "registry.py")
