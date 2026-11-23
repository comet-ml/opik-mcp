"""The fields of each backend record, from Opik's OpenAPI spec.

The backend serialises with ``NON_NULL``, so a record's JSON never shows the
fields it leaves empty. ``record_shapes.json`` holds what the spec declares
instead. ``make record-shapes`` writes it, and the ``record-shapes`` workflow
opens a PR when the spec on opik ``main`` changes it. Never edit it by hand.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from importlib import resources
from typing import Final, Literal, TypedDict, cast

from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.projection import NOTHING_DECLARED, DeclaredFields

SHAPES_FILE: Final = "record_shapes.json"

FieldKind = Literal["scalar", "object", "open", "named_list", "list"]


class FieldShape(TypedDict, total=False):
    """One property of a schema, as the generator writes it."""

    kind: FieldKind
    fields: list[str]
    enum: list[str]


class _ShapesFile(TypedDict):
    source: dict[str, str]
    schemas: dict[str, dict[str, FieldShape]]


@functools.cache
def _shapes() -> _ShapesFile:
    text = resources.files(__package__).joinpath(SHAPES_FILE).read_text(encoding="utf-8")
    return cast(_ShapesFile, json.loads(text))


def schema_names() -> frozenset[str]:
    return frozenset(_shapes()["schemas"])


def schema_fields(schema: str) -> Mapping[str, FieldShape]:
    """The properties of ``schema``; a ``KeyError`` names a schema the file does not hold."""
    return _shapes()["schemas"][schema]


def declared(schema: str, *, prefix: str = "") -> DeclaredFields:
    paths: set[str] = set()
    open_paths: set[str] = set()
    for name, shape in schema_fields(schema).items():
        path = f"{prefix}{name}"
        paths.add(path)
        kind = shape.get("kind")
        if kind == "object":
            paths.update(f"{path}.{sub}" for sub in shape.get("fields", ()))
        elif kind in ("open", "named_list"):
            open_paths.add(path)
    return DeclaredFields(frozenset(paths), frozenset(open_paths))


def for_list(handler: EntityHandler) -> DeclaredFields:
    """What ``fields=`` takes on a page of this entity, beyond what its rows carry."""
    schema = handler.list_record_schema
    base = declared(schema) if schema is not None else NOTHING_DECLARED
    derived = handler.list_derived_fields
    # A derived column replaces the value it is named after (an experiment's
    # prompt_version object becomes a label), so the object's sub-paths are gone.
    hidden = tuple(f"{name}." for name in derived)
    return DeclaredFields(
        paths=frozenset(p for p in base.paths if not p.startswith(hidden)) | set(derived),
        open_paths=frozenset(p for p in base.open_paths if not p.startswith(hidden)),
    )


def for_read(handler: EntityHandler) -> DeclaredFields:
    """What ``fields=`` takes on a read of this entity, beyond what the record carries."""
    out = NOTHING_DECLARED
    for block, schema in handler.read_record_schemas.items():
        out |= declared(schema, prefix=f"{block}." if block else "")
        if block:
            out |= DeclaredFields(paths=frozenset({block}))
    return out


def reset_for_tests() -> None:
    _shapes.cache_clear()


__all__ = [
    "SHAPES_FILE",
    "FieldKind",
    "FieldShape",
    "declared",
    "for_list",
    "for_read",
    "reset_for_tests",
    "schema_fields",
    "schema_names",
]
