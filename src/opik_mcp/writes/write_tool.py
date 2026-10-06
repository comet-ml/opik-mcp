"""``write`` MCP tool — universal write entrypoint (spec §2.1).

Wraps ``dispatch.run_write`` so the MCP layer doesn't have to know about
the validation pipeline. Errors are converted to ``ToolError`` carrying
the JSON-encoded structured envelope from §4.5; the host shows the body
on the model's next turn, and the model self-corrects from the embedded
``example``, or from ``schema(operation)``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.base import OpikWrongURLError, opik_rest_base
from opik_mcp.client.opik import OpikClient
from opik_mcp.client.setup_hints import unreachable
from opik_mcp.config import Settings, get_settings
from opik_mcp.writes.dispatch import run_write as _dispatch
from opik_mcp.writes.errors import WriteError
from opik_mcp.writes.scopes import ALL_WRITE_SCOPES

logger = logging.getLogger("opik_mcp.writes.write_tool")


async def run_write(
    *,
    operation: str,
    data: Any,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    scopes: frozenset[str] = ALL_WRITE_SCOPES,
    client: OpikClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute a write. Returns the success envelope; raises ``ToolError`` on failure.

    The wrapped ``WriteError`` is serialized into the ``ToolError`` body so
    the MCP host surfaces the structured fields verbatim. We deliberately
    do not let the exception type propagate — every host that supports
    ``isError: true`` only sees the JSON envelope on the model's input.
    """
    try:
        return await _dispatch(
            operation=operation,
            data=data,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            scopes=scopes,
            client=client,
            settings=settings,
        )
    except WriteError as we:
        logger.info("write.failed operation=%s code=%s", operation, we.error)
        raise ToolError(we.to_json()) from we
    # Below, no answer came back from Opik, so there is no envelope to give: the
    # sentence says where the write went and what to change.
    except OpikWrongURLError as e:
        raise ToolError(str(e)) from e
    except httpx.ConnectTimeout as e:
        # Never connected, so never sent: the URL is the question.
        base_url = opik_rest_base(settings or get_settings())
        raise ToolError(unreachable(f"write {operation!r}", e, base_url)) from e
    except httpx.TimeoutException as e:
        raise ToolError(
            f"Opik did not answer in time for write({operation!r}, …). It may have been "
            "applied: read the target before retrying."
        ) from e
    except httpx.HTTPError as e:
        base_url = opik_rest_base(settings or get_settings())
        raise ToolError(unreachable(f"write {operation!r}", e, base_url)) from e


__all__ = ["run_write"]
