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
from typing import TYPE_CHECKING, Any

import httpx

from opik_mcp.config import Settings
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikListClient,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.read_list.diagnostics.availability import (
    UNAVAILABLE_SENTENCE,
    diagnostics_available,
)
from opik_mcp.read_list.project_scope import resolve_project_id
from opik_mcp.read_list.ui_links import project_page_url
from opik_mcp.read_list.window import parse_instant, to_minute

if TYPE_CHECKING:  # pragma: no cover - import cycle: registry imports this module
    from opik_mcp.read_list.registry import PageContext

logger = logging.getLogger("opik_mcp.read_list.diagnostics.state")

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


async def _read_job(client: OpikListClient, project_id: str, *, what: str) -> dict[str, Any] | None:
    """The project's Diagnostics job, ``None`` when it has none, and
    :class:`_Unreadable` when the lookup itself failed.

    The two callers need the same three-way answer and the same rule: a failed
    side lookup decorates nothing rather than turning an answered list into an
    error.
    """
    try:
        return await client.get_agent_insights_job(project_id)
    except OpikNotFoundError:
        return None
    except (OpikAuthError, OpikValidationError, OpikServerError, httpx.HTTPError):
        logger.debug("Diagnostics job lookup for the %s failed", what, exc_info=True)
        raise _Unreadable from None


class _Unreadable(Exception):
    """The job lookup failed, as opposed to the project having no job."""


def _instant_arg(when: datetime) -> str:
    """An instant in the form ``since``/``until`` take."""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _trigger_reaches(window_end: datetime, now: datetime) -> bool:
    """Can a scan started now cover the end of the requested window?

    A trigger rescans the last 24 hours *from now*, so it can only help while
    the window is still open. Offering one for a window that closed last week
    reads as the fix and changes nothing.
    """
    return now - window_end <= COVERAGE_GRACE


async def diagnostics_state_hint(
    client: OpikListClient,
    settings: Settings,
    project_id: str,
    *,
    issue_status: str | None = None,
    windowed: bool = False,
    window_end: datetime | None = None,
    now: datetime | None = None,
) -> str | None:
    """One or two sentences explaining an empty issue list, or ``None`` when
    the job could not be read.

    ``window_end`` is the end of the window the caller asked about (``until``,
    or now when unbounded). Staleness is judged against it, not against now: a
    scan from last Tuesday answers a question about last Monday perfectly well.
    """
    if not await diagnostics_available(client):
        # Nothing will ever scan here, so the project's job state is beside
        # the point and proposing enable would be a lie.
        return UNAVAILABLE_SENTENCE
    try:
        job = await _read_job(client, project_id, what="empty-list hint")
    except _Unreadable:
        return None

    now = now or datetime.now(UTC)
    end = window_end or now
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
        elif end - last_scan > STALE_AFTER:
            stale = f"Diagnostics is enabled; last scan {to_minute(last_scan)}, older than a day."
            if _trigger_reaches(end, now):
                sentences = [f"{stale} {trigger}"]
            else:
                # The window closed before now, so a 24-hour rescan lands
                # outside it entirely.
                sentences = [
                    f"{stale} The requested window ends {to_minute(end)}, and a trigger only "
                    "rescans the last 24 hours, so it cannot cover it: read that stretch from "
                    f"raw traces with {_traces_call(project_id, last_scan, end)}."
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


def _since_arg(delta: timedelta) -> str:
    """``"20h"`` / ``"4d"`` — the shape the ``since`` argument takes, so the
    number in the sentence can be pasted into the call it suggests."""
    hours = delta.total_seconds() / 3600
    if hours < 48:
        return f"{max(1, round(hours))}h"
    return f"{round(hours / 24)}d"


def _traces_call(project_id: str, start: datetime, end: datetime) -> str:
    """The ``list('trace', …)`` call that reads an uncovered stretch, bounded
    at both ends because a relative ``since`` would name the wrong days for a
    window that has already closed."""
    return (
        f"list('trace', project_id='{project_id}', "
        f"since='{_instant_arg(start)}', until='{_instant_arg(end)}')"
    )


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
        job = await _read_job(client, project_id, what="coverage note")
    except _Unreadable:
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
        bounded = _traces_call(project_id, last_scan, end)
        if not _trigger_reaches(end, now):
            # The window has already closed, so "the last N hours" would name a
            # different stretch than the hole, and a trigger cannot reach it.
            sentences.append(
                f"The window runs to {to_minute(end)}, past that scan, and a trigger only "
                f"rescans the last 24 hours: read the uncovered stretch with {bounded}."
            )
        elif gap <= TRIGGER_COVERS:
            scope = json.dumps({"project_id": project_id})
            sentences.append(
                f"The last {_since_arg(gap)} are not in it: write('{TRIGGER_OP}', {scope}) "
                f"rescans the last 24 hours, or read the gap from raw traces with {bounded}."
            )
        else:
            sentences.append(
                f"The last {_since_arg(gap)} are not in it, and a trigger rescans the last "
                f"24 hours, so it cannot close this gap: read it from raw traces with {bounded}."
            )
    return " ".join(sentences)


async def issue_page_note(
    client: OpikListClient,
    settings: Settings,
    ctx: PageContext,
) -> str | None:
    """The ``page_note_fn`` for ``agent_insights_issue``: what this page of
    issues does not say for itself.

    Empty or full, the page needs the project's job to explain itself, so the
    two cases share one entry point and one rule: any failure yields ``None``
    and the page stands as it was.
    """
    project_id = ctx.project_id
    if project_id is None and ctx.project_name is not None:
        # The list_fn resolved this name already and got its own copy of the
        # kwargs, so the id does not come back — a cache hit in practice.
        try:
            project_id = await resolve_project_id(client, ctx.project_name)
        except Exception:
            logger.debug("project re-resolve for a Diagnostics note failed", exc_info=True)
            return None
    if project_id is None:
        return None
    try:
        if ctx.empty:
            return await diagnostics_state_hint(
                client,
                settings,
                project_id,
                issue_status=ctx.status,
                windowed=ctx.windowed,
                window_end=ctx.window_end,
            )
        return await diagnostics_coverage_note(
            client, settings, project_id, window_end=ctx.window_end
        )
    except Exception:
        logger.debug("Diagnostics note for the issue list failed", exc_info=True)
        return None


__all__ = [
    "COVERAGE_GRACE",
    "ENABLE_OP",
    "STALE_AFTER",
    "TRIGGER_OP",
    "diagnostics_coverage_note",
    "diagnostics_state_hint",
    "issue_page_note",
]
