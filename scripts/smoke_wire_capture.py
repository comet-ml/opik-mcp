"""Real-MCP wire-capture smoke: confirms the dispatcher emits the BE's
legacy ``dataset_*`` field names on the wire even when the MCP caller used
``test_suite_*``, and injects ``type='evaluation_suite'`` on test_suite.create.

Uses ``respx`` so the dispatcher's actual HTTP layer fires — same code path
a live BE call would take — but we intercept and inspect the request body.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx
import respx
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.opik_client import OpikClient
from opik_mcp.server import mcp

OPIK_BASE = "https://opik.test"


def _decode(content: Any) -> Any:
    if not content:
        return None
    text = content[0].text  # type: ignore[union-attr]
    if text.startswith("Error executing tool ") and ": " in text:
        text = text.split(": ", 1)[1]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


async def main() -> None:
    test_client = OpikClient(base_url=OPIK_BASE, api_key="k", workspace="ws")
    from opik_mcp.writes import dispatch as _disp

    _orig = _disp.run_write

    async def _patched(**kw: Any) -> Any:
        if kw.get("client") is None:
            kw["client"] = test_client
        return await _orig(**kw)

    # Monkey-patch the write_tool dispatch import so all session.call_tool
    # calls flow through our respx-mocked client.
    import opik_mcp.writes.write_tool as _wt

    _wt._dispatch = _patched  # type: ignore[attr-defined]

    suite_item_id = str(uuid4())
    experiment_id = str(uuid4())
    trace_id = str(uuid4())

    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()

        # --- test_suite.create -------------------------------------------- #
        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.post("/v1/private/datasets").mock(
                return_value=httpx.Response(201, json={"id": "ds-1"})
            )
            await session.call_tool(
                "write",
                {
                    "operation": "test_suite.create",
                    "data": {"name": "smoke_001", "description": "wire-check"},
                },
            )
            sent = json.loads(route.calls.last.request.read())
            print("test_suite.create →", json.dumps(sent, indent=2))
            assert sent.get("type") == "evaluation_suite", "missing type=evaluation_suite!"

        # --- test_suite_item.upsert --------------------------------------- #
        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.put("/v1/private/datasets/items").mock(
                return_value=httpx.Response(204)
            )
            await session.call_tool(
                "write",
                {
                    "operation": "test_suite_item.upsert",
                    "data": {
                        "test_suite_name": "smoke_001",
                        "items": [
                            {"input": {"q": "ping"}, "expected_output": {"a": "pong"}}
                        ],
                    },
                },
            )
            sent = json.loads(route.calls.last.request.read())
            print("test_suite_item.upsert →", json.dumps(sent, indent=2))
            assert "dataset_name" in sent, "MCP test_suite_name should translate to wire dataset_name"
            assert "test_suite_name" not in sent, "test_suite_name should NOT leak to wire"

        # --- experiment.create -------------------------------------------- #
        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.post("/v1/private/experiments").mock(
                return_value=httpx.Response(201, json={"id": "exp-1"})
            )
            await session.call_tool(
                "write",
                {
                    "operation": "experiment.create",
                    "data": {"test_suite_name": "smoke_001", "name": "baseline"},
                },
            )
            sent = json.loads(route.calls.last.request.read())
            print("experiment.create →", json.dumps(sent, indent=2))
            assert "dataset_name" in sent, "experiment.create must send dataset_name on wire"

        # --- experiment_item.create --------------------------------------- #
        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.post("/v1/private/experiments/items").mock(
                return_value=httpx.Response(204)
            )
            await session.call_tool(
                "write",
                {
                    "operation": "experiment_item.create",
                    "data": {
                        "experiment_items": [
                            {
                                "experiment_id": experiment_id,
                                "test_suite_item_id": suite_item_id,
                                "trace_id": trace_id,
                            }
                        ]
                    },
                },
            )
            sent = json.loads(route.calls.last.request.read())
            print("experiment_item.create →", json.dumps(sent, indent=2))
            item0 = sent["experiment_items"][0]
            assert "dataset_item_id" in item0, "must translate test_suite_item_id → dataset_item_id"
            assert "test_suite_item_id" not in item0, "test_suite_item_id should NOT leak"

    print("\nALL WIRE TRANSLATIONS VERIFIED ✓")


if __name__ == "__main__":
    asyncio.run(main())
