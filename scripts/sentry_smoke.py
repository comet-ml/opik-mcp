#!/usr/bin/env python3
"""Fire a handful of representative errors at the opik-mcp Sentry project.

Usage: ``uv run scripts/sentry_smoke.py``

Each call goes through the same ``@instrument_tool`` wrapper the real server
uses, so the resulting Sentry events carry the full tag set: tool_name,
error_kind, props_fn output, mcp_host/mcp_client_version (from a stub ctx),
plus all the global tags bound at startup.

Analytics is forced off so BI stays clean; ``COMET_WORKSPACE`` is set to
``opik-mcp-smoke`` so these events sort obviously in the dashboard.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Env must be set BEFORE Settings is constructed.
os.environ.setdefault("OPIK_MCP_ANALYTICS_ENABLED", "false")
os.environ.setdefault("OPIK_MCP_SENTRY_ENABLED", "true")
os.environ.setdefault("COMET_WORKSPACE", "opik-mcp-smoke")
os.environ.setdefault("OPIK_MCP_ANALYTICS_ENVIRONMENT", "dev")

import httpx
import sentry_sdk

from opik_mcp import error_tracking
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.comet_client import CometProtocolError
from opik_mcp.config import get_settings
from opik_mcp.opik_client import OpikServerError


class _ClientInfo:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version


class _Params:
    def __init__(self) -> None:
        # Mirrors MCP spec field naming (camelCase on the wire).
        self.clientInfo = _ClientInfo("opik-mcp-smoke-client", "0.0.1")


class _Session:
    def __init__(self) -> None:
        self.client_params = _Params()


class _Ctx:
    def __init__(self) -> None:
        self.session = _Session()


def _read_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {"entity_type": kwargs.get("entity_type", ""), "id_kind": "uuid"}


def _write_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    return {
        "operation": kwargs.get("operation", ""),
        "is_batch": "false",
        "dry_run": "false",
    }


async def main() -> int:
    settings = get_settings()
    if not error_tracking.setup_sentry(settings):
        print("setup_sentry returned False — opted out or pytest detected. Aborting.")
        return 1

    @instrument_tool("read", props_fn=_read_props)
    async def fake_read_500(*, entity_type: str, id: str, ctx: Any) -> None:
        raise OpikServerError(f"[SMOKE] Opik server error (500) for {entity_type} {id}.")

    @instrument_tool("write", props_fn=_write_props)
    async def fake_write_network(*, operation: str, ctx: Any) -> None:
        raise httpx.ConnectError("[SMOKE] Connection refused to opik backend.")

    @instrument_tool("list")
    async def fake_list_unknown(*, ctx: Any) -> None:
        raise RuntimeError("[SMOKE] Unexpected list-tool bug.")

    @instrument_tool("ask_ollie")
    async def fake_ollie_protocol(*, ctx: Any) -> None:
        raise CometProtocolError("[SMOKE] Pod returned non-JSON.")

    cases: list[tuple[str, Any, dict[str, Any]]] = [
        (
            "read / opik_http_5xx",
            fake_read_500,
            {
                "entity_type": "trace",
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "ctx": _Ctx(),
            },
        ),
        (
            "write / network_error",
            fake_write_network,
            {"operation": "traces.create_many", "ctx": _Ctx()},
        ),
        (
            "list / unknown",
            fake_list_unknown,
            {"ctx": _Ctx()},
        ),
        (
            "ask_ollie / comet_protocol_error",
            fake_ollie_protocol,
            {"ctx": _Ctx()},
        ),
    ]
    for label, fn, kwargs in cases:
        try:
            await fn(**kwargs)
        except Exception as exc:
            print(f"  {label:<40} -> raised {type(exc).__name__}")

    print("\nFlushing Sentry queue ...")
    sentry_sdk.flush(timeout=5.0)
    print(f"Done. {len(cases)} events sent to the opik-mcp Sentry project.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
