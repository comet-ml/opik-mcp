"""Feedback about opik-mcp itself, sent to the Opik team as one analytics event."""

from __future__ import annotations

from typing import Final

from opik_mcp.config import Settings

# A host that defers tools/list sees only the instructions until it loads a tool,
# and a failed call is the moment this tool is for.
INSTRUCTIONS_PARAGRAPH: Final = (
    "If an opik-mcp tool fails or cannot do the job, send_feedback reports it to the Opik team."
)


def is_feedback_enabled(settings: Settings) -> bool:
    """The report has only the analytics endpoint to go to, so no endpoint means no tool."""
    return (
        settings.opik_mcp_feedback_enabled
        and settings.opik_mcp_analytics_enabled
        and bool(settings.opik_mcp_analytics_url.strip())
    )
