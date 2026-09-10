"""The root of ``read_list`` stays generic, and every entity stays in its own
namespace.

Asked for in review of #187: root modules dispatch, register, or hold logic
every entity shares; an entity's own logic lives under ``entities/``. That is
a property of the layout, and a property nobody checks is one that erodes on
the next ticket, so it is checked here rather than remembered.
"""

from __future__ import annotations

import ast
import pathlib

from opik_mcp.read_list import registry
from opik_mcp.read_list.registry import ENTITY_REGISTRY, LISTABLE_TYPES, READABLE_TYPES

READ_LIST = pathlib.Path(registry.__file__).parent
ENTITIES = READ_LIST / "entities"

#: Modules at the root of ``read_list``. Each is either a dispatcher or
#: something every entity uses; none is about one entity. Pinned as a set so
#: adding a root module is a deliberate act with a reviewer, not a side effect
#: of implementing an entity.
SHARED: frozenset[str] = frozenset(
    {
        "__init__",
        "compression",
        "decorations",
        "errors",
        "handler",
        "list_tool",
        "oql",
        "paging",
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


def test_the_root_holds_nothing_entity_specific() -> None:
    found = {p.stem for p in READ_LIST.glob("*.py")}
    assert found == SHARED, (
        "a module appeared at the root of read_list, or one left it. An "
        "entity's logic belongs in entities/<entity>; only dispatch and "
        "shared machinery live here."
    )


def test_every_registered_entity_declares_itself_in_its_own_namespace() -> None:
    """The registry names an entity once, as an import. Nothing else about it
    is written there."""
    for entity_type in ENTITY_REGISTRY:
        namespace = ENTITIES / entity_type
        module = ENTITIES / f"{entity_type}.py"
        # Two entities are sub-collections of another and share its namespace.
        if entity_type in ("test_suite_item", "prompt_version"):
            continue
        assert namespace.is_dir() or module.is_file(), (
            f"{entity_type} is registered but has no namespace under entities/"
        )


def test_the_registry_is_a_table_and_not_an_implementation() -> None:
    """It may import handlers and hold the two table-level behaviours; a
    fetcher, a list function or a compressor in here means the split has
    started to leak back."""
    tree = ast.parse(pathlib.Path(registry.__file__).read_text())
    defined = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert defined == {"resolve_entity_type", "compress_for"}, (
        f"registry.py defines {sorted(defined)}; it should only resolve an "
        "alias and pick a compressor."
    )
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in tree.body), (
        "an async function in the registry means a backend call lives in the table"
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


def test_the_surfaces_still_cover_every_entity() -> None:
    """The move must not have dropped one on the floor: what was readable and
    listable before the split is what is readable and listable now."""
    assert set(READABLE_TYPES) == {
        "project",
        "trace",
        "span",
        "thread",
        "test_suite",
        "experiment",
        "prompt",
        "agent_insights_issue",
    }
    assert set(LISTABLE_TYPES) == {
        "project",
        "trace",
        "span",
        "thread",
        "test_suite",
        "test_suite_item",
        "experiment",
        "prompt",
        "prompt_version",
        "project_metric",
        "score_name",
        "online_rule",
        "agent_insights_issue",
    }
