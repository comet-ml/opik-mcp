"""Reads ``record_shapes.json``, which ``make record-shapes`` generates from opik's OpenAPI spec."""

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
    schema = handler.list_record_schema
    base = declared(schema) if schema is not None else NOTHING_DECLARED
    derived = handler.list_derived_fields
    hidden = tuple(f"{name}." for name in derived)
    return DeclaredFields(
        paths=frozenset(p for p in base.paths if not p.startswith(hidden)) | set(derived),
        open_paths=frozenset(p for p in base.open_paths if not p.startswith(hidden)),
    )


def for_read(handler: EntityHandler) -> DeclaredFields:
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
