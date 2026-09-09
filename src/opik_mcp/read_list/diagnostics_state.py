"""Why is a project's Diagnostics issue list empty?

"No agent_insights_issues found." covers several situations an agent cannot
tell apart: Diagnostics was never turned on for the project, it was turned
off, it is on but has not scanned (or not recently), or it is on and clean.
Only the last one is an all-clear. The project's Diagnostics *job* record
tells them apart, so an empty page spends one extra read on it and says
which case this is, what to call about it, and where the page lives.

The hint decorates an answer the agent already has: a failed lookup yields
``None`` and the plain empty message stands.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.read_list.deployment import UNAVAILABLE_SENTENCE, diagnostics_available
from opik_mcp.read_list.ui_links import project_page_url
from opik_mcp.read_list.window import parse_instant, to_minute

logger = logging.getLogger("opik_mcp.read_list.diagnostics_state")

# The nightly job runs once a day; a scan older than its own period is stale.
STALE_AFTER = timedelta(hours=24)

# Operation names are fixed here so the hint can name them before they ship
# (they land in the write registry as separate slices).
ENABLE_OP = "agent_insights_job.enable"
TRIGGER_OP = "agent_insights_job.trigger"


async def diagnostics_state_hint(
    client: OpikListClient,
    settings: Settings,
    project_id: str,
    *,
    issue_status: str,
    now: datetime | None = None,
) -> str | None:
    """One or two sentences explaining an empty issue list, or ``None`` when
    the job could not be read."""
    if not await diagnostics_available(client):
        # Nothing will ever scan here, so the project's job state is beside
        # the point and proposing enable would be a lie.
        return UNAVAILABLE_SENTENCE
    try:
        job: dict[str, Any] | None = await client.get_agent_insights_job(project_id)
    except OpikNotFoundError:
        job = None
    except (OpikAuthError, OpikValidationError, OpikServerError, httpx.HTTPError):
        logger.debug("Diagnostics job lookup for the empty-list hint failed", exc_info=True)
        return None

    now = now or datetime.now(UTC)
    scope = f"{{project_id={project_id!r}}}"
    enable = f"Enable it with write('{ENABLE_OP}', {scope}); it then scans daily."
    trigger_first = f"write('{TRIGGER_OP}', {scope}) runs the first scan now."
    trigger = f"Trigger a scan with write('{TRIGGER_OP}', {scope})."

    if job is None:
        sentences = [f"Diagnostics is not enabled for this project. {enable} {trigger_first}"]
    elif job.get("status") == "disabled":
        turned_off = _day(job.get("last_updated_at"))
        when = f" on {turned_off}" if turned_off else ""
        sentences = [f"Diagnostics was turned off for this project{when}. {enable} {trigger_first}"]
    else:
        last_scan = parse_instant(str(job.get("last_scan_at") or ""))
        if last_scan is None:
            sentences = [f"Diagnostics is enabled but has not scanned yet. {trigger}"]
        elif now - last_scan > STALE_AFTER:
            sentences = [
                f"Diagnostics is enabled; last scan {to_minute(last_scan)}, older than a day. "
                f"{trigger}"
            ]
        else:
            sentences = [f"No {issue_status} issues. Last scan: {to_minute(last_scan)}."]

    if job is not None and job.get("last_failure_reason"):
        detail = job.get("last_failure_detail")
        suffix = f" ({detail})" if detail else ""
        sentences.append(f"The last run failed: {job['last_failure_reason']}{suffix}.")

    page = project_page_url(settings, project_id, "diagnostics")
    if page is not None:
        sentences.append(f"Diagnostics page: {page}")
    return " ".join(sentences)


def _day(value: Any) -> str | None:
    dt = parse_instant(str(value)) if value else None
    return dt.astimezone(UTC).strftime("%Y-%m-%d") if dt is not None else None


__all__ = ["ENABLE_OP", "STALE_AFTER", "TRIGGER_OP", "diagnostics_state_hint"]
