from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from opik_mcp.analytics.events import bucket_count
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.server.tools.description import described
from opik_mcp.server.tools.hints import WRITES
from opik_mcp.writes import WRITE_TOOL_DESCRIPTION, run_write
from opik_mcp.writes.registry import WRITE_OPERATIONS


def _write_props(_result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
    """Analytics labels for the universal write tool.

    ``operation`` is the high-cardinality dimension that dashboards key off
    of; pair it with the boolean-ish shape signals so the (tool, operation)
    label set ADR §4.4 specifies stays useful as Phase 2 grows it.
    """
    data = kwargs.get("data")
    is_batch = isinstance(data, list)
    batch_size = len(data) if isinstance(data, list) else 1
    return {
        "operation": str(kwargs.get("operation", "")),
        "is_batch": str(is_batch).lower(),
        "batch_size_bucket": bucket_count(batch_size),
        "dry_run": str(bool(kwargs.get("dry_run", False))).lower(),
        "had_idempotency_key": str(kwargs.get("idempotency_key") is not None).lower(),
    }


# --- write (universal write tool, supersedes score/comment) ------------- #
#
# Operation is advertised as a JSON-Schema enum but typed as ``str`` so the
# FastMCP/Pydantic boundary does NOT reject unknown values — we want those
# to flow into the dispatcher's Stage 1 which raises ``UnknownOperationError``
# with the full ``valid_operations`` list. The conformance test verifies
# the advertised enum matches ``WRITE_OPERATIONS`` so drift is caught.

WRITE_OPERATION_ENUM: list[str] = list(WRITE_OPERATIONS)


@instrument_tool("write", props_fn=_write_props)
async def write(
    operation: Annotated[
        str,
        Field(
            description=(
                "The entity/verb pair to invoke. See tool description for the list. "
                "Call schema(operation) for the JSON Schema, example, and required scope."
            ),
            json_schema_extra={"enum": WRITE_OPERATION_ENUM},
        ),
    ],
    data: Annotated[
        dict[str, Any] | list[Any],
        Field(
            description=(
                "Payload for the operation. Object for a single write, or array "
                "(max 1000 elements) for batch. Always-envelope operations "
                "(dataset_item.upsert, experiment_item.create) take their list "
                "inside the envelope, not at the top level."
            ),
        ),
    ],
    *,
    idempotency_key: Annotated[
        str | None,
        Field(
            description=(
                "Optional client-supplied UUID. Re-running with the same key is a "
                "no-op on the backend. Takes precedence over data.id when both are set."
            ),
            max_length=64,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "Validate against the operation's schema and OAuth scope without "
                "calling the backend. Returns {dry_run: true, would_call: ...}."
            ),
        ),
    ] = False,
    ctx: Context[ServerSession, None] | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        is_batch = isinstance(data, list)
        await ctx.info(f"write.called operation={operation} batch={is_batch} dry_run={dry_run}")
    return await run_write(
        operation=operation,
        data=data,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )


def register(mcp: FastMCP[object], sentence: str | None = None) -> None:
    mcp.tool(
        description=described(sentence, WRITE_TOOL_DESCRIPTION),
        title="Write to Opik",
        annotations=WRITES,
        structured_output=False,
    )(write)
