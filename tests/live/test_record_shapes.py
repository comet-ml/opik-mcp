from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.seed_e2e_backend import Manifest

from tests.live.conftest import Live

pytestmark = [pytest.mark.live, pytest.mark.anyio]

SHAPES = Path(__file__).resolve().parents[2] / "src/opik_mcp/read_list/record_shapes.json"


def _declared(schema: str) -> set[str]:
    schemas: dict[str, dict[str, object]] = json.loads(SHAPES.read_text())["schemas"]
    return set(schemas[schema])


def _undeclared(record: object, schema: str, *, added: set[str] | None = None) -> list[str]:
    assert isinstance(record, dict), f"the {schema} record is not an object: {record!r}"
    return sorted(set(record) - _declared(schema) - (added or set()))


async def test_a_trace_and_its_span_carry_only_declared_fields(
    mcp: Live, manifest: Manifest
) -> None:
    trace = (await mcp.read("trace", manifest.typical.id)).record()
    extra = _undeclared(trace["trace"], "Trace_Public")
    assert not extra, f"Trace_Public in record_shapes.json lacks {extra}. Run make record-shapes."
    spans = trace["spans"]
    assert isinstance(spans, list), f"trace {manifest.typical.id} spans is not a list"
    assert spans, f"trace {manifest.typical.id} has no spans"
    span = (await mcp.read("span", str(spans[0]["id"]))).record()
    extra = _undeclared(span, "Span_Public", added={"url"})
    assert not extra, f"Span_Public in record_shapes.json lacks {extra}. Run make record-shapes."


async def test_a_thread_carries_only_declared_fields(mcp: Live, manifest: Manifest) -> None:
    thread = (
        await mcp.read("thread", manifest.long_thread.id, project_name=manifest.project_name)
    ).record()
    extra = _undeclared(thread["thread"], "TraceThread")
    assert not extra, f"TraceThread in record_shapes.json lacks {extra}. Run make record-shapes."


async def test_spans_take_fields_the_backend_left_out(mcp: Live, manifest: Manifest) -> None:
    answer = await mcp.list(
        "span",
        project_name=manifest.project_name,
        filters=f'trace_id = "{manifest.typical.id}"',
        fields=["parent_span_id", "error_info"],
        size=50,
    )
    assert not answer.is_error, f"{answer.call} was refused: {answer.text[:300]}"
    assert len(answer.rows()) == manifest.typical.span_count


async def _empty_in_list(
    mcp: Live, manifest: Manifest, entity_type: str, record: object, schema: str
) -> list[str]:
    assert isinstance(record, dict), f"the {schema} record is not an object: {record!r}"
    filled = sorted(
        key
        for key, value in record.items()
        if key in _declared(schema) and value not in (None, "", [], {})
    )
    answer = await mcp.list(
        entity_type,
        project_name=manifest.project_name,
        filters=f'id = "{record["id"]}"',
        fields=filled,
        size=1,
    )
    rows = answer.rows()
    assert len(rows) == 1, f"{answer.call} did not list the record once: {answer.text[:300]}"
    return [key for key in filled if not rows[0].get(key)]


async def test_a_listed_trace_and_span_fill_what_their_reads_fill(
    mcp: Live, manifest: Manifest
) -> None:
    trace = (await mcp.read("trace", manifest.typical.id)).record()
    empty = await _empty_in_list(mcp, manifest, "trace", trace["trace"], "Trace_Public")
    assert not empty, f"list('trace') leaves out {empty}; take them out of Trace_Public's list use"
    spans = trace["spans"]
    assert isinstance(spans, list), f"trace {manifest.typical.id} spans is not a list"
    assert spans, f"trace {manifest.typical.id} has no spans"
    span = (await mcp.read("span", str(spans[0]["id"]))).record()
    empty = await _empty_in_list(mcp, manifest, "span", span, "Span_Public")
    assert not empty, f"list('span') leaves out {empty}; take them out of Span_Public's list use"
