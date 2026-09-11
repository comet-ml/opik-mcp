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
from datetime import datetime, timedelta
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.decorations import BLOCK_ERRORS, describe
from opik_mcp.read_list.oql import SDK_SOURCE_CLAUSE
from opik_mcp.read_list.window import closed_window, format_instant

WINDOW_DAYS: Final = 7
"""Default window: the week the question is about.

Note this is *not* the UI's default — the Logs cards and the projects list both
open on 30 days. The read reports the window it used so the two can be
reconciled, and ``since``/``until`` select any other.
"""

# The Logs page's own filter, verbatim. Not configurable here: a summary that
# does not match the screen is worse than no summary.
#
# The shared clause, encoded — `kpi-cards` declares `filters` as a String where
# the list endpoints take an array. The encoding differs between the two
# endpoints; the fact must not, so this serialises the one clause rather than
# spelling out a second copy of it.
SDK_SOURCE_FILTER: Final = json.dumps([SDK_SOURCE_CLAUSE], separators=(",", ":"))

_UNDEFINED_OVER_NO_SAMPLES: Final = ("errors", "avg_duration")
"""A rate and a mean need samples. Over an empty period they are undefined,
not zero — unlike ``count`` and ``total_cost``, which are legitimately zero."""

_FIGURES: Final = ("count", "errors", "avg_duration", "total_cost")

_NO_TRAFFIC_NOTE: Final = (
    "no SDK traces in this window, so the error rate and average duration are "
    "undefined rather than zero"
)


def window(
    *,
    since: str | None = None,
    until: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The window block: ``{since, until, days?, compared_to}``.

    Both bounds arrive already resolved to instants by the read tool, or not at
    all — the default is the last :data:`WINDOW_DAYS` days ending now, and a
    lone ``until`` still gets that span.

    ``compared_to`` is stated rather than left implicit. The backend's previous
    period is ``[start - (end - start), start)``, which an agent can derive but
    routinely derives wrong; "compared to last month" when it was last week is
    exactly the mistake this spends thirty tokens to prevent.

    ``days`` appears only when the span is a whole number of them. A 36-hour
    window has no day count, and rounding one would be a small lie in a field
    an agent will quote.
    """
    start, end = closed_window(since, until, days=WINDOW_DAYS, now=now)
    span = end - start

    block: dict[str, Any] = {"since": format_instant(start), "until": format_instant(end)}
    if span and span % timedelta(days=1) == timedelta(0):
        block["days"] = span.days
    block["compared_to"] = {
        "since": format_instant(start - span),
        "until": format_instant(start),
    }
    return block


def _readable(value: Any) -> Any:
    """A figure at the precision it has, not the precision a float prints at.

    The backend answers an error rate over 167 traces as
    ``18.562874251497007``. Seventeen digits is thirteen characters of noise
    claiming a precision the measurement does not have, on a number an agent
    quotes to a person. Four decimals is plenty — except near zero, where
    rounding to four would turn a real cost of ``1.35e-05`` into ``0`` and say
    something false. Below that, three significant digits.
    """
    if not isinstance(value, float):
        return value
    if value and abs(value) < 1e-4:
        return float(f"{value:.3g}")
    return round(value, 4)


def _figure(stats: dict[str, dict[str, Any]], name: str) -> dict[str, float | None]:
    row = stats.get(name) or {}
    return {
        "current": _readable(row.get("current_value")),
        "previous": _readable(row.get("previous_value")),
    }


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
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """The summary block for a project read: the window, the source, the figures.

    On failure returns ``{window, source, error}`` instead of the figures. It
    must never return zeros — a metric that could not be loaded looking like a
    measured zero is the one outcome worth guarding against, since "no traces
    this week" is something a person may act on. Same rule as a thread's
    messages, where an empty list would contradict the metadata.
    """
    span = window(since=since, until=until)
    block: dict[str, Any] = {"window": span, "source": "sdk"}
    try:
        body = await client.get_project_kpi_cards(
            project_id,
            entity_type="traces",
            interval_start=span["since"],
            interval_end=span["until"],
            filters=SDK_SOURCE_FILTER,
        )
    except BLOCK_ERRORS as exc:
        # Not `decorations.block`: the summary keeps its window and source
        # alongside the error, and adds the one thing the other blocks have no
        # equivalent for — the call that answers the same question by hand.
        block["error"] = (
            f"{describe("this project's metrics", exc)}. "
            f"Retry, or count directly with list('trace', project_id='{project_id}', "
            f"since='{span['since']}')."
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
