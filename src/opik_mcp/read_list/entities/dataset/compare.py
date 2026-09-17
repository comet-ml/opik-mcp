"""``list('dataset_item', experiment_ids=[A, B])`` — which cases regressed.

The question this answers is the one two experiment records cannot: not "did
the average move" but "which cases moved". opik-backend joins the cases to the
runs already — it is what the UI's compare page reads — and nothing here does
that join, or reads a trace to do it: a page of twenty cases costs a page of
twenty cases, whether the dataset holds twenty or a hundred thousand.

It serves any experiments that ran the same dataset: plain ``evaluate()``
runs as much as test-suite runs. A test suite is a dataset whose experiments
carry ``evaluation_method = evaluation_suite``, and that flag adds exactly two
columns, ``passed`` and ``reason``, because only a suite records assertions.
Everything else — the cases, the scores, the worst trace — renders the same.

The call takes the whole ``list`` invocation rather than the shared collection
path because its rows are not records. A row is one case with several runs
hanging off it, its columns are computed from those runs, and the filters and
sort are only meaningful when there are runs to apply them to.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Final

from opik_mcp.opik_client import OpikReadClient
from opik_mcp.read_list.entities.dataset.layout import (
    PASS_SEPARATOR,
    RUN_SEPARATOR,
    Experiment,
    render,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import compile_filters, render_filters
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.sample import is_thin
from opik_mcp.read_list.sorting import compile_sort

#: What opik-backend stores on an experiment that ran a test suite. Not
#: ``test_suite``: its OPIK-5795 plans that rename and has not done it, so
#: this is the one place that knows the stored spelling.
TEST_SUITE_METHOD = "evaluation_suite"
#: opik-backend's ``ExperimentStatus.RUNNING``. The record's ``status`` is not
#: a filterable field, so it has no entry in the OQL enum table; the one value
#: the comparison has to recognise is named here.
RUNNING: Final = "running"

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
_ENTITY = "dataset_item"


async def run_compare(
    client: OpikReadClient,
    *,
    experiment_ids: list[str] | None = None,
    dataset_id: str | None = None,
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
    ran_dataset_id = _dataset_of(experiments, dataset_id)

    clauses = compile_filters(_ENTITY, filters) if filters else []
    stripping = _strips_runs(clauses, experiment_count=len(ids))
    if stripping and size > REFETCH_ROW_CAP:
        raise EntityArgValidationError(
            f"A filter on the runs ({', '.join(RUN_LEVEL_FIELDS)}) hides the experiments that "
            f"did not match, so each matched case is fetched again to put them back. "
            f"size={size} would be {size} extra requests; use size={REFETCH_ROW_CAP} or less, "
            "or filter on the case (data.<key>, id, comments) instead."
        )
    sorting, sort_label, sort_field = _sorting(sort)

    assertion_columns = any(experiment.is_suite for experiment in experiments)
    # The output keys are the first page's business only, and they do not
    # depend on it, so the two go out together rather than one after the other.
    page_result, *column_results = await asyncio.gather(
        client.list_compared_dataset_items(
            ran_dataset_id,
            experiment_ids=ids,
            filters=json.dumps(clauses, separators=(",", ":")) if clauses else None,
            sorting=sorting,
            search=search or None,
            page=page,
            size=size,
        ),
        *(
            [client.list_compared_output_columns(ran_dataset_id, experiment_ids=ids)]
            if page == 1
            else []
        ),
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
        rows, unrestored = await _with_every_run(client, ran_dataset_id, ids, rows)

    applied = [f"compare: {_legend(experiments)}"]
    if clauses:
        applied.append(f"filters: {render_filters(_ENTITY, clauses)}")
    if sort_label is not None:
        applied.append(sort_label)
    if search:
        applied.append(f'search: "{search}"')
    header = f"[list: {_ENTITY} | {' | '.join(applied)}]"

    # Warnings first: whether the table can be read at face value is decided
    # before how to read it.
    notes = [*_guards(experiments), _how_to_read(experiments, assertion_columns=assertion_columns)]
    if stripping and rows:
        notes.append(
            "A filter on the runs matches a case when any of its experiments matches; the "
            "experiments that did not match were fetched back onto the row, so what you see "
            "is the whole case."
        )
    keys_line = _keys_note(
        column_results[0] if column_results else None, rows, hide_echo=assertion_columns
    )
    if keys_line is not None:
        notes.append(keys_line)
    if search:
        notes.append(
            "search matched the cases' data, not the runs' output; to search the output, "
            'filter on it (output contains "…").'
        )
    caveat = _sort_caveat(sort_field)
    if caveat is not None:
        notes.append(caveat)
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
        assertion_columns=assertion_columns,
    )


def _keys_note(columns: Any, rows: list[dict[str, Any]], *, hide_echo: bool) -> str | None:
    """What this dataset and its runs can be filtered and sorted on, once.

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
    dataset_id: str,
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
            client.list_compared_dataset_items(
                dataset_id,
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
                f"compared runs, and a plain list of a dataset's cases has none. "
                f"E.g. list('{_ENTITY}', experiment_ids=['<uuid>', '<uuid>'], "
                f"filters='feedback_scores.correctness < 0.5')."
            )
        raise EntityArgValidationError(
            f"list('{_ENTITY}') needs dataset_id, or experiment_ids to compare runs."
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
            f"since/until are not supported for {_ENTITY}: a case belongs to a dataset, not "
            "to a window. Filter the experiments instead, or compare different ones."
        )


def _sorting(sort: str | None) -> tuple[str | None, str | None, str | None]:
    """The backend's ``sorting`` parameter, and what to echo in the header.

    A field the backend does not order by is refused by ``compile_sort``
    before the call, because the backend logs it and answers 200 with an
    unsorted page — the caller would read it as ordered.
    """
    if sort is None:
        return None, None, None
    field, direction = compile_sort(_ENTITY, sort)
    return (
        json.dumps([{"field": field, "direction": direction}], separators=(",", ":")),
        f"sort: {field} {direction.lower()}",
        field,
    )


#: Row fields the joined query averages across the compared runs
#: (``avgMap``/``avg`` in opik-backend's compare SELECT).
_AVERAGED_SORTS = ("feedback_scores.", "usage.")
_AVERAGED_SORT_FIELDS = ("duration", "total_estimated_cost")
#: Row fields it takes from the newest run instead (``argMax`` by created_at).
_NEWEST_RUN_SORTS = ("output.", "input.", "metadata.")


def _sort_caveat(field: str | None) -> str | None:
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


# --- the experiments, and the dataset they agree on ------------------------ #


async def _resolve(client: OpikReadClient, ids: list[str]) -> list[Experiment]:
    """Read the named experiments at once, in the order the caller named them.

    The first is the baseline. The records carry the dataset to query, the names
    the legend needs, and whether the run was a test suite — which is what
    decides if the table has a pass column at all.
    """
    records = await asyncio.gather(*(client.get_experiment(one) for one in ids))
    experiments = []
    for position, (experiment_id, record) in enumerate(zip(ids, records, strict=True)):
        dataset_id = record.get("dataset_id")
        if not dataset_id:
            raise EntityArgValidationError(
                f"Experiment {experiment_id!r} carries no dataset, so its cases cannot "
                "be lined up with another run's."
            )
        version_summary = record.get("dataset_version_summary")
        version_name = (
            version_summary.get("version_name") if isinstance(version_summary, dict) else None
        )
        trace_count = record.get("trace_count")
        experiments.append(
            Experiment(
                id=experiment_id,
                name=str(record.get("name") or experiment_id),
                label=f"E{position + 1}",
                dataset_id=str(dataset_id),
                dataset_name=str(record.get("dataset_name") or dataset_id),
                is_suite=record.get("evaluation_method") == TEST_SUITE_METHOD,
                dataset_version_id=(
                    str(record["dataset_version_id"]) if record.get("dataset_version_id") else None
                ),
                dataset_version=str(version_name) if version_name else None,
                status=str(record["status"]) if record.get("status") else None,
                trace_count=trace_count if isinstance(trace_count, int) else None,
            )
        )
    return experiments


def _guards(experiments: list[Experiment]) -> list[str]:
    """What makes this comparison unsafe to read at face value, before the table.

    Each was a check the agent had to make itself with a ``list('experiment')``
    call before comparing, and skipped, because the table renders either way.
    Three facts decide whether two averages are the same kind of number: the
    dataset version each run used (same dataset, different version means
    cases were added, edited or removed, so a dash may be a case that did not
    exist yet — a warning, not the refusal a different *dataset* gets, since
    the shared cases still line up); whether a run has finished (a running
    one's averages will move); and how many cases each covered (the incident
    behind ``experiment._SPINE`` — a mean over three beside a mean over
    twenty is not a comparison of the same thing).

    Every guard stays silent when a record lacks the field it reads. Older
    experiments carry no ``dataset_version_id`` and some carry no
    ``trace_count``; a guard that fired on absence would warn about a
    difference nobody can see.
    """
    if len(experiments) < 2:
        return []
    notes: list[str] = []

    versions = {e.dataset_version_id for e in experiments if e.dataset_version_id}
    if len(versions) > 1:
        ran = ", ".join(
            f"{e.label} ran {e.dataset_version or e.dataset_version_id or 'an unknown version'}"
            for e in experiments
        )
        notes.append(
            f"These runs used different versions of the dataset ({ran}): cases may have been "
            "added, edited or removed between them, so a - can mean the case did not exist yet, "
            "and a gap on an edited case is not a regression."
        )

    running = [e.label for e in experiments if e.status == RUNNING]
    if running:
        who = " and ".join(running)
        verb = "is" if len(running) == 1 else "are"
        notes.append(
            f"{who} {verb} still running: {'its' if len(running) == 1 else 'their'} scores are "
            "over the cases finished so far and will change."
        )

    counts = [(e.label, e.trace_count) for e in experiments if e.trace_count is not None]
    if len(counts) == len(experiments) and len({n for _, n in counts}) > 1:
        each = ", ".join(f"{label} {n}" for label, n in counts)
        smallest = min(n for _, n in counts)
        thin = f" {smallest} is too few to weigh against the others." if is_thin(smallest) else ""
        notes.append(f"The runs covered different numbers of cases ({each}).{thin}")
    return notes


def _dataset_of(experiments: list[Experiment], dataset_id: str | None) -> str:
    """The one dataset every compared experiment ran.

    Experiments of different datasets have no cases in common, so lining them
    up would produce a table of blanks rather than an answer. The UI refuses
    the same comparison in the same words.
    """
    dataset_ids = {experiment.dataset_id for experiment in experiments}
    if len(dataset_ids) > 1:
        ran = "; ".join(
            f"{experiment.name} ran {experiment.dataset_name} ({experiment.dataset_id})"
            for experiment in experiments
        )
        raise EntityArgValidationError(
            f"Cannot compare experiments that ran different datasets: {ran}. "
            "Compare experiments of one dataset."
        )
    ran_dataset_id = experiments[0].dataset_id
    if dataset_id and dataset_id != ran_dataset_id:
        raise EntityArgValidationError(
            f"dataset_id {dataset_id!r} is not the dataset these experiments ran "
            f"({experiments[0].dataset_name}, {ran_dataset_id}). Drop dataset_id: with "
            "experiment_ids the dataset is resolved from the experiments."
        )
    return ran_dataset_id


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


def _how_to_read(experiments: list[Experiment], *, assertion_columns: bool) -> str:
    """What the separators in a cell mean. The header already said who is who."""
    passed = (
        f" passed is passed/total runs, {PASS_SEPARATOR.join(e.label for e in experiments)}."
        if assertion_columns
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
