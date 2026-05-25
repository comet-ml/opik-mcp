#!/usr/bin/env python3
"""Fire a *realistic* backend-connection failure at Sentry.

Unlike ``sentry_smoke.py`` (which raises canned exceptions inside fake tool
bodies), this exercises the full ``OpikClient`` → ``httpx`` → socket path
against a closed local port. Sentry sees a stack trace that goes through
the real opik-mcp HTTP code, exactly the shape a production network outage
would produce — no ``[SMOKE]`` strings injected into the exception value,
no synthesised function names in the frames.

The bound workspace is ``opik-mcp-realistic-smoke`` so the resulting issue
sorts obviously alongside the canned smoke events.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Env must be set BEFORE Settings is constructed. ``127.0.0.1:1`` is the
# canonical "nothing listens" target: privileged port reserved by IANA, never
# bound by anything sane — produces a clean ECONNREFUSED without depending
# on external DNS or firewall behaviour.
os.environ.setdefault("OPIK_MCP_ANALYTICS_ENABLED", "false")
os.environ.setdefault("OPIK_MCP_SENTRY_ENABLED", "true")
os.environ.setdefault("COMET_WORKSPACE", "opik-mcp-realistic-smoke")
os.environ.setdefault("OPIK_API_KEY", "realistic-smoke-key")
os.environ.setdefault("OPIK_MCP_ANALYTICS_ENVIRONMENT", "dev")
os.environ.setdefault("COMET_URL_OVERRIDE", "http://127.0.0.1:1")
os.environ.setdefault("OPIK_URL", "http://127.0.0.1:1/opik/api")

import sentry_sdk

from opik_mcp import error_tracking
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.config import get_settings
from opik_mcp.opik_client import OpikClient


class _ClientInfo:
    def __init__(self) -> None:
        self.name = "opik-mcp-realistic-smoke-client"
        self.version = "0.0.1"


class _Params:
    def __init__(self) -> None:
        # MCP spec uses camelCase on the wire — mirror it here.
        self.clientInfo = _ClientInfo()


class _Session:
    def __init__(self) -> None:
        self.client_params = _Params()


class _Ctx:
    def __init__(self) -> None:
        self.session = _Session()


def _list_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    """Mirrors ``server._list_props`` so the Sentry event carries the same
    bucket tags a real ``list`` failure would.
    """
    return {
        "entity_type": kwargs.get("entity_type", ""),
        "had_name_filter": str(kwargs.get("name") is not None).lower(),
        "page": str(kwargs.get("page", 1)),
        "size": str(kwargs.get("size", 25)),
    }


async def main() -> int:
    settings = get_settings()
    if not error_tracking.setup_sentry(settings):
        print("setup_sentry returned False — opted out or pytest detected. Aborting.")
        return 1

    # Real client. The URL is well-formed, the endpoint path is correct, the
    # API key + workspace are sent in headers. Only the TCP connection to
    # 127.0.0.1:1 fails — exactly the shape of a production "backend is
    # down" / "DNS failure" / "firewall blocked" incident.
    client = OpikClient(
        base_url=settings.opik_url or f"{settings.comet_url_override}/opik/api",
        api_key=settings.opik_api_key or "no-key",
        workspace=settings.comet_workspace or "no-workspace",
    )

    @instrument_tool("list", props_fn=_list_props)
    async def real_list(*, entity_type: str, ctx: Any) -> dict[str, Any]:
        # Goes through OpikClient.list_projects → _get_json → _request →
        # httpx.AsyncClient.request → socket connect. Fails with a real
        # ``httpx.ConnectError`` whose value carries the OS-level error
        # message and an MRO that classifies to ``network_error``.
        return await client.list_projects(page=1, size=10)

    try:
        await real_list(entity_type="project", ctx=_Ctx())
    except Exception as exc:
        print(f"  list / projects -> raised {type(exc).__name__}: {exc}")

    print("\nFlushing Sentry queue ...")
    sentry_sdk.flush(timeout=5.0)
    print("Done. The new event should:")
    print("  * group as ConnectError / list")
    print("  * carry tags: tool_name=list, error_kind=network_error,")
    print("    entity_type=project, workspace=opik-mcp-realistic-smoke")
    print("  * carry a stack going through opik_mcp.opik_client._request")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
