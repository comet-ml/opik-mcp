"""Conformance tests — spec §7.3 'Conformance tests'.

These pin the *public MCP contract* for the universal write surface. Unit
tests cover module internals; this file proves the FastMCP-registered
tools advertise the right shape over an actual MCP session and that the
registry-generated description matches what the server ships byte-for-byte.

Failures here mean a client's schema cache, codegen, or strict-mode
validator will reject the server — never let these red.
"""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import WRITE_OPERATION_ENUM, mcp
from opik_mcp.writes import SCHEMA_TOOL_DESCRIPTION, WRITE_TOOL_DESCRIPTION
from opik_mcp.writes.registry import WRITE_OPERATIONS, WRITE_REGISTRY


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- tools/list advertises write + schema ------------------------------- #


@pytest.mark.anyio
async def test_write_and_schema_tools_listed() -> None:
    """tools/list over the wire MUST include both new tools."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert "write" in names, f"write not advertised; got {names}"
    assert "schema" in names, f"schema not advertised; got {names}"


# --- write inputSchema is strict-mode-clean ----------------------------- #


@pytest.mark.anyio
async def test_write_input_schema_enumerates_operations() -> None:
    """The `operation` parameter MUST be an enum of exactly the registered ops."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    write_tool = next(t for t in tools.tools if t.name == "write")
    op_schema = write_tool.inputSchema["properties"]["operation"]
    # Pydantic/FastMCP may emit the enum either inline or via $defs+$ref.
    enum_values = _resolve_enum_from_schema(op_schema, write_tool.inputSchema)
    assert set(enum_values) == set(WRITE_OPERATIONS), (
        f"write.operation enum drift: schema={sorted(enum_values)} "
        f"registry={sorted(WRITE_OPERATIONS)}"
    )


@pytest.mark.anyio
async def test_write_input_schema_data_accepts_object_or_array() -> None:
    """`data` MUST accept either object or array — the universal payload shape."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    write_tool = next(t for t in tools.tools if t.name == "write")
    data_schema = write_tool.inputSchema["properties"]["data"]
    # FastMCP renders `dict | list` as `anyOf: [{type:object}, {type:array}]`.
    options = data_schema.get("anyOf") or data_schema.get("oneOf") or [data_schema]
    types = {opt.get("type") for opt in options if "type" in opt}
    assert {"object", "array"}.issubset(types), (
        f"`data` must accept both object and array; got types={types} schema={data_schema}"
    )


@pytest.mark.anyio
async def test_write_input_schema_no_additional_required() -> None:
    """Only `operation` and `data` are required — others (idempotency_key, dry_run) optional."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    write_tool = next(t for t in tools.tools if t.name == "write")
    required = set(write_tool.inputSchema.get("required", []))
    assert required == {"operation", "data"}, (
        f"write.required drift — expected exactly operation+data, got {required}"
    )


# --- description prose is byte-identical to registry-generated ---------- #


@pytest.mark.anyio
async def test_write_tool_description_byte_identical() -> None:
    """Description shipped over the wire MUST equal the registry-generated string.

    Drift here means someone hardcoded a description in server.py and bypassed
    the registry — that's a teaching-surface regression.
    """
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    write_tool = next(t for t in tools.tools if t.name == "write")
    assert write_tool.description == WRITE_TOOL_DESCRIPTION


@pytest.mark.anyio
async def test_schema_tool_description_byte_identical() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        tools = await session.list_tools()
    schema_tool = next(t for t in tools.tools if t.name == "schema")
    assert schema_tool.description == SCHEMA_TOOL_DESCRIPTION


# --- WRITE_OPERATION_ENUM mirrors the registry --------------------------- #


def test_write_operation_enum_matches_registry() -> None:
    """server.WRITE_OPERATION_ENUM MUST enumerate the same set as the registry.

    The enum is derived at module load from ``WRITE_OPERATIONS`` so drift is
    structurally impossible; this test is a belt-and-braces pin in case the
    server.py ever switches to a hand-written list again.
    """
    assert set(WRITE_OPERATION_ENUM) == set(WRITE_OPERATIONS)


# --- schema(operation) round-trips against the Pydantic model ----------- #


@pytest.mark.parametrize("operation", WRITE_OPERATIONS)
@pytest.mark.anyio
async def test_schema_call_matches_registry_model(operation: str) -> None:
    """`schema(operation)` MUST return the Pydantic model's own JSON Schema.

    Drift between the schema tool's output and the model means the validation
    error's `expected_schema` would lie — recovery loops would break.
    """
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.call_tool("schema", {"operation": operation})
    body = _decode_tool_text(result)
    pyd_schema = WRITE_REGISTRY[operation].pydantic_model.model_json_schema()
    assert body["schema"] == pyd_schema, (
        f"{operation}: schema() output drifted from model_json_schema()"
    )
    assert body["operation"] == operation
    assert body["example"] == WRITE_REGISTRY[operation].example
    assert body["oauth_scope"] == WRITE_REGISTRY[operation].oauth_scope


# --- validation_failed embeds the same expected_schema as schema() ------ #


@pytest.mark.anyio
async def test_validation_failed_expected_schema_matches_schema_tool() -> None:
    """A `validation_failed` from `write` MUST embed the same JSON Schema that
    `schema(operation)` returns — the two teaching surfaces agree.

    We trigger `span.create` with a missing `trace_id` to force Stage 2 failure,
    parse the ToolError body, and diff `expected_schema` against the schema
    tool's response.
    """
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        write_result = await session.call_tool(
            "write",
            {
                "operation": "span.create",
                "data": {"name": "s", "start_time": "2026-05-18T12:00:00Z"},
            },
        )
        schema_result = await session.call_tool("schema", {"operation": "span.create"})

    assert write_result.isError, "expected isError=true for missing trace_id"
    write_body = _decode_tool_text(write_result)
    schema_body = _decode_tool_text(schema_result)
    assert write_body["error"] == "validation_failed"
    assert write_body["expected_schema"] == schema_body["schema"], (
        "validation_failed.expected_schema drifted from schema() — recovery loop would fail"
    )
    assert write_body["example"] == schema_body["example"]


@pytest.mark.anyio
async def test_unknown_operation_returns_structured_envelope() -> None:
    """An operation outside the registry MUST surface as our structured
    ``unknown_operation`` envelope — same shape as every other write failure
    so an LLM's recovery loop sees one consistent error format.

    The boundary intentionally accepts ``str`` (with the enum advertised via
    ``json_schema_extra``) so unknown values flow into Stage 1 of the
    dispatcher instead of being rejected as Pydantic ``literal_error``.
    """
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.call_tool("write", {"operation": "trace.delete", "data": {}})
    assert result.isError
    body = _decode_tool_text(result)
    assert body["error"] == "unknown_operation"
    assert body["operation"] == "trace.delete"
    valid_ops = body["valid_operations"]
    assert isinstance(valid_ops, list)
    assert set(valid_ops) == set(WRITE_OPERATIONS)


# --- helpers ------------------------------------------------------------ #


def _decode_tool_text(result: object) -> dict[str, object]:
    """Extract the first text content block and JSON-decode it.

    FastMCP returns success and error envelopes as text-content blocks; the
    structuredContent route isn't universally supported (see errors.py docstring).
    For tool errors, FastMCP prefixes the body with ``Error executing tool <name>: ``
    — strip that to recover the JSON.
    """
    contents = getattr(result, "content", None)
    assert contents, f"expected content blocks on tool result; got {result!r}"
    first = contents[0]
    text = getattr(first, "text", None)
    assert isinstance(text, str), f"expected text content block, got {first!r}"
    body = text
    marker = ": "
    if text.startswith("Error executing tool ") and marker in text:
        body = text.split(marker, 1)[1]
    decoded: dict[str, object] = json.loads(body)
    return decoded


def _resolve_enum_from_schema(
    field_schema: dict[str, object], full_schema: dict[str, object]
) -> list[str]:
    """Pull the enum list out of a property schema that may use $ref + $defs."""
    if "enum" in field_schema:
        enum_vals = field_schema["enum"]
        assert isinstance(enum_vals, list)
        return [str(v) for v in enum_vals]
    ref = field_schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = full_schema.get("$defs", {})
        assert isinstance(defs, dict)
        target = defs.get(ref.removeprefix("#/$defs/"))
        if isinstance(target, dict) and "enum" in target:
            return [str(v) for v in target["enum"]]
    raise AssertionError(
        f"could not resolve enum from {field_schema!r} (full schema keys: {list(full_schema)})"
    )
