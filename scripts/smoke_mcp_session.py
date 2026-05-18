"""Real-MCP smoke run: drives the in-process FastMCP server with a real client
session and exercises each write operation in dry_run mode.

Mirrors what an LLM client (Claude Desktop, etc.) would do — it calls
``tools/list`` to discover the surface, ``schema(operation)`` to fetch
the JSON Schema, then ``write(operation, data, dry_run=True)`` for each
of the 10 operations. Prints the path + body the BE *would* receive.

Run: ``uv run python scripts/smoke_mcp_session.py``
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.server import mcp


def _decode(content: Any) -> Any:
    """FastMCP returns a list of content blocks; first block is text/json."""
    if not content:
        return None
    text = content[0].text  # type: ignore[union-attr]
    if text.startswith("Error executing tool ") and ": " in text:
        text = text.split(": ", 1)[1]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _now() -> str:
    return "2026-05-18T12:00:00Z"


def _uuid() -> str:
    return str(uuid4())


async def main() -> None:
    trace_id = _uuid()
    span_id = _uuid()
    thread_id = _uuid()
    suite_id = _uuid()
    suite_item_id = _uuid()
    experiment_id = _uuid()

    cases: list[tuple[str, Any]] = [
        ("trace.create", {"name": "openai.chat", "start_time": _now(), "id": trace_id}),
        ("trace.update", {"id": trace_id, "end_time": _now(), "output": {"text": "hi"}}),
        (
            "span.create",
            {
                "id": span_id,
                "trace_id": trace_id,
                "name": "openai.chat",
                "type": "llm",
                "start_time": _now(),
            },
        ),
        (
            "score.create",
            {
                "target": "trace",
                "target_id": trace_id,
                "name": "helpfulness",
                "value": 0.8,
            },
        ),
        (
            "comment.create",
            {"target": "trace", "target_id": trace_id, "text": "looks good"},
        ),
        (
            "prompt_version.save",
            {"name": "support_reply", "template": "Hi {{name}}", "commit": "v1"},
        ),
        # NEW: renamed surface
        (
            "test_suite.create",
            {"name": "smoke_suite_001", "description": "smoke test"},
        ),
        (
            "test_suite_item.upsert",
            {
                "test_suite_name": "smoke_suite_001",
                "items": [{"input": {"q": "ping"}, "expected_output": {"a": "pong"}}],
            },
        ),
        (
            "experiment.create",
            {"test_suite_name": "smoke_suite_001", "name": "baseline"},
        ),
        (
            "experiment_item.create",
            {
                "experiment_items": [
                    {
                        "experiment_id": experiment_id,
                        "test_suite_item_id": suite_item_id,
                        "trace_id": trace_id,
                    }
                ]
            },
        ),
    ]

    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()

        print("\n=== tools/list (MCP surface) ===\n")
        tools = await session.list_tools()
        names = sorted(t.name for t in tools.tools)
        print(json.dumps(names, indent=2))

        print("\n=== schema(test_suite.create) ===\n")
        sch = await session.call_tool("schema", {"operation": "test_suite.create"})
        sch_body = _decode(sch.content)
        # Print only the headline + scope so output stays readable.
        if isinstance(sch_body, dict):
            print(
                json.dumps(
                    {
                        "operation": sch_body.get("operation"),
                        "oauth_scope": sch_body.get("oauth_scope"),
                        "supports_batch": sch_body.get("supports_batch"),
                        "example": sch_body.get("example"),
                        "required": sch_body.get("schema", {}).get("required"),
                    },
                    indent=2,
                )
            )

        print("\n=== write(...) dry_run per operation ===\n")
        for op, data in cases:
            result = await session.call_tool(
                "write",
                {"operation": op, "data": data, "dry_run": True},
            )
            body = _decode(result.content)
            tag = "ERR " if result.isError else "OK  "
            wc = body.get("would_call") if isinstance(body, dict) else None
            print(f"{tag} {op:28s} -> {wc}")
            if result.isError:
                print(json.dumps(body, indent=2))

        # --- targeted recovery probe: thread-score singleton must surface array example ---
        print("\n=== score.create target='thread' singleton (expect validation_failed w/ array example) ===\n")
        bad = await session.call_tool(
            "write",
            {
                "operation": "score.create",
                "data": {
                    "target": "thread",
                    "target_id": thread_id,
                    "name": "helpfulness",
                    "value": 0.5,
                },
            },
        )
        body = _decode(bad.content)
        if isinstance(body, dict):
            example = body.get("example")
            ex_tid = example[0].get("target_id") if isinstance(example, list) and example else None
            print(json.dumps(
                {
                    "error": body.get("error"),
                    "issue_codes": [i.get("code") for i in body.get("issues", [])],
                    "example_is_array": isinstance(example, list),
                    "example_carries_callers_thread_id": ex_tid == thread_id,
                },
                indent=2,
            ))

        # --- targeted: test_suite_item.upsert top-level array MUST be rejected (no silent loss) ---
        print("\n=== test_suite_item.upsert top-level array (expect batch_unsupported) ===\n")
        bad2 = await session.call_tool(
            "write",
            {
                "operation": "test_suite_item.upsert",
                "data": [
                    {"test_suite_name": "a", "items": [{"input": {"q": "1"}}]},
                    {"test_suite_name": "b", "items": [{"input": {"q": "2"}}]},
                ],
            },
        )
        body = _decode(bad2.content)
        if isinstance(body, dict):
            print(json.dumps(
                {
                    "isError": bad2.isError,
                    "error": body.get("error"),
                    "issue_codes": [i.get("code") for i in body.get("issues", [])],
                },
                indent=2,
            ))

        # --- targeted: experiment.create with no test_suite_* → test_suite_parent_missing ---
        print("\n=== experiment.create with neither test_suite_name nor test_suite_id ===\n")
        bad3 = await session.call_tool(
            "write",
            {"operation": "experiment.create", "data": {"name": "exp-x"}},
        )
        body = _decode(bad3.content)
        if isinstance(body, dict):
            print(json.dumps(
                {
                    "isError": bad3.isError,
                    "error": body.get("error"),
                    "issue_codes": [i.get("code") for i in body.get("issues", [])],
                },
                indent=2,
            ))

        # --- targeted: experiment.create with BOTH → test_suite_parent_conflict ---
        print("\n=== experiment.create with both test_suite_name AND test_suite_id ===\n")
        bad4 = await session.call_tool(
            "write",
            {
                "operation": "experiment.create",
                "data": {
                    "name": "exp-y",
                    "test_suite_name": "x",
                    "test_suite_id": suite_id,
                },
            },
        )
        body = _decode(bad4.content)
        if isinstance(body, dict):
            print(json.dumps(
                {
                    "isError": bad4.isError,
                    "error": body.get("error"),
                    "issue_codes": [i.get("code") for i in body.get("issues", [])],
                },
                indent=2,
            ))

        # --- targeted: legacy dataset.create MUST be rejected at MCP Literal boundary ---
        print("\n=== legacy dataset.create (expect MCP-layer rejection at Literal) ===\n")
        legacy = await session.call_tool(
            "write",
            {"operation": "dataset.create", "data": {"name": "x"}, "dry_run": True},
        )
        body = _decode(legacy.content)
        rejected = legacy.isError
        print(json.dumps(
            {
                "isError": rejected,
                "preview": (str(body)[:300] if rejected else "REGRESSION — accepted!"),
            },
            indent=2,
        ))


if __name__ == "__main__":
    asyncio.run(main())
