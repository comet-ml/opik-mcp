"""Finding one case in a large dataset, end to end over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. The in-process suite drives
``run_list`` against a fake client: it proves the wording, the refusals and
the compiled clause, and it cannot see whether the request we send is the one
opik-backend answers. This is where the query string is inspected as the
backend receives it — the ``filters`` array on a route that had never carried
one, and the item route that addresses a case without its dataset.

The stub reads the filters far enough to answer 400 for something that is not
the array the backend deserializes, and no further: which rows a clause
matches is opik-backend's business.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.hermetic.stub_backend import (
    CASE_ID,
    CASE_TRACE_ID,
    SUITE_ID,
    CompareSuite,
    StubBackend,
)

_TIMEOUT_S = 60


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@asynccontextmanager
async def _session(stub: StubBackend) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            "OPIK_URL": f"http://127.0.0.1:{stub.port}/api",
            "OPIK_API_KEY": "stub-key",
            "OPIK_WORKSPACE": "stub-workspace",
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            "OPIK_MCP_LOG_LEVEL": "WARNING",
        },
    )
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


async def _call(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert not result.isError, f"{tool}({args}) was refused: {text}"
    return text


async def _refuse(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert result.isError, f"{tool}({args}) was answered, expected a refusal: {text}"
    return text


def _get(stub: StubBackend, path: str, **params: Any) -> httpx.Response:
    return httpx.get(f"http://127.0.0.1:{stub.port}/api{path}", params=params, timeout=30)


_ITEMS = f"/v1/private/datasets/{SUITE_ID}/items"


# --- the stub's own contract ----------------------------------------------- #


@pytest.mark.hermetic
def test_the_items_route_pages_the_dataset_and_takes_filters(backend: StubBackend) -> None:
    backend.suite = CompareSuite(case_count=2_000)
    clause = [{"field": "data", "key": "question", "operator": "contains", "value": "install"}]

    response = _get(backend, _ITEMS, page=2, size=25, filters=json.dumps(clause))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2_000
    assert len(body["content"]) == 25
    assert backend.one("/items").filters() == clause


@pytest.mark.hermetic
def test_filters_that_are_not_the_backends_array_are_a_400(backend: StubBackend) -> None:
    """``FiltersFactory`` deserializes the parameter and answers 400 when it
    cannot. A stub that shrugged at a malformed array would let a wrongly
    encoded filter pass every test and fail in production."""
    assert _get(backend, _ITEMS, filters="data.question contains install").status_code == 400


@pytest.mark.hermetic
def test_one_case_is_addressed_without_its_dataset(backend: StubBackend) -> None:
    body = _get(backend, f"/v1/private/datasets/items/{CASE_ID}").json()

    assert body["id"] == CASE_ID
    assert body["data"]["expected_answer"] == "Paris"
    assert _get(backend, "/v1/private/datasets/items/not-a-case-id").status_code == 404


# --- through the server ---------------------------------------------------- #


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_a_case_key_reaches_the_backend_as_the_maps_wire_form(
    backend: StubBackend,
) -> None:
    """The acceptance case: one filter, on a key the user named, arriving as
    the MAP clause opik-backend deserializes — field ``data`` with the key
    beside it, not spliced into the field name."""
    backend.suite = CompareSuite(case_count=2_000)

    async with _session(backend) as session:
        out = await _call(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters='data.question contains "install" AND tags contains "regression"',
            size=5,
        )

    assert backend.one("/items").filters() == [
        {"field": "data", "key": "question", "operator": "contains", "value": "install"},
        {"field": "tags", "key": "", "operator": "contains", "value": "regression"},
    ]
    assert out.splitlines()[0] == (
        '[list: dataset_item | filters: data.question contains "install" '
        'AND tags contains "regression"]'
    )
    assert "Found 2000 dataset_items (page 1, showing 5 of 2000)" in out


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_the_source_trace_is_one_call_and_a_comparison_is_none(
    backend: StubBackend,
) -> None:
    """``trace_id`` is how a case is found from the conversation it was made
    from. A comparison on a map key is refused before anything is sent: the
    backend answers 400 for the pair, and there is nothing to learn from it."""
    async with _session(backend) as session:
        await _call(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters=f'trace_id = "{CASE_TRACE_ID}"',
        )
        refusal = await _refuse(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            filters="data.score > 0.5",
        )
        sort_refusal = await _refuse(
            session,
            "list",
            entity_type="dataset_item",
            dataset_id=SUITE_ID,
            sort="created_at desc",
        )

    assert backend.one("/items").filters() == [
        {"field": "trace_id", "key": "", "operator": "=", "value": CASE_TRACE_ID}
    ]
    assert "not valid for 'data' (map)" in refusal
    assert "sort is not supported" in sort_refusal


@pytest.mark.hermetic
@pytest.mark.anyio
async def test_read_returns_the_case_the_table_had_to_cut(backend: StubBackend) -> None:
    """The listing cuts every cell to fit a row and says so; the read of the
    id it printed is the same case with nothing taken out."""
    async with _session(backend) as session:
        table = await _call(
            session, "list", entity_type="dataset_item", dataset_id=SUITE_ID, size=25
        )
        record = await _call(session, "read", entity_type="dataset_item", id=CASE_ID)

    assert "values cut at" in table
    assert "read('dataset_item', id) is the value whole" in table
    notes = json.loads(record.split("\n", 1)[1])["data"]["notes"]
    assert notes.startswith("Case 3: ")
    assert len(notes) > 1_000
    assert notes not in table
