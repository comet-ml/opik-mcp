"""One metric's series, as a pipe table.

Split from ``project_metrics`` because it shares nothing with the catalog next
door: this half knows about buckets, columns and what a number means, and
nothing about which metrics exist or which groupings the backend accepts. The
seam is one function, ``render``.

**Rows are keyed by time, never by position.** The ungrouped queries fill
every bucket in the window (``WITH FILL``), so for years the series arrived
the same length and the same shape, and reading the *n*-th point of each one
worked. The grouped queries have no fill: ``ProjectMetricsService`` builds
each group's ``data`` from that group's own rows, so a model that ran on one
day of seven comes back with one point, and its neighbour with seven. Read
positionally, that one point lands in the first row and is labelled with
somebody else's Monday — a wrong attribution with a plausible number, which is
the worst thing this tool can do. So the row axis is the union of the times
present, and every cell is looked up by its own timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

OTHERS: Final = "__others__"
"""The backend's own bucket for groups past its limit (``BreakdownQueryBuilder``).

It is not aggregated on the way out — ``ProjectMetricsService`` concatenates
the remaining groups' rows and relabels them — so this series alone can carry
several points at one timestamp. Summing them is right for a count and
nonsense for a percentile, so the sum is only taken where it means something.
"""

ADDITIVE_FAMILIES: Final = frozenset({"count", "cost", "token_usage", "guardrails"})
"""Families whose points can be added together.

An average, a percentile, a rate and a score cannot: the parts do not carry
the weights the whole would need.
"""

MAX_SERIES: Final = 11
"""Columns kept.

The backend caps a grouping at ten groups and then adds ``__others__``, so
eleven is the widest honest answer to a grouped question — capping at ten
would silently drop a series the backend deliberately included. The cap is
really for the *ungrouped* feedback-score and token-usage metrics, which fan
out into one series per score name or usage key with nothing bounding them: a
project with sixty score names would otherwise return sixty columns. That
width is not knowable before the call, so it is capped on the way out — widest
first, since a change hides in the big ones — with the true count stated and a
pointer to where the full list of names lives.
"""


@dataclass(frozen=True)
class Presence:
    """How many of the metric's entity fell in each bucket, by timestamp.

    Keyed by time rather than by index for the same reason the table is: the
    count is a second query, and nothing guarantees its buckets line up with
    the metric's row for row.
    """

    entity: str
    counts: dict[str, float]

    def empty(self, when: str) -> bool:
        return not self.counts.get(when, 0.0)

    def nothing_at_all(self) -> bool:
        return not any(self.counts.values())


def series_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The well-formed series in a metric answer."""
    raw = body.get("results")
    found = [one for one in raw if isinstance(one, dict)] if isinstance(raw, list) else []
    return [one for one in found if isinstance(one.get("data"), list)]


def points_of(one: dict[str, Any]) -> list[dict[str, Any]]:
    return [point for point in one["data"] if isinstance(point, dict)]


def bucket_counts(body: dict[str, Any]) -> dict[str, float]:
    """A count series as ``{time: value}`` — what ``Presence`` is built from."""
    series = series_of(body)
    if not series:
        return {}
    return {
        str(point.get("time")): float(point.get("value") or 0)
        for point in points_of(series[0])
        if isinstance(point.get("time"), str)
    }


def all_zero(series: list[dict[str, Any]]) -> bool:
    """True when not one point in any series carries a non-zero value.

    Thirty-one rows of ``| 0`` cost 148 tokens to say nothing happened, and a
    cost or error-rate question on a quiet project produces exactly that. The
    answer is the same either way; only one of the two is worth paying for.
    """
    return all(not point.get("value") for one in series for point in points_of(one))


def carries_data(body: dict[str, Any]) -> bool:
    """True when the answer holds at least one non-zero number.

    The test for "this told me nothing" — no series at all, or every point
    null or zero. It is the condition the table collapses on, and the
    condition under which a supplied series name is worth checking.
    """
    series = series_of(body)
    return bool(series) and not all_zero(series)


SMALL: Final = 1e-4
"""Below this, four decimal places round a real number to nothing.

Costs live here: a bucket at $0.000032 printed as ``0`` reads as "no cost",
and the table would then contradict the summary, which reports the same
figure as ``1.35e-05``. Small numbers keep significant digits instead.
"""


def number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    if abs(as_float) < SMALL:
        return f"{as_float:.3g}"
    return f"{round(as_float, 4):g}"


def time_label(raw: Any, interval: str, *, until: str | None = None) -> str:
    """Buckets are labelled at the precision they mean.

    A daily bucket labelled with a time implies a precision it does not have,
    and it costs eleven characters a row to imply it.

    ``total`` is the case worth care: the backend labels its single bucket with
    the window's *start*, so passing that through reads as "on the 3rd there
    were 5" when the number is the whole week's. It gets the span instead.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    if interval == "total":
        return f"{raw[:10]} → {until[:10]}" if until else f"since {raw[:10]}"
    return raw[:16].replace("T", " ") if interval == "hourly" else raw[:10]


NO_GROUP: Final = "(no value)"
"""A group whose key was absent on the rows in it.

The backend labels such a group with an empty string. Falling back to the
metric's own name printed `span_token_usage` as a column under a header that
said `by metadata.environment`, which reads as the ungrouped total rather
than as "the spans with no environment set".
"""


def _column(one: dict[str, Any], table: Table) -> str:
    name = one.get("name")
    if isinstance(name, str) and name:
        return name
    return NO_GROUP if table.grouped else table.metric_name


def _weight(one: dict[str, Any]) -> float:
    """Total magnitude across the window — the series most likely to matter."""
    return sum(
        abs(float(point["value"]))
        for point in points_of(one)
        if isinstance(point.get("value"), (int, float))
    )


def _by_time(one: dict[str, Any]) -> dict[str, list[Any]]:
    """A series as ``{time: [value, …]}`` — a list because ``__others__`` repeats."""
    found: dict[str, list[Any]] = {}
    for point in points_of(one):
        when = point.get("time")
        if isinstance(when, str):
            found.setdefault(when, []).append(point.get("value"))
    return found


def _cell(values: list[Any] | None, *, additive: bool) -> tuple[Any, bool]:
    """One cell's value, and whether it had to be dropped as uncombinable."""
    if not values:
        return None, False
    if len(values) == 1:
        return values[0], False
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    if not additive:
        return None, True
    return (sum(numbers) if numbers else None), False


@dataclass(frozen=True)
class Table:
    """What to render: the answer, and how to talk about it."""

    metric_name: str
    family: str
    interval: str
    grouped: bool = False
    until: str | None = None
    names_source: str | None = None
    presence: Presence | None = None


def render(body: dict[str, Any], table: Table) -> str:
    """The series as ``time | <series> …``, one row per bucket.

    Series are columns rather than repeated blocks, which is the whole reason
    this is a table: a bucket's timestamp is printed once per row instead of
    once per point.
    """
    metric_name = table.metric_name
    series = series_of(body)
    if not series:
        return f"No {metric_name} data in this window."

    presence = table.presence
    if presence is not None and presence.nothing_at_all():
        return (
            f"No {presence.entity}s in this window, so {metric_name} has no value in it. "
            "(The backend reports 0 for an empty bucket; there was nothing to measure.)"
        )

    if all_zero(series):
        # The window is already on the header line, so the table would add
        # nothing but its own length. Seen live: 31 rows of "| 0" for a cost
        # question on a project with no cost.
        return f"No {metric_name} recorded in this window — {_nothing_there(table.family)}."

    total_series = len(series)
    if total_series > MAX_SERIES:
        series = sorted(series, key=_weight, reverse=True)[:MAX_SERIES]

    columns = [_column(one, table) for one in series]
    charted = [_by_time(one) for one in series]
    # The union, because grouped series are not filled and each group carries
    # only the buckets it appeared in.
    axis = sorted({when for one in charted for when in one})
    additive = table.family in ADDITIVE_FAMILIES

    lines = [" | ".join(["time", *columns])]
    quiet_rows = 0
    empty_rows = 0
    uncombinable = False
    for when in axis:
        if presence is not None and presence.empty(when):
            # Not blanked in place: a row of empty cells costs as much to print
            # as a real one and carries less. Seen live — a 14-day error rate
            # on a project used one day was 13 rows of "| " for 103 tokens.
            quiet_rows += 1
            continue
        cells = []
        for one in charted:
            value, dropped = _cell(one.get(when), additive=additive)
            uncombinable = uncombinable or dropped
            cells.append(value)
        if all(value is None for value in cells):
            # The backend's own "no data here" — a null, not a zero. Seen live:
            # nine days of `duration.p50 | duration.p90 | duration.p99` with one
            # day of numbers and eight rows of ` |  | `.
            empty_rows += 1
            continue
        lines.append(
            " | ".join(
                [time_label(when, table.interval, until=table.until), *(number(v) for v in cells)]
            )
        )

    lines.extend(_notes(table, axis, quiet_rows, empty_rows, total_series, columns, uncombinable))
    return "\n".join(lines)


def _nothing_there(family: str) -> str:
    """Why every bucket is empty, in the terms of the metric that was asked for.

    A feedback-score average comes back as null when it is genuinely zero —
    the backend's own ``nullIf(avg(value), 0)`` — so "every bucket is zero" is
    the one reading this table cannot support for that family.
    """
    if family == "feedback_scores":
        return "no bucket carries a score (the backend reports an average of 0 as no score)"
    return "every bucket is zero"


def _notes(
    table: Table,
    axis: list[str],
    quiet_rows: int,
    empty_rows: int,
    total_series: int,
    columns: list[str],
    uncombinable: bool,
) -> list[str]:
    """The lines under the table: what was left out, and why."""
    lines: list[str] = []
    presence = table.presence
    for skipped, reason in (
        (
            quiet_rows,
            f"no {presence.entity}s in them, so nothing to measure — which is not the same as zero"
            if presence is not None
            else "",
        ),
        (empty_rows, f"no {table.metric_name} recorded in them"),
    ):
        if skipped and reason:
            lines.append("")
            lines.append(f"{skipped} of {len(axis)} buckets are not listed: {reason}.")

    if OTHERS in columns:
        lines.append("")
        lines.append(
            f"{OTHERS} is the backend's own bucket for every group past its top ten"
            + (
                "; it arrives unaggregated, and these values cannot be summed for this "
                "metric, so its cells are blank."
                if uncombinable
                else ", summed per bucket."
            )
        )

    if total_series > MAX_SERIES:
        where = f" All {total_series} names: {table.names_source}." if table.names_source else ""
        lines.append("")
        lines.append(
            f"The {MAX_SERIES} largest of {total_series} series, by total over the window.{where}"
        )
    return lines


__all__ = [
    "ADDITIVE_FAMILIES",
    "MAX_SERIES",
    "OTHERS",
    "Presence",
    "Table",
    "all_zero",
    "bucket_counts",
    "carries_data",
    "number",
    "points_of",
    "render",
    "series_of",
    "time_label",
]
