"""In-process smoke against the LIVE Opik BE.

Bypasses the stdio MCP subprocess (which caches code at startup) and
drives the dispatcher through the real HTTP client with the real BE.
Uses env vars OPIK_API_KEY, COMET_WORKSPACE, COMET_URL_OVERRIDE.

Run: ``uv run python scripts/smoke_live_be.py``
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import WriteError


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uuid() -> str:
    return str(uuid4())


def _uuid7() -> str:
    """UUID v7 (BE requires v7 for trace/span ids — time-sortable)."""
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    val = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=val))


# All writes require these scopes — granted unconditionally here for the smoke.
ALL_SCOPES = frozenset({
    "trace_span_thread_log",
    "trace_span_thread_annotate",
    "prompt_create",
    "dataset_edit",
    "experiment_create",
})


async def _run(label: str, operation: str, data: Any, *, dry_run: bool = False) -> dict[str, Any] | None:
    print(f"\n--- {label} ({operation}) ---")
    try:
        result = await run_write(
            operation=operation,
            data=data,
            dry_run=dry_run,
            scopes=ALL_SCOPES,
        )
        print(json.dumps(result, indent=2, default=str)[:600])
        return result
    except WriteError as we:
        body = json.loads(we.to_json())
        print("WriteError:", json.dumps(body, indent=2)[:800])
        return None


async def main() -> None:
    assert os.environ.get("OPIK_API_KEY"), "OPIK_API_KEY must be set"
    assert os.environ.get("COMET_WORKSPACE"), "COMET_WORKSPACE must be set"
    print(f"Workspace: {os.environ['COMET_WORKSPACE']}")
    print(f"URL: {os.environ.get('COMET_URL_OVERRIDE', '<unset>')}")

    trace_id = _uuid7()
    span_id = _uuid7()
    project = "Demo chatbot 🤖"

    # 1. trace.create  (uses UUID v7 — BE constraint)
    await _run(
        "1. trace.create",
        "trace.create",
        {
            "id": trace_id,
            "name": "mcp_smoke_trace",
            "start_time": _now(),
            "project_name": project,
            "input": {"messages": [{"role": "user", "content": "smoke test"}]},
            "tags": ["mcp_smoke_20260518"],
        },
    )

    # 2. trace.update  (BE checks project_name matches the existing trace's project)
    await _run(
        "2. trace.update",
        "trace.update",
        {
            "id": trace_id,
            "project_name": project,
            "end_time": _now(),
            "output": {"text": "hello from smoke"},
        },
    )

    # 3. span.create
    await _run(
        "3. span.create",
        "span.create",
        {
            "id": span_id,
            "trace_id": trace_id,
            "project_name": project,
            "name": "openai.chat",
            "type": "llm",
            "start_time": _now(),
            "input": {"prompt": "ping"},
        },
    )

    # 4. score.create (trace)
    await _run(
        "4. score.create trace",
        "score.create",
        {
            "target": "trace",
            "target_id": trace_id,
            "name": "helpfulness",
            "value": 0.85,
            "project_name": project,
        },
    )

    # 5. comment.create (trace)
    await _run(
        "5. comment.create trace",
        "comment.create",
        {
            "target": "trace",
            "target_id": trace_id,
            "text": "smoke test comment from MCP",
        },
    )

    # 6. prompt_version.save  (omit commit — BE auto-assigns; 8 alphanum required when set)
    prompt_name = f"mcp_smoke_prompt_{uuid4().hex[:8]}"
    await _run(
        "6. prompt_version.save",
        "prompt_version.save",
        {
            "name": prompt_name,
            "template": "Hello {{name}}, smoke test {{ts}}",
            "metadata": {"source": "mcp_smoke"},
        },
    )

    # 7. test_suite.create
    suite_name = f"mcp_smoke_suite_{uuid4().hex[:8]}"
    suite_res = await _run(
        "7. test_suite.create",
        "test_suite.create",
        {"name": suite_name, "description": "smoke test from MCP"},
    )
    suite_id = suite_res.get("id") if suite_res else None

    # 8. test_suite_item.upsert
    await _run(
        "8. test_suite_item.upsert",
        "test_suite_item.upsert",
        {
            "test_suite_name": suite_name,
            "items": [
                {"input": {"q": "ping"}, "expected_output": {"a": "pong"}},
                {"input": {"q": "hello"}, "expected_output": {"a": "world"}},
            ],
        },
    )

    # 9. experiment.create
    exp_res = await _run(
        "9. experiment.create",
        "experiment.create",
        {
            "test_suite_name": suite_name,
            "name": f"mcp_smoke_exp_{uuid4().hex[:8]}",
        },
    )
    experiment_id = (exp_res.get("backend_body") or {}).get("id") if exp_res else None

    # Look up suite_id + a real suite_item_id via the read-side client.
    from opik_mcp.opik_client import make_opik_client
    from opik_mcp.config import get_settings

    client = make_opik_client(get_settings())
    test_suite_item_id = None
    resolved_suite_id = None
    try:
        suites = await client.list_test_suites(name=suite_name, page=1, size=5)
        for s in suites.get("content", []):
            if s.get("name") == suite_name:
                resolved_suite_id = s["id"]
                break
        if resolved_suite_id:
            items_resp = await client.list_test_suite_items(resolved_suite_id, page=1, size=5)
            for it in items_resp.get("content", []):
                test_suite_item_id = it.get("id")
                if test_suite_item_id:
                    break
    except Exception as e:
        print(f"  (suite-item lookup failed: {e})")
    finally:
        pass

    if not experiment_id:
        # exp.create succeeded but BE may not echo the id; look it up by name.
        try:
            client = make_opik_client(get_settings())
            exps = await client.list_experiments(name=None, page=1, size=50)
            for e in exps.get("content", []):
                if e.get("dataset_id") == resolved_suite_id:
                    experiment_id = e["id"]
                    break
        except Exception as e:
            print(f"  (experiment lookup failed: {e})")

    # 10. experiment_item.create
    if experiment_id and test_suite_item_id:
        await _run(
            "10. experiment_item.create",
            "experiment_item.create",
            {
                "experiment_items": [
                    {
                        "id": _uuid7(),
                        "experiment_id": experiment_id,
                        "test_suite_item_id": test_suite_item_id,
                        "trace_id": trace_id,
                    }
                ]
            },
        )
    else:
        print(f"\n--- 10. experiment_item.create SKIPPED (exp_id={experiment_id}, item_id={test_suite_item_id}) ---")

    print("\n=== LIVE BE SMOKE COMPLETE ===")
    print(f"trace_id:    {trace_id}")
    print(f"span_id:     {span_id}")
    print(f"suite_name:  {suite_name}")
    print(f"prompt:      {prompt_name}")


if __name__ == "__main__":
    asyncio.run(main())
