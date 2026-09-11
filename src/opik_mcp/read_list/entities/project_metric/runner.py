"""``list('project_metric', …)`` end to end: validate, ask, render.

The catalog next door says what can be asked and refuses what cannot; the
table says how an answer reads. This is the order of operations between them,
and the two backend calls it takes: the metric, and for a rate or an average
the count that says which buckets were empty.

Everything that can be rejected is rejected before the backend is called. Its
own refusals for these are unusable (a bad filter comes back as ``Invalid
filters query parameter`` with no field named), so the local check is not an
optimisation, it is the only readable error.
"""

from __future__ import annotations

from asyncio import gather
from collections.abc import Coroutine
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.entities.project_metric.catalog import (
    SOURCE_FILTERED_METRIC_ENTITIES,
    Metric,
    companion_count,
    parse_breakdown,
    parse_interval,
    parse_metric,
    refuse_dropped_fields,
    request_body,
    resolve_series,
    resolve_window,
)
from opik_mcp.read_list.entities.project_metric.table import (
    Presence,
    Table,
    bucket_counts,
    carries_data,
    render,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import SDK_SOURCE_CLAUSE, compile_filters, render_filters
from opik_mcp.read_list.project_names import (
    SCORE_NAMES_CAP,
    recorded,
)
from opik_mcp.read_list.project_scope import require_project_id

# --- checking the series against the project ------------------------------ #
#
# A percentile can be checked against a fixed set; a score name and a usage
# key cannot — they are whatever this project recorded. An unknown one is not
# refused by the backend either: it charts nothing and returns an empty
# series, which reads as "quiet window" when it means "wrong name". So the
# name is checked against the project's own — but only when the answer came
# back empty, because a chart with data has proved its own name. The happy
# path pays nothing; the ambiguous one pays one cheap GET to say which of the
# two it was.


CHECKED_SERIES: Final[dict[str, str]] = {"token_usage": "usage", "feedback_scores": "score"}
_KIND_WORDS: Final[dict[str, tuple[str, str, int | None]]] = {
    # Usage keys are listed whole: nothing else enumerates them.
    "usage": ("usage key", "usage keys", None),
    "score": ("feedback score name", "score names", SCORE_NAMES_CAP),
}


def _listed(names: list[str], cap: int | None) -> str:
    if cap is None or len(names) <= cap:
        return ", ".join(names)
    return f"{', '.join(names[:cap])} (and {len(names) - cap} more)"


async def check_series(
    client: OpikReadClient,
    project_id: str,
    metric: Metric,
    chosen: str,
    *,
    defaulted: bool,
) -> str | None:
    """Refuse an unrecorded series name; confirm a recorded one.

    Returns a note for the empty answer when the name checks out — that the
    window is empty is worth saying plainly, so the agent stops suspecting the
    name and moves the window instead.
    """
    kind = CHECKED_SERIES.get(metric.family)
    if kind is None:
        return None
    names = await recorded(client, project_id, kind=kind)
    if names is None:
        # The lookup failed. Refusing a name we could not check would be worse
        # than the ambiguity we were trying to remove.
        return None
    singular, plural, cap = _KIND_WORDS[kind]
    if chosen in names:
        return f"series={chosen!r} is a {singular} this project records, so the window is empty."
    if not names:
        raise EntityArgValidationError(
            f"This project has no {plural} recorded at all, so {metric.name} cannot be "
            f"grouped by one. series={chosen!r} matches nothing."
        )
    how = (
        f"{metric.name} grouped charts one series at a time and defaults to "
        f"series={chosen!r}, which this project does not record"
        if defaulted
        else f"series={chosen!r} is not a {singular} in this project"
    )
    raise EntityArgValidationError(f"{how}. Its {plural}: {_listed(names, cap)}.")


async def run_project_metric(
    client: OpikReadClient,
    *,
    project_id: str | None,
    project_name: str | None,
    metric_type: str | None,
    interval: str | None,
    since: str | None,
    until: str | None,
    filters: str | None,
    breakdown: str | None = None,
    series: str | None = None,
    page: int | None = None,
    size: int | None = None,
    sort: str | None = None,
) -> str:
    """``list('project_metric', …)`` end to end: validate, ask, render.

    Everything that can be rejected is rejected before the backend is called —
    an unknown metric, an unknown interval, a filter field that does not exist
    on the metric's entity. The backend's own refusals for these are unusable
    (a bad filter comes back as ``Invalid filters query parameter`` with no
    field named), so local validation is not an optimisation, it is the only
    readable error. Size is not on the list: a wide answer is the caller's to
    ask for, and the interval a caller does not name follows the window so the
    default is never wide (see ``interval_for_window``).
    """
    _refuse_collection_args(page=page, size=size, sort=sort)
    metric = parse_metric(metric_type)
    window_since, window_until = resolve_window(since, until)
    interval_name = parse_interval(interval, since=window_since, until=window_until)

    clauses = compile_filters(metric.entity, filters or "")
    refuse_dropped_fields(metric, clauses)
    if metric.entity in SOURCE_FILTERED_METRIC_ENTITIES and not any(
        clause["field"] == "source" for clause in clauses
    ):
        # Same default as the Logs page and the other lists. Echoed as part of
        # the filter rather than called out separately: the filter line already
        # reads `source = "sdk"`, and saying it twice is two claims where the
        # agent has to check they agree.
        clauses.append(dict(SDK_SOURCE_CLAUSE))

    name = metric.name
    grouping = parse_breakdown(breakdown, name) if breakdown else None
    chosen = resolve_series(metric, series, grouped=grouping is not None)
    if grouping is not None and chosen is not None:
        grouping["sub_metric"] = chosen

    resolved = await require_project_id(
        client,
        project_id=project_id,
        project_name=project_name,
        caller="list('project_metric')",
    )

    def ask(which: Metric, group: dict[str, str] | None) -> Coroutine[Any, Any, dict[str, Any]]:
        return client.get_project_metrics(
            resolved,
            **request_body(
                which,
                interval=interval_name,
                since=window_since,
                until=window_until,
                clauses=clauses,
                breakdown=group,
            ),
        )

    # A rate or an average needs to know which buckets were empty, and the
    # backend will not say — so the count rides along on the same connection,
    # concurrently. If it fails the series is still rendered: a chart with an
    # ambiguous zero beats no chart, and the note is simply absent.
    counting = companion_count(metric)
    answers = await gather(
        ask(metric, grouping),
        *([ask(counting, None)] if counting else []),
        return_exceptions=True,
    )
    body = answers[0]
    if isinstance(body, BaseException):
        raise body
    presence = None
    if counting is not None and not isinstance(answers[1], BaseException):
        presence = Presence(entity=counting.entity, counts=bucket_counts(answers[1]))

    # A derived interval is echoed as a choice, like the defaulted series
    # below: the caller never typed it and the numbers depend on it.
    interval_shown = interval_name if interval else f"{interval_name} (from the window)"
    applied = [name, interval_shown, f"{window_since} → {window_until}"]
    if clauses:
        applied.append(f"filters: {render_filters(metric.entity, clauses)}")
    if breakdown:
        grouped_by = f"by {breakdown.strip().lower()}"
        # The chosen series is echoed with the grouping because it changes what
        # the numbers are, and one of the two is a default the caller never
        # typed.
        applied.append(f"{grouped_by} ({chosen})" if chosen else grouped_by)
    header = f"[list: project_metric | {' | '.join(applied)}]"
    table = render(
        body,
        Table(
            metric_name=name,
            family=metric.family,
            interval=interval_name,
            grouped=grouping is not None,
            until=window_until,
            presence=presence,
        ),
    )
    if chosen is not None and not carries_data(body):
        # Nothing came back, and a name the caller (or the default) supplied
        # could be why. Which of the two it is, is worth one GET.
        note = await check_series(client, resolved, metric, chosen, defaulted=series is None)
        if note:
            table = f"{table}\n{note}"
    return f"{header}\n{table}"


def _refuse_collection_args(*, page: int | None, size: int | None, sort: str | None) -> None:
    """``page``/``size``/``sort`` mean nothing here, so they are refused.

    Silently ignoring them would let an agent believe it had paged through a
    series it actually re-read from the start, or ordered rows that are ordered
    by time by definition.
    """
    named = [
        name
        for name, value in (("page", page), ("size", size), ("sort", sort))
        if value is not None
    ]
    if not named:
        return
    raise EntityArgValidationError(
        f"list('project_metric') does not take {', '.join(named)}: rows are time "
        "buckets, not records — they are ordered by time and the window and "
        "interval decide how many there are. Use since/until and interval instead."
    )


__all__ = ["run_project_metric"]
