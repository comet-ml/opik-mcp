"""What does a project's Diagnostics issue list actually say?

"No agent_insights_issues found." covers several situations an agent cannot
tell apart: Diagnostics was never turned on for the project, it was turned
off, it is on but has not scanned (or not recently), or it is on and clean.
Only the last one is an all-clear. The project's Diagnostics *job* record
tells them apart, so an empty page spends one extra read on it and says
which case this is, what to call about it, and where the page lives.

A non-empty list has a quieter version of the same problem. The issues are
whatever the last scan grouped, and nothing in the rows says when that was,
so a report that stops at yesterday reads as the state of the world now. Ask
for a week and the last day is missing from the answer without a word about
it. So a non-empty page carries the report's as-of date, and when the window
runs past it, the size of the uncovered tail and where to get it.

Both decorate an answer the agent already has: a failed lookup yields
``None`` and the plain reply stands.
"""

from __future__ import annotations

import json
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

#: A trigger rescans this much, so it can only close a gap no wider.
TRIGGER_COVERS = timedelta(hours=24)

#: Below this, the report is current enough that naming a gap would be noise
#: rather than news. Anything above it is a real hole in the answer.
COVERAGE_GRACE = timedelta(hours=1)

# The write operations this hint points at. Named as constants rather than
# imported from ``writes.registry``: ``writes`` already imports from
# ``read_list`` (this module among them), so importing back would close a
# cycle. ``tests/test_writes/test_registry.py`` asserts both names exist.
ENABLE_OP = "agent_insights_job.enable"
TRIGGER_OP = "agent_insights_job.trigger"


#: The statuses ``list`` accepts, so a caller's string is never interpolated
#: into the reply unchecked.
_KNOWN_STATUSES = frozenset({"open", "resolved", "closed"})


async def diagnostics_state_hint(
    client: OpikListClient,
    settings: Settings,
    project_id: str,
    *,
    issue_status: str | None = None,
    windowed: bool = False,
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
    # The snippet is for copying into write(), whose data is JSON.
    scope = json.dumps({"project_id": project_id})
    enable = f"Enable it with write('{ENABLE_OP}', {scope}); it then scans daily."
    trigger_first = f"write('{TRIGGER_OP}', {scope}) runs the first scan now."
    trigger = f"Trigger a scan with write('{TRIGGER_OP}', {scope})."

    status = issue_status if issue_status in _KNOWN_STATUSES else "open"
    scoped = " in the requested window" if windowed else ""
    if job is None:
        sentences = [f"Diagnostics is not enabled for this project. {enable} {trigger_first}"]
    elif str(job.get("status")).lower() == "disabled":
        # ``last_updated_at`` is the row's mtime — a trigger or a scan moves it
        # — so it cannot be reported as the date somebody switched this off.
        sentences = [f"Diagnostics is turned off for this project. {enable} {trigger_first}"]
    else:
        last_scan = parse_instant(str(job.get("last_scan_at") or ""))
        if last_scan is None:
            # The job record has no "run in flight" field, so a scan started a
            # minute ago is indistinguishable from none at all. Cover both, or
            # an agent that just triggered one is told to trigger another.
            sentences = [
                "Diagnostics is enabled but has no completed scan yet. A run "
                f"started in the last few minutes may still be running; otherwise {trigger}"
            ]
        elif now - last_scan > STALE_AFTER:
            sentences = [
                f"Diagnostics is enabled; last scan {to_minute(last_scan)}, older than a day. "
                f"{trigger}"
            ]
        else:
            sentences = [f"No {status} issues{scoped}. Last scan: {to_minute(last_scan)}."]

    if job is not None and job.get("last_failure_reason"):
        detail = job.get("last_failure_detail")
        suffix = f" ({detail})" if detail else ""
        sentences.append(f"The last run failed: {job['last_failure_reason']}{suffix}.")

    page = project_page_url(settings, project_id, "diagnostics")
    if page is not None:
        sentences.append(f"Diagnostics page: {page}")
    return " ".join(sentences)


def _span(delta: timedelta) -> str:
    """``"20h"`` / ``"4d"`` — the shape the ``since`` argument takes, so the
    number in the sentence can be pasted into the call it suggests."""
    hours = delta.total_seconds() / 3600
    if hours < 48:
        return f"{max(1, round(hours))}h"
    return f"{round(hours / 24)}d"


async def diagnostics_coverage_note(
    client: OpikListClient,
    settings: Settings,
    project_id: str,
    *,
    window_end: datetime | None = None,
    now: datetime | None = None,
) -> str | None:
    """What a *non-empty* issue list covers, and what it misses.

    ``window_end`` is the end of the window the caller asked about (``until``,
    or now when unbounded). Returns ``None`` — leaving the list exactly as it
    was — when the job cannot be read or has no scan to date from.

    The deployment gate is deliberately not consulted here: issues exist, so
    Diagnostics demonstrably works on this deployment and asking would spend a
    request to learn what the rows already prove.
    """
    try:
        job: dict[str, Any] | None = await client.get_agent_insights_job(project_id)
    except OpikNotFoundError:
        return None
    except (OpikAuthError, OpikValidationError, OpikServerError, httpx.HTTPError):
        logger.debug("Diagnostics job lookup for the coverage note failed", exc_info=True)
        return None
    if job is None:
        return None
    last_scan = parse_instant(str(job.get("last_scan_at") or ""))
    if last_scan is None:
        return None

    now = now or datetime.now(UTC)
    end = window_end or now
    sentences = [f"Report covers data through {to_minute(last_scan)}."]
    gap = end - last_scan
    if gap > COVERAGE_GRACE:
        scope = json.dumps({"project_id": project_id})
        span = _span(gap)
        traces = f"list('trace', project_id='{project_id}', since='{span}')"
        if gap <= TRIGGER_COVERS:
            sentences.append(
                f"The last {span} are not in it: write('{TRIGGER_OP}', {scope}) rescans "
                f"the last 24 hours, or read the gap from raw traces with {traces}."
            )
        else:
            sentences.append(
                f"The last {span} are not in it, and a trigger rescans the last 24 hours, "
                f"so it cannot close this gap: read it from raw traces with {traces}."
            )
    return " ".join(sentences)


__all__ = [
    "COVERAGE_GRACE",
    "ENABLE_OP",
    "STALE_AFTER",
    "TRIGGER_OP",
    "diagnostics_coverage_note",
    "diagnostics_state_hint",
]
