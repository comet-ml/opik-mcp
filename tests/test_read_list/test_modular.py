"""The root of ``read_list`` stays generic, and every entity stays in its own
namespace.

Asked for in review of #187: root modules dispatch, register, or hold logic
every entity shares; an entity's own logic lives under ``entities/``. That is
a property of the layout, and a property nobody checks is one that erodes on
the next ticket, so it is checked here rather than remembered.

What these tests do *not* claim: that nothing entity-shaped is left at the
root. Several shared modules are keyed by entity because the thing they
describe is per-entity by nature — the OQL field tables, the sortable field
lists, the URI patterns. Splitting those would scatter one grammar across
thirteen files. The line drawn here is about *code paths*, not about tables.
"""

from __future__ import annotations

import ast
import pathlib

from opik_mcp.read_list import registry
from opik_mcp.read_list.registry import ENTITY_REGISTRY

READ_LIST = pathlib.Path(registry.__file__).parent
ENTITIES = READ_LIST / "entities"

#: Modules at the root of ``read_list``. Each is either a dispatcher or
#: something more than one entity uses; none is one entity's implementation.
#: Pinned as a set so that adding a root module is a deliberate act with a
#: reviewer, and so that an entity module cannot quietly appear beside them.
SHARED: frozenset[str] = frozenset(
    {
        "__init__",
        "size",
        "slim",
        "decorations",
        "errors",
        "handler",
        "list_tool",
        "oql",
        "paging",
        "project_names",
        "project_scope",
        "read_tool",
        "reference",
        "registry",
        "sorting",
        "ui_links",
        "unsupported",
        "uri",
        "window",
    }
)


def _entity_modules() -> list[pathlib.Path]:
    """Every entity module: a file under ``entities/``, or a package's init."""
    return [
        path
        for path in ENTITIES.rglob("*.py")
        if path.name != "__init__.py" or path.parent != ENTITIES
    ]


def _imports(path: pathlib.Path) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def test_the_root_holds_no_entity_module() -> None:
    found = {p.stem for p in READ_LIST.glob("*.py")}
    assert found == SHARED, (
        "a module appeared at the root of read_list, or one left it. An "
        "entity's logic belongs in entities/<entity>; only dispatch and what "
        "more than one entity uses lives here."
    )


def test_no_entity_reaches_into_another_entity() -> None:
    """The leak this layout exists to prevent, and the one a review caught:
    the metric runner was importing the project's vocabulary module for the
    names it checks a ``series`` against. A fact two entities need is shared,
    so it moved to the root; an import across two namespaces means the next
    one did not."""
    for path in _entity_modules():
        own = path.relative_to(ENTITIES).parts[0].removesuffix(".py")
        for module in _imports(path):
            prefix = "opik_mcp.read_list.entities."
            if not module.startswith(prefix):
                continue
            other = module[len(prefix) :].split(".")[0]
            assert other == own, f"{path.name} imports entities.{other}; share it at the root"


def test_every_entity_module_is_registered() -> None:
    """A module nobody imported is a feature nobody can call. Registering is
    the second half of "add a namespace, then import and register", and it is
    the half a compiler cannot miss for you."""
    declared: set[str] = set()
    for path in _entity_modules():
        source = path.read_text()
        if "EntityHandler(" not in source:
            continue
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                name = getattr(node.value.func, "id", None)
                if name == "EntityHandler":
                    for keyword in node.value.keywords:
                        if keyword.arg == "entity_type" and isinstance(keyword.value, ast.Constant):
                            declared.add(str(keyword.value.value))
    assert declared == set(ENTITY_REGISTRY), (
        f"declared but not registered: {sorted(declared - set(ENTITY_REGISTRY))}; "
        f"registered but not declared in a module: {sorted(set(ENTITY_REGISTRY) - declared)}"
    )


def test_the_registry_is_a_table_and_not_an_implementation() -> None:
    """It may import handlers and hold the one table-level behaviour; a
    fetcher or a list function in here means the split has started to leak
    back."""
    tree = ast.parse(pathlib.Path(registry.__file__).read_text())
    defined = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert defined == {"resolve_entity_type"}, (
        f"registry.py defines {sorted(defined)}; it should only resolve an alias."
    )


def test_the_dispatchers_name_no_entity() -> None:
    """A branch on a literal entity type is what the hooks on ``EntityHandler``
    exist to replace — ``run_fn`` for an entity that answers ``list`` whole,
    ``reference_fn`` for one whose schema is not a field table."""
    known = set(ENTITY_REGISTRY) | {"issue"}
    for name in ("list_tool.py", "read_tool.py"):
        source = (READ_LIST / name).read_text()
        for entity_type in known:
            branch = f'entity_type == "{entity_type}"'
            assert branch not in source, f"{name} branches on {entity_type!r}"


def test_an_entity_that_answers_list_whole_declares_no_list_function() -> None:
    """``project_metric`` used to carry a sentinel ``list_fn`` that existed
    only to make it count as listable. Asking the handler whether it lists is
    what retired it, and this is what stops the sentinel coming back."""
    for entity_type, handler in ENTITY_REGISTRY.items():
        if handler.run_fn is not None:
            assert handler.list_fn is None, (
                f"{entity_type} answers list through run_fn, so a list_fn here "
                "would never be called"
            )
        assert handler.lists == (handler.list_fn is not None or handler.run_fn is not None)


def test_the_handler_contract_imports_no_entity() -> None:
    """``handler.py`` exists so an entity can state its contract without
    importing the table that collects it. If the contract ever imports an
    entity, that direction has reversed."""
    for module in _imports(READ_LIST / "handler.py"):
        assert "entities" not in module, f"handler.py imports {module}"
