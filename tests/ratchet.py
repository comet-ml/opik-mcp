# Shared by the read_list and writes guard tests. A root module may name only
# the entities its entry in ratchets.json already names, and the list only
# shrinks: a change that needs a root table adds a hook instead, and a
# paid-off entry left in the list fails. Some entity names are ordinary words
# ("issue", "score", "comment", "project", "prompt"), so an unrelated literal
# can trip it; rename the literal.

from __future__ import annotations

import ast
import json
from collections.abc import Collection, Mapping
from pathlib import Path

RATCHETS = Path(__file__).with_name("ratchets.json")


def allowlist(key: str) -> dict[str, frozenset[str]]:
    entries: dict[str, list[str]] = json.loads(RATCHETS.read_text())[key]
    return {module: frozenset(names) for module, names in entries.items()}


def string_literals(path: Path, names: Collection[str]) -> set[str]:
    return {
        node.value
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names
    }


def assert_no_new_names(
    root: Path,
    allowlist: Mapping[str, frozenset[str]],
    names: Collection[str],
    *,
    exempt: Collection[str],
    where_it_belongs: str,
    allowlist_name: str,
) -> None:
    for path in sorted(root.glob("*.py")):
        if path.stem in exempt:
            continue
        new = string_literals(path, names) - allowlist.get(path.stem, frozenset())
        assert not new, (
            f"{path.name} names {sorted(new)}. {where_it_belongs} "
            "(.claude/rules/architecture.md). If no hook exists for this yet, add one; "
            f"{allowlist_name} in tests/ratchets.json only shrinks."
        )


def assert_allowlist_is_current(
    root: Path, allowlist: Mapping[str, frozenset[str]], names: Collection[str]
) -> None:
    for stem, allowed in allowlist.items():
        path = root / f"{stem}.py"
        stale = allowed - (string_literals(path, names) if path.exists() else set())
        assert not stale, f"{stem}.py no longer names {sorted(stale)}: remove it from the list"
