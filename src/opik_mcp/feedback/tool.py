from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations
from pydantic import Field

from opik_mcp.analytics.mcp_client_info import call_context_props
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.config import get_settings
from opik_mcp.feedback.report import FeedbackReport, send_feedback_report
from opik_mcp.read_list.registry import ENTITY_REGISTRY
from opik_mcp.writes.registry import WRITE_OPERATIONS

TITLE = "Send feedback to the Opik team"
# It adds a report and changes nothing that exists, and the report leaves the
# user's workspace. Defined here, not in server/tools/hints.py: that package
# imports this module, so importing hints from here would be a cycle.
ANNOTATIONS = ToolAnnotations(
    title=TITLE,
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# Each field is a closed set, so the team can count reports by it.
Category = Literal["error", "wrong_result", "confusing", "missing_capability", "slow", "other"]
Impact = Literal["blocked", "workaround", "minor"]
# The tools a report can concern. tests/feedback pins this to tools/list.
ToolName = Literal["read", "list", "write", "schema", "read_skill", "none"]

DESCRIPTION = (
    "Send feedback about this MCP server (opik-mcp) to the Opik team at Comet. "
    "It is not for the user's Opik data. Call it when an opik-mcp tool failed, gave "
    "a wrong or confusing result, or cannot do what the user needs, or when the user "
    "asks to send feedback. Do not call it when the task went well, to report "
    "progress, or as a test. Send at most one report per task. Describe the problem "
    "in general terms: no trace content, names, IDs or secrets from the user's data. "
    "The report carries the workspace name and the user's Comet login."
)


def _known_targets() -> frozenset[str]:
    return frozenset((*ENTITY_REGISTRY, *WRITE_OPERATIONS))


def _target_label(target: str) -> str:
    """A known name, ``other`` or ``none``: the free argument never reaches the report."""
    stripped = target.strip()
    if not stripped:
        return "none"
    return stripped if stripped in _known_targets() else "other"


def _send_feedback_props(_result: object, kwargs: dict[str, object]) -> dict[str, str]:
    """The closed fields only. The description goes in the feedback event and nowhere else."""
    return {
        "category": str(kwargs.get("category", "")),
        "impact": str(kwargs.get("impact", "")),
        "feedback_tool": str(kwargs.get("tool", "none")),
        "requested_by_user": str(bool(kwargs.get("requested_by_user", False))).lower(),
    }


# No Sentry: a stack trace carries the frame locals, and the description is one.
@instrument_tool("send_feedback", props_fn=_send_feedback_props, reports_to_sentry=False)
async def send_feedback(
    category: Annotated[Category, Field(description="What went wrong.")],
    impact: Annotated[
        Impact,
        Field(
            description=(
                "blocked: the task could not be done. workaround: it was done another "
                "way. minor: it cost time only."
            )
        ),
    ],
    description: Annotated[
        str,
        Field(
            description=(
                "2-4 sentences: what you tried, what came back, what you expected. No user data."
            ),
            max_length=2000,
        ),
    ],
    *,
    tool: Annotated[ToolName, Field(description="The opik-mcp tool it concerns.")] = "none",
    target: Annotated[
        str,
        Field(description="The entity type or write operation, e.g. trace.", max_length=64),
    ] = "",
    requested_by_user: Annotated[
        bool, Field(description="True when the user asked to send this.")
    ] = False,
    ctx: Context[ServerSession, None, object] | None = None,
) -> str:
    session = ctx.session if ctx is not None else None
    if ctx is not None:
        await ctx.info(f"send_feedback.called category={category} impact={impact} tool={tool}")
    sent = send_feedback_report(
        FeedbackReport(
            category=category,
            impact=impact,
            tool=tool,
            target=_target_label(target),
            description=description,
            is_requested_by_user=requested_by_user,
        ),
        host_properties=call_context_props(session),
        settings=get_settings(),
    )
    return sent.answer()


def register(mcp: FastMCP[object]) -> None:
    mcp.tool(
        description=DESCRIPTION,
        title=TITLE,
        annotations=ANNOTATIONS,
        structured_output=False,
    )(send_feedback)
