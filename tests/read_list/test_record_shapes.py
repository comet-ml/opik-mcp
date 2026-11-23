"""``record_shapes.json`` covers every entity, and the generator reads the spec correctly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.generate_record_shapes import build, changes, load_spec, render, write_if_changed

from opik_mcp.read_list import record_shapes
from opik_mcp.read_list.registry import ENTITY_REGISTRY

#: Entities that list rows but name no schema, and why.
NO_SCHEMA: dict[str, str] = {
    "score_name": "the server builds its rows from a list of names",
    "online_rule": "each row is one of several rule types (oneOf in the spec)",
}

_REGENERATE = "Run `make record-shapes` and commit src/opik_mcp/read_list/record_shapes.json."


def test_every_schema_an_entity_names_is_in_the_shapes_file() -> None:
    named = {
        (entity, schema)
        for entity, handler in ENTITY_REGISTRY.items()
        for schema in (handler.list_record_schema, *handler.read_record_schemas.values())
        if schema is not None
    }
    missing = sorted(pair for pair in named if pair[1] not in record_shapes.schema_names())
    assert named, "no entity names a schema; the guard below would pass on nothing"
    assert not missing, f"entities name schemas the file does not hold: {missing}. {_REGENERATE}"


def test_every_entity_that_lists_backend_rows_names_its_schema() -> None:
    unnamed = sorted(
        entity
        for entity, handler in ENTITY_REGISTRY.items()
        if handler.list_fn is not None and handler.list_record_schema is None
    )
    assert unnamed == sorted(NO_SCHEMA), (
        f"list rows with no schema: {unnamed}. Set list_record_schema on the entity's "
        "EntityHandler in read_list/entities/<entity>, or add it to NO_SCHEMA in this file "
        "with the reason. Without it, fields= refuses a field the backend left empty."
    )


def test_the_shapes_file_says_which_spec_commit_it_comes_from() -> None:
    shapes_file = Path(record_shapes.__file__).with_name(record_shapes.SHAPES_FILE)
    source = json.loads(shapes_file.read_text())["source"]
    assert len(source["commit"]) == 40, f"source.commit is {source['commit']!r}. {_REGENERATE}"
    assert len(source["sha256"]) == 64, f"source.sha256 is {source['sha256']!r}. {_REGENERATE}"


_SPEC = b"""
components:
  schemas:
    Thing:
      type: object
      properties:
        id: {type: string}
        kind: {type: string, enum: [a, "="]}
        error_info: {$ref: '#/components/schemas/Error'}
        metadata: {$ref: '#/components/schemas/Json'}
        usage: {type: object, additionalProperties: {type: integer}}
        feedback_scores: {type: array, items: {$ref: '#/components/schemas/Score'}}
        tags: {type: array, items: {type: string}}
    Error:
      type: object
      properties: {message: {type: string}, traceback: {type: string}}
    Json:
      type: object
      anyOf: [{type: object}, {type: string}]
    Score:
      type: object
      properties: {name: {type: string}, value: {type: number}}
"""


def test_the_generator_reads_each_kind_of_property_the_projection_needs() -> None:
    spec = load_spec(_SPEC)
    assert isinstance(spec, dict)
    assert build(spec, ["Thing"]) == {
        "Thing": {
            "error_info": {"kind": "object", "fields": ["message", "traceback"]},
            "feedback_scores": {"kind": "named_list"},
            "id": {"kind": "scalar"},
            "kind": {"kind": "scalar", "enum": ["a", "="]},
            "metadata": {"kind": "open"},
            "tags": {"kind": "list"},
            "usage": {"kind": "open"},
        }
    }


def test_the_generator_refuses_a_schema_the_spec_does_not_have() -> None:
    spec = load_spec(_SPEC)
    assert isinstance(spec, dict)
    with pytest.raises(SystemExit, match="Gone"):
        build(spec, ["Gone"])


def test_the_change_summary_names_what_each_schema_gained_and_lost() -> None:
    old = {"Thing": {"id": {"kind": "scalar"}, "old": {"kind": "scalar"}}}
    new = {"Thing": {"id": {"kind": "open"}, "new": {"kind": "scalar"}}}
    assert changes(old, new) == ["- `Thing`: added new; removed old; changed id"]
    assert changes(new, json.loads(json.dumps(new))) == []


def test_the_file_has_one_line_per_field_and_reads_back_as_written() -> None:
    spec = load_spec(_SPEC)
    assert isinstance(spec, dict)
    schemas = build(spec, ["Thing", "Error"])
    text = render({"commit": "c"}, schemas)
    assert json.loads(text) == {"source": {"commit": "c"}, "schemas": schemas}
    assert len(text.splitlines()) == 5 + sum(len(fields) + 2 for fields in schemas.values())


def test_a_new_spec_commit_with_no_field_change_leaves_the_file_alone(tmp_path: Path) -> None:
    """Otherwise the daily workflow pushes to its PR with nothing to review."""
    spec = load_spec(_SPEC)
    assert isinstance(spec, dict)
    schemas = build(spec, ["Thing"])
    output = tmp_path / "record_shapes.json"
    write_if_changed(output, {"commit": "first"}, schemas)

    assert write_if_changed(output, {"commit": "second"}, schemas) == []
    assert json.loads(output.read_text())["source"] == {"commit": "first"}

    fewer = {"Thing": {k: v for k, v in schemas["Thing"].items() if k != "tags"}}
    assert write_if_changed(output, {"commit": "third"}, fewer) == ["- `Thing`: removed tags"]
    assert json.loads(output.read_text())["source"] == {"commit": "third"}
