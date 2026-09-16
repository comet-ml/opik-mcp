"""``list('test_suite_item', experiment_ids=[A, B])`` — which cases regressed.

The question this answers is the one two experiment records cannot: not "did
the average move" but "which cases moved". opik-backend joins the cases to the
runs already — it is what the UI's compare page reads — and nothing here does
that join, or reads a trace to do it: a page of twenty cases costs a page of
twenty cases, whether the suite holds twenty or a hundred thousand.

The call takes the whole ``list`` invocation rather than the shared collection
path because its rows are not records. A row is one case with several runs
hanging off it, its columns are computed from those runs, and the filters and
sort are only meaningful when there are runs to apply them to.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.entities.test_suite.layout import (
    PASS_SEPARATOR,
    RUN_SEPARATOR,
    Experiment,
    render,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import compile_filters, render_filters
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.sorting import compile_sort

#: What opik-backend stores on an experiment that ran a test suite. Not
#: ``test_suite``: its OPIK-5795 plans that rename and has not done it, so
#: this is the one place that knows the stored spelling.
TEST_SUITE_METHOD = "evaluation_suite"

MAX_EXPERIMENTS = 10
#: Filter fields that live on the runs rather than on the case. opik-backend
#: applies these inside the join, so a row comes back carrying only the runs
#: that matched — the others are not absent, they are hidden.
RUN_LEVEL_FIELDS = ("feedback_scores", "output", "duration")
#: How many rows a run-level filter may put back together in one call. Each
#: costs a request, and one tool call turning into a hundred is not a page.
REFETCH_ROW_CAP = 25
#: A test suite's runs echo the case input back under ``input``, so it shows
#: up as an output key that is not one. The UI hides it on the same page.
ECHOED_OUTPUT_KEY = "input"
_ENTITY = "test_suite_item"


async def run_compare(
    client: OpikReadClient,
    *,
    experiment_ids: list[str] | None = None,
    test_suite_id: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    search: str | None = None,
    since: str | None = None,
    until: str | None = None,
    page: int | None = None,
    size: int | None = None,
    **_collection_args: Any,
) -> str:
    """The comparison, end to end: validate, resolve, ask, render."""
    ids = _validated_ids(experiment_ids, filters=filters, sort=sort)
    _refuse_window(since, until)
    # A runner is handed ``None`` for a page argument the caller did not
    # choose (see ``list_tool._run_whole``), so the defaults are applied here.
    page = max(1, page or 1)
    size = clamp_size(size)

    experiments = await _resolve(client, ids)
    suite_id = _suite_of(experiments, test_suite_id)

    clauses = compile_filters(_ENTITY, filters) if filters else []
    stripping = _strips_runs(clauses, experiment_count=len(ids))
    if stripping and size > REFETCH_ROW_CAP:
        raise EntityArgValidationError(
            f"A filter on the runs ({', '.join(RUN_LEVEL_FIELDS)}) hides the experiments that "
            f"did not match, so each matched case is fetched again to put them back. "
            f"size={size} would be {size} extra requests; use size={REFETCH_ROW_CAP} or less, "
            "or filter on the case (data.<key>, id, comments) instead."
        )
    sorting, sort_label = _sorting(sort)

    suite_columns = any(experiment.is_suite for experiment in experiments)
    # The output keys are the first page's business only, and they do not
    # depend on it, so the two go out together rather than one after the other.
    page_result, *column_results = await asyncio.gather(
        client.list_compared_test_suite_items(
            suite_id,
            experiment_ids=ids,
            filters=json.dumps(clauses, separators=(",", ":")) if clauses else None,
            sorting=sorting,
            search=search or None,
            page=page,
            size=size,
        ),
        *([client.list_compared_output_columns(suite_id, experiment_ids=ids)] if page == 1 else []),
        return_exceptions=True,
    )
    if isinstance(page_result, BaseException):
        raise page_result
    body = page_result
    rows = [row for row in body.get("content") or [] if isinstance(row, dict)]
    total_raw = body.get("total")
    total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else len(rows)

    unrestored: list[str] = []
    if stripping and rows:
        rows, unrestored = await _with_every_run(client, suite_id, ids, rows)

    applied = [f"compare: {_legend(experiments)}"]
    if clauses:
        applied.append(f"filters: {render_filters(_ENTITY, clauses)}")
    if sort_label is not None:
        applied.append(sort_label)
    if search:
        applied.append(f'search: "{search}"')
    header = f"[list: {_ENTITY} | {' | '.join(applied)}]"

    notes = [_how_to_read(experiments, suite_columns=suite_columns)]
    if stripping and rows:
        notes.append(
            "A filter on the runs matches a case when any of its experiments matches; the "
            "experiments that did not match were fetched back onto the row, so what you see "
            "is the whole case."
        )
    keys_line = _keys_note(
        column_results[0] if column_results else None, rows, hide_echo=suite_columns
    )
    if keys_line is not None:
        notes.append(keys_line)
    if search:
        notes.append(
            "search matched the cases' data, not the runs' output; to search the output, "
            'filter on it (output contains "…").'
        )
    if unrestored:
        notes.append(_unrestored_note(unrestored))
    if not rows:
        # An empty page still says what could be asked next: the keys, the
        # search semantics and the legend are what turn it into a second call.
        reason = _empty(
            stripping=stripping,
            filtered=bool(clauses),
            searched=bool(search),
            page=page,
            total=total,
        )
        return "\n".join([header, reason, "", *notes])

    return render(
        rows,
        experiments,
        total=total,
        page=page,
        size=size,
        header=header,
        notes=notes,
        suite_columns=suite_columns,
    )


def _keys_note(columns: Any, rows: list[dict[str, Any]], *, hide_echo: bool) -> str | None:
    """What this suite and its runs can be filtered and sorted on, once.

    The case keys are read off the page; the runs' output keys need a call,
    which only the first page makes. A failed columns call costs the line, not
    the page.
    """
    case_keys = sorted({key for row in rows for key in (row.get("data") or {})})
    output_keys: list[str] = []
    if isinstance(columns, dict):
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


def _unrestored_note(case_ids: list[str]) -> str:
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


def _strips_runs(clauses: list[dict[str, str]], *, experiment_count: int) -> bool:
    """Will this filter hide runs the caller needs to see?

    Only a filter on the runs does, and only when there is more than one run
    to hide: comparing a single experiment, the row carries what matched and
    nothing was lost.
    """
    if experiment_count < 2:
        return False
    return any(clause["field"].split(".")[0] in RUN_LEVEL_FIELDS for clause in clauses)


async def _with_every_run(
    client: OpikReadClient,
    suite_id: str,
    ids: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Put the stripped experiments back, one request per matched case.

    The backend has no list operator for ``id`` on this endpoint — it is an
    exact-match string field, and clauses are ANDed — so the page cannot be
    asked for again in one call. It is asked for a row at a time instead, in
    parallel, which is why the page is capped before we get here.

    A row whose refetch fails keeps what the filter returned: half a row is
    still an answer, and the note names the rows it could not complete, so a
    caller reads the partial ones as partial rather than distrusting the page.
    """
    fetched = await asyncio.gather(
        *(
            client.list_compared_test_suite_items(
                suite_id,
                experiment_ids=ids,
                filters=json.dumps(
                    [{"field": "id", "operator": "=", "key": "", "value": str(row.get("id"))}],
                    separators=(",", ":"),
                ),
                page=1,
                size=1,
            )
            for row in rows
        ),
        return_exceptions=True,
    )
    whole: list[dict[str, Any]] = []
    unrestored: list[str] = []
    for row, result in zip(rows, fetched, strict=True):
        content = (
            result.get("content") if isinstance(result, dict) and result.get("content") else None
        )
        if not content or not isinstance(content[0], dict):
            unrestored.append(str(row.get("id") or "?"))
            whole.append(row)
            continue
        whole.append(content[0])
    return whole, unrestored


# --- what the call has to get right before anything is fetched ------------- #


def _validated_ids(
    experiment_ids: list[str] | None, *, filters: str | None, sort: str | None
) -> list[str]:
    if not experiment_ids:
        asked = [name for name, given in (("filters", filters), ("sort", sort)) if given]
        if asked:
            raise EntityArgValidationError(
                f"{' and '.join(asked)} on {_ENTITY} need experiment_ids: they apply to the "
                f"compared runs, and a plain list of a suite's cases has none. "
                f"E.g. list('{_ENTITY}', experiment_ids=['<uuid>', '<uuid>'], "
                f"filters='feedback_scores.correctness < 0.5')."
            )
        raise EntityArgValidationError(
            f"list('{_ENTITY}') needs test_suite_id, or experiment_ids to compare runs."
        )
    if not isinstance(experiment_ids, list) or not all(
        isinstance(one, str) and one.strip() for one in experiment_ids
    ):
        raise EntityArgValidationError(
            "experiment_ids must be a non-empty array of experiment ids, "
            "e.g. experiment_ids=['<uuid>', '<uuid>']."
        )
    ids = [one.strip() for one in experiment_ids]
    if len(ids) > MAX_EXPERIMENTS:
        raise EntityArgValidationError(
            f"Cannot compare {len(ids)} experiments at once: the row would carry "
            f"{len(ids)} values per score. Compare up to {MAX_EXPERIMENTS}."
        )
    if len(set(ids)) != len(ids):
        raise EntityArgValidationError(
            "experiment_ids repeats an experiment; name each one once — the baseline is "
            "the first id, and the rest are compared against it."
        )
    return ids


def _refuse_window(since: str | None, until: str | None) -> None:
    if since is not None or until is not None:
        raise EntityArgValidationError(
            f"since/until are not supported for {_ENTITY}: a case belongs to a suite, not "
            "to a window. Filter the experiments instead, or compare different ones."
        )


def _sorting(sort: str | None) -> tuple[str | None, str | None]:
    """The backend's ``sorting`` parameter, and what to echo in the header.

    A field the backend does not order by is refused by ``compile_sort``
    before the call, because the backend logs it and answers 200 with an
    unsorted page — the caller would read it as ordered.
    """
    if sort is None:
        return None, None
    field, direction = compile_sort(_ENTITY, sort)
    return (
        json.dumps([{"field": field, "direction": direction}], separators=(",", ":")),
        f"sort: {field} {direction.lower()}",
    )


# --- the experiments, and the suite they agree on -------------------------- #


async def _resolve(client: OpikReadClient, ids: list[str]) -> list[Experiment]:
    """Read the named experiments at once, in the order the caller named them.

    The first is the baseline. The records carry the suite to query, the names
    the legend needs, and whether the run was a test suite — which is what
    decides if the table has a pass column at all.
    """
    records = await asyncio.gather(*(client.get_experiment(one) for one in ids))
    experiments = []
    for position, (experiment_id, record) in enumerate(zip(ids, records, strict=True)):
        dataset_id = record.get("dataset_id")
        if not dataset_id:
            raise EntityArgValidationError(
                f"Experiment {experiment_id!r} carries no test suite, so its cases cannot "
                "be lined up with another run's."
            )
        experiments.append(
            Experiment(
                id=experiment_id,
                name=str(record.get("name") or experiment_id),
                label=f"E{position + 1}",
                dataset_id=str(dataset_id),
                dataset_name=str(record.get("dataset_name") or dataset_id),
                is_suite=record.get("evaluation_method") == TEST_SUITE_METHOD,
            )
        )
    return experiments


def _suite_of(experiments: list[Experiment], test_suite_id: str | None) -> str:
    """The one suite every compared experiment ran.

    Experiments of different suites have no cases in common, so lining them up
    would produce a table of blanks rather than an answer. The UI refuses the
    same comparison in the same words.
    """
    suite_ids = {experiment.dataset_id for experiment in experiments}
    if len(suite_ids) > 1:
        ran = "; ".join(
            f"{experiment.name} ran {experiment.dataset_name} ({experiment.dataset_id})"
            for experiment in experiments
        )
        raise EntityArgValidationError(
            f"Cannot compare experiments that ran different test suites: {ran}. "
            "Compare experiments of one suite."
        )
    suite_id = experiments[0].dataset_id
    if test_suite_id and test_suite_id != suite_id:
        raise EntityArgValidationError(
            f"test_suite_id {test_suite_id!r} is not the suite these experiments ran "
            f"({experiments[0].dataset_name}, {suite_id}). Drop test_suite_id: with "
            "experiment_ids the suite is resolved from the experiments."
        )
    return suite_id


# --- the lines under the table --------------------------------------------- #


def _legend(experiments: list[Experiment]) -> str:
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


def _how_to_read(experiments: list[Experiment], *, suite_columns: bool) -> str:
    """What the separators in a cell mean. The header already said who is who."""
    passed = (
        f" passed is passed/total runs, {PASS_SEPARATOR.join(e.label for e in experiments)}."
        if suite_columns
        else ""
    )
    if len(experiments) == 1:
        return f"Score cells carry {experiments[0].label}'s value.{passed}"
    order = RUN_SEPARATOR.join(e.label for e in experiments)
    gap = ", and Δ is the unsigned gap between them" if len(experiments) == 2 else ""
    return (
        f"{experiments[0].label} is the baseline; score cells read {order} "
        f"in that order{gap}.{passed}"
    )


def _empty(*, stripping: bool, filtered: bool, searched: bool, page: int, total: int) -> str:
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
    if stripping:
        return (
            "No case matched. A filter on the runs matches a case when any of its "
            "experiments matches, so nothing here scored or ran the way you asked."
        )
    if filtered:
        return "No case matched the filter."
    if searched:
        return "No case matched the search. Search matches the case data, not the runs' output."
    return "No cases found: these experiments have no items in common."
