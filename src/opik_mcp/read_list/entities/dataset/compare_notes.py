"""The lines around the comparison's table: the legend, how to read a cell,
why a page is empty, and what the filters and sort did to it.
"""

from __future__ import annotations

from collections.abc import Sequence

from opik_mcp.client.shapes import Columns, DatasetItem
from opik_mcp.read_list.entities.dataset.compared_row import (
    PASS_SEPARATOR,
    RUN_SEPARATOR,
    Experiment,
)

#: A test suite's runs echo the case input back under ``input``, so it shows
#: up as an output key that is not one. The UI hides it on the same page.
ECHOED_OUTPUT_KEY = "input"


def keys_note(
    columns: Columns | BaseException | None, rows: Sequence[DatasetItem], *, hide_echo: bool
) -> str | None:
    """What this dataset and its runs can be filtered and sorted on, once.

    The case keys are read off the page; the runs' output keys need a call,
    which only the first page makes. A failed columns call costs the line, not
    the page.
    """
    case_keys = sorted({key for row in rows for key in (row.get("data") or {})})
    output_keys: list[str] = []
    if columns is not None and not isinstance(columns, BaseException):
        output_keys = [
            str(column["name"])
            for column in columns.get("columns") or []
            if isinstance(column, dict) and column.get("name")
        ]
        if hide_echo:
            # A suite's runs echo the case back under ``input``; it is the case,
            # not an output.
            output_keys = [key for key in output_keys if key != ECHOED_OUTPUT_KEY]
    if not case_keys and not output_keys:
        return None
    parts = []
    if output_keys:
        parts.append(f"runs' output keys: {', '.join(output_keys)}")
    if case_keys:
        parts.append(f"case data keys: {', '.join(case_keys)}")
    return f"{'; '.join(parts)}. Filter or sort on them as output.<key> and data.<key>."


#: How many failed refetches a note names before it starts counting. Naming
#: all of a capped page is 25 uuids of note for a backend having a bad minute.
_NAMED_UNRESTORED = 3


def unrestored_note(case_ids: list[str]) -> str:
    """Which rows on the page show only the runs that matched the filter.

    A count alone makes the whole page suspect; the ids make exactly those
    rows suspect, and each one is a ``filters='id = "…"'`` away from a retry.
    """
    named = ", ".join(case_ids[:_NAMED_UNRESTORED])
    if len(case_ids) > _NAMED_UNRESTORED:
        named += f", and {len(case_ids) - _NAMED_UNRESTORED} more"
    plural = "s" if len(case_ids) != 1 else ""
    return (
        f"{len(case_ids)} row{plural} could not be fetched again and show{'' if plural else 's'} "
        f"only the runs that matched the filter: {named}."
    )


#: Row fields the joined query averages across the compared runs
#: (``avgMap``/``avg`` in opik-backend's compare SELECT).
_AVERAGED_SORTS = ("feedback_scores.", "usage.")
_AVERAGED_SORT_FIELDS = ("duration", "total_estimated_cost")
#: Row fields it takes from the newest run instead (``argMax`` by created_at).
_NEWEST_RUN_SORTS = ("output.", "input.", "metadata.")


def sort_caveat(field: str | None) -> str | None:
    """What a sort on a compared row actually ordered by.

    A joined row has one value per column and several runs behind it, so the
    backend has to pick one: it averages the numbers across the compared runs
    and takes the bodies from the newest run. Neither is the baseline, and a
    caller ranking regressions by score would otherwise read the order as the
    baseline's. Case-level fields (id, created_at, data.<key>, comments) have
    one value per row and need no warning.
    """
    if field is None:
        return None
    if field.startswith(_AVERAGED_SORTS) or field in _AVERAGED_SORT_FIELDS:
        return (
            f"The sort on {field} ordered the page by that value averaged across the compared "
            "runs, which is what the joined row carries — not by the baseline's own value."
        )
    if field.startswith(_NEWEST_RUN_SORTS):
        return (
            f"The sort on {field} ordered the page by the most recent run's value on each case, "
            "which is what the joined row carries — not by the baseline's."
        )
    return None


def legend(experiments: list[Experiment]) -> str:
    """Which experiment each ``E<n>`` is, in the header, above the table.

    The labels are the key to every cell on the page, so they go where they
    are read before the rows rather than in a note under them — and they carry
    the ids, because the caller's next call (another comparison, a read of the
    losing run) is written with an id and not with a name.
    """
    return ", ".join(
        f"{e.label} = {'baseline ' if position == 0 and len(experiments) > 1 else ''}"
        f"{e.name} ({e.id})"
        for position, e in enumerate(experiments)
    )


def how_to_read(experiments: list[Experiment], *, assertion_columns: bool) -> str:
    """What the separators in a cell mean. The header already said who is who."""
    passed = (
        f" passed is passed/total runs, {PASS_SEPARATOR.join(e.label for e in experiments)}."
        if assertion_columns
        else ""
    )
    if len(experiments) == 1:
        return f"Score cells carry {experiments[0].label}'s value.{passed}"
    order = RUN_SEPARATOR.join(e.label for e in experiments)
    gap = ""
    if len(experiments) == 2:
        # The sign is arithmetic. Opik's feedback definitions record a score's
        # type and range and nothing about which way it improves, so the
        # table cannot call a drop a regression; it says who scored higher
        # and leaves the reading of a lower-is-better metric to the caller,
        # in so many words — and in the column header too, which is where a
        # caller reading a Δ is looking (``layout._header``). Phrased as the
        # rule rather than as a claim about this page: with only a categorical
        # or a two-authored score there is no Δ to mark, and the sentence has
        # to be true there as well.
        gap = (
            f", and Δ is {experiments[1].label} minus {experiments[0].label} (a + means "
            f"{experiments[1].label} scored higher). No score definition records which "
            "direction is better, so a score column carrying a Δ is marked direction "
            "unknown: on a lower-is-better metric a + is the regression"
        )
    return (
        f"{experiments[0].label} is the baseline; score cells read {order} "
        f"in that order{gap}.{passed}"
    )


def why_empty(
    *, strips_runs: bool, is_filtered: bool, is_searched: bool, page: int, total: int
) -> str:
    """Why this page is empty — which is four different things.

    Answering "no items in common" to a page past the end, or explaining
    any-run semantics to someone who filtered on the case, sends the caller
    looking for a problem that is not there.
    """
    if page > 1 and total:
        return (
            f"Page {page} is past the end: the comparison has {total} "
            f"case{'s' if total != 1 else ''}. Ask for an earlier page."
        )
    if strips_runs:
        return (
            "No case matched. A filter on the runs matches a case when any of its "
            "experiments matches, so nothing here scored or ran the way you asked."
        )
    if is_filtered:
        return "No case matched the filter."
    if is_searched:
        return "No case matched the search. Search matches the case data, not the runs' output."
    return "No cases found: these experiments have no items in common."
