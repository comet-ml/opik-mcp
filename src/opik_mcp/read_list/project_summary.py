"""The four figures a project read leads with, and how not to mislead with them.

"How is my project doing this week" is the first thing a developer asks, and
the UI answers it with four cards on the Logs page: trace count, error rate,
average duration and total cost, each against the period before. One backend
call (``POST /projects/{id}/kpi-cards``) returns exactly that, so the project
read reproduces the cards rather than inventing its own arithmetic over raw
traces.

Three things about the backend's answer have to be handled here, all three
confirmed against www.comet.com rather than read off the Java:

1. **Order is the backend's, not the UI's.** It answers ``count``,
   ``avg_duration``, ``total_cost``, ``errors``. Reading positionally would
   label cost as duration — silently, and plausibly.
2. **Zero and "no data" are mixed.** An empty period returns a zero count, a
   zero cost, a zero error rate, and a *null* average duration. Passing the
   zero rate through reads as "0% errors — healthy", which is advice someone
   may act on. A rate and an average over no samples are undefined; a count
   and a sum are honestly zero.
3. **The cards are SDK-only.** Both Logs tabs hardcode ``source = "sdk"``, and
   so does ``list('trace')``. A summary that quietly counted experiment and
   playground traffic would never reconcile with the number on screen.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikReadClient,
    OpikServerError,
    OpikValidationError,
)

logger = logging.getLogger("opik_mcp.read_list.project_summary")

WINDOW_DAYS: Final = 7
"""Default window: the week the question is about.

Note this is *not* the UI's default — the Logs cards and the projects list both
open on 30 days. The read reports the window it used so the two can be
reconciled, and ``since``/``until`` select any other.
"""

# The Logs page's own filter, verbatim. Not configurable here: a summary that
# does not match the screen is worse than no summary.
SDK_SOURCE_FILTER: Final = json.dumps(
    [{"field": "source", "operator": "=", "value": "sdk"}], separators=(",", ":")
)

_UNDEFINED_OVER_NO_SAMPLES: Final = ("errors", "avg_duration")
"""A rate and a mean need samples. Over an empty period they are undefined,
not zero — unlike ``count`` and ``total_cost``, which are legitimately zero."""

_FIGURES: Final = ("count", "errors", "avg_duration", "total_cost")

_NO_TRAFFIC_NOTE: Final = (
    "no SDK traces in this window, so the error rate and average duration are "
    "undefined rather than zero"
)


def window(*, days: int = WINDOW_DAYS, now: datetime | None = None) -> tuple[str, str]:
    """``(since, until)`` as ISO-8601 instants, second precision.

    The backend derives the comparison period from the window's own length —
    ``[start - (end - start), start)`` — so the caller never states it.
    """
    end = (now or datetime.now(UTC)).replace(microsecond=0)
    start = end - timedelta(days=days)
    return _iso(start), _iso(end)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _figure(stats: dict[str, dict[str, Any]], name: str) -> dict[str, float | None]:
    row = stats.get(name) or {}
    return {"current": row.get("current_value"), "previous": row.get("previous_value")}


def shape_stats(stats: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """The backend's ``stats`` list → ``({figure: {current, previous}}, no_traffic)``.

    Keyed by ``type``, never by position. Each period is judged on its own
    count, so a quiet week followed by a busy one reports an undefined rate for
    the first and a real one for the second.
    """
    by_type = {
        row["type"]: row
        for row in stats
        if isinstance(row, dict) and isinstance(row.get("type"), str)
    }
    figures = {name: _figure(by_type, name) for name in _FIGURES if name in by_type}

    counts = figures.get("count", {})
    for period in ("current", "previous"):
        if counts.get(period):
            continue
        for name in _UNDEFINED_OVER_NO_SAMPLES:
            if name in figures:
                figures[name][period] = None

    return figures, not counts.get("current")


async def trace_summary(
    client: OpikReadClient,
    project_id: str,
    *,
    days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """The summary block for a project read: the window, the source, the figures.

    On failure returns ``{window, source, error}`` instead of the figures. It
    must never return zeros — a metric that could not be loaded looking like a
    measured zero is the one outcome worth guarding against, since "no traces
    this week" is something a person may act on. Same rule as a thread's
    messages, where an empty list would contradict the metadata.
    """
    since, until = window(days=days)
    block: dict[str, Any] = {
        "window": {"since": since, "until": until, "days": days},
        "source": "sdk",
    }
    try:
        body = await client.get_project_kpi_cards(
            project_id,
            entity_type="traces",
            interval_start=since,
            interval_end=until,
            filters=SDK_SOURCE_FILTER,
        )
    except (
        OpikAuthError,
        OpikNotFoundError,
        OpikValidationError,
        OpikServerError,
    ) as exc:
        logger.debug("project %s KPI cards failed: %s", project_id, exc)
        block["error"] = (
            f"Could not load this project's metrics: {exc} "
            f"Retry, or count directly with list('trace', project_id='{project_id}', "
            f"since='{days}d')."
        )
        return block

    raw = body.get("stats")
    figures, no_traffic = shape_stats(raw if isinstance(raw, list) else [])
    block["traces"] = figures
    if no_traffic:
        block["note"] = _NO_TRAFFIC_NOTE
    return block


__all__ = [
    "SDK_SOURCE_FILTER",
    "WINDOW_DAYS",
    "shape_stats",
    "trace_summary",
    "window",
]
