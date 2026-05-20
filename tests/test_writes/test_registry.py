"""Registry contract tests — spec §7.1 'Registry contract'.

The registry is the single source of truth for the write tool's enum,
descriptions, and OAuth scope mapping. These tests guard against drift
between the registry and the parts of the surface that consume it (the
``write`` tool's inputSchema enum, the tool's description prose, and the
JSON Schema that ``schema(operation)`` returns).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from opik_mcp.writes import (
    SCHEMA_TOOL_DESCRIPTION,
    WRITE_OPERATIONS,
    WRITE_REGISTRY,
    WRITE_TOOL_DESCRIPTION,
)
from opik_mcp.writes.models import EXAMPLES, MODELS

# --- enum agreement ------------------------------------------------------ #


def test_registry_keys_match_operations_tuple() -> None:
    """``WRITE_OPERATIONS`` and ``WRITE_REGISTRY`` keys must agree."""
    assert tuple(WRITE_REGISTRY.keys()) == WRITE_OPERATIONS


def test_registry_keys_match_server_enum() -> None:
    """server.WRITE_OPERATION_ENUM must enumerate the same operations as the
    registry. The enum is derived from ``WRITE_OPERATIONS`` at module load,
    so drift is structurally impossible — this is a belt-and-braces pin.
    """
    # Import lazily so we don't pay server-init cost when this module is
    # imported by other tests for fixtures.
    from opik_mcp.server import WRITE_OPERATION_ENUM

    assert set(WRITE_OPERATION_ENUM) == set(WRITE_OPERATIONS)


def test_registry_keys_match_models_table() -> None:
    """Every registry entry's pydantic model must come from MODELS."""
    for name in WRITE_OPERATIONS:
        assert WRITE_REGISTRY[name].pydantic_model is MODELS[name]


def test_registry_keys_match_examples_table() -> None:
    for name in WRITE_OPERATIONS:
        assert WRITE_REGISTRY[name].example == EXAMPLES[name]


# --- per-entry sanity --------------------------------------------------- #


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_each_entry_has_valid_example(name: str) -> None:
    """The bundled example must validate against the entry's own model."""
    op = WRITE_REGISTRY[name]
    op.pydantic_model.model_validate(op.example)


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_each_entry_has_nonempty_description(name: str) -> None:
    op = WRITE_REGISTRY[name]
    assert op.description.strip(), f"{name}: description is empty"


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_each_entry_has_valid_method(name: str) -> None:
    op = WRITE_REGISTRY[name]
    assert op.method in {"POST", "PUT", "PATCH"}, f"{name}: unexpected method {op.method!r}"


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_pydantic_model_round_trips_json_schema(name: str) -> None:
    """``model_json_schema()`` must round-trip through draft-07 JSON.

    We don't validate the schema against a draft-07 meta-schema (heavy
    dependency); the JSON round-trip catches non-serializable defaults
    (e.g. ``datetime.now`` literals) which is the realistic failure mode.
    """
    op = WRITE_REGISTRY[name]
    schema = op.pydantic_model.model_json_schema()
    # Must serialize cleanly.
    json.dumps(schema)
    # Must be an object schema with properties.
    assert schema.get("type") == "object"
    assert "properties" in schema


# --- description prose contains every operation ------------------------- #


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_description_mentions_every_operation(name: str) -> None:
    """The tool description string must list every registered operation by name."""
    assert name in WRITE_TOOL_DESCRIPTION, (
        f"{name!r} missing from WRITE_TOOL_DESCRIPTION — registry/description drift."
    )


def test_schema_description_nonempty() -> None:
    assert SCHEMA_TOOL_DESCRIPTION.strip()


# --- batch endpoint consistency ----------------------------------------- #


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_supports_batch_implies_endpoint_pair(name: str) -> None:
    """If batch is supported and the BE has a distinct batch path, both must be set.

    ``experiment_item.create`` is an always-envelope operation with
    ``supports_batch=True`` but no separate ``batch_endpoint`` because the
    singleton path already accepts the envelope. ``test_suite_item.upsert``
    is also always-envelope but uses ``supports_batch=False`` to reject a
    top-level array (which would silently lose items past index 0). Both
    shapes are deliberate, so this assertion only checks the *false*
    direction (batch_endpoint set → must support batch).
    """
    op = WRITE_REGISTRY[name]
    if op.batch_endpoint is not None:
        assert op.supports_batch, f"{name}: batch_endpoint set but supports_batch=False"


# --- OAuth scope set ---------------------------------------------------- #


def test_oauth_scopes_are_known() -> None:
    """Every operation's scope must be one of the five §5 scopes."""
    from opik_mcp.writes.scopes import ALL_WRITE_SCOPES

    for op in WRITE_REGISTRY.values():
        assert op.oauth_scope in ALL_WRITE_SCOPES, (
            f"{op.name}: unknown scope {op.oauth_scope!r}; allowed={sorted(ALL_WRITE_SCOPES)}"
        )


# --- helpers ------------------------------------------------------------ #


def _model(name: str) -> type[BaseModel]:
    return WRITE_REGISTRY[name].pydantic_model


def _example(name: str) -> dict[str, Any]:
    return WRITE_REGISTRY[name].example
