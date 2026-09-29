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

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.read_list.entities.dataset.compare_guards import dataset_of, guards
from opik_mcp.read_list.entities.dataset.compare_notes import (
    how_to_read,
    keys_note,
    legend,
    sort_caveat,
    unrestored_note,
    why_empty,
)
from opik_mcp.read_list.entities.dataset.compared_row import (
    NO_KINDS,
    Experiment,
    ScoreKinds,
)
from opik_mcp.read_list.entities.dataset.figures import (
    CATEGORY_CALL_CAP,
    Figures,
    category_clause,
    figures_note,
    label_counts_wanted,
    render_figures,
)
from opik_mcp.read_list.entities.dataset.layout import render
from opik_mcp.read_list.entities.dataset.vocabulary import COMPARED
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.oql import compile_filters, render_filters
from opik_mcp.read_list.paging import clamp_size
from opik_mcp.read_list.projection import normalise
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
    fields: list[str] | None = None,
    page: int | None = None,
    size: int | None = None,
    **_collection_args: Any,
) -> str:
    """The comparison, end to end: validate, resolve, ask, render."""
    ids = _validated_ids(experiment_ids)
    # Which names are valid is a fact about the page, so ``fields`` is checked
    # by the renderer once the rows are in hand — here it is only normalised.
    wanted = normalise(fields)
    _refuse_window(since, until)
    # A runner is handed ``None`` for a page argument the caller did not
    # choose (see ``list_tool._run_whole``), so the defaults are applied here.
    page = max(1, page or 1)
    size = clamp_size(size)

    experiments = await _resolve(client, ids)
    ran_dataset_id = dataset_of(experiments, dataset_id)

    clauses = compile_filters(COMPARED, filters) if filters else []
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
    filters_json = json.dumps(clauses, separators=(",", ":")) if clauses else None
    # Everything the page needs and nothing that depends on it goes out at
    # once: the rows, the output keys (first page only), the score
    # definitions, and one stats call per experiment. Only the rows can fail
    # the call; the rest decorate an answer and say so when they are missing.
    page_result, definitions_result, *rest = await asyncio.gather(
        client.list_compared_dataset_items(
            ran_dataset_id,
            experiment_ids=ids,
            filters=filters_json,
            sorting=sorting,
            search=search or None,
            page=page,
            size=size,
        ),
        client.list_feedback_definitions(size=DEFINITIONS_PAGE),
        *(
            client.get_compared_stats(ran_dataset_id, experiment_ids=[one], filters=filters_json)
            for one in ids
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
    stats_results, column_results = rest[: len(ids)], rest[len(ids) :]

    kinds = _kinds(definitions_result)
    figures: dict[str, Figures | BaseException] = {
        one: got if isinstance(got, BaseException) else Figures.of(got)
        for one, got in zip(ids, stats_results, strict=True)
    }
    label_counts, uncounted = await _label_counts(
        client, ran_dataset_id, experiments, figures, kinds, clauses
    )
    figure_lines = render_figures(
        experiments, figures, kinds, label_counts, counts_skipped=bool(uncounted)
    )

    unrestored: list[str] = []
    if stripping and rows:
        rows, unrestored = await _with_every_run(client, ran_dataset_id, ids, rows)

    applied = [f"compare: {legend(experiments)}"]
    if clauses:
        applied.append(f"filters: {render_filters(COMPARED, clauses)}")
    if sort_label is not None:
        applied.append(sort_label)
    if search:
        applied.append(f'search: "{search}"')
    if wanted is not None:
        # Echoed beside the filters for the same reason they are: the header
        # is where a caller checks what their arguments did to the page.
        applied.append(f"fields: {', '.join(wanted)}")
    header = f"[list: {_ENTITY} | {' | '.join(applied)}]"

    # Warnings first: whether the table can be read at face value is decided
    # before how to read it.
    notes = [*guards(experiments), how_to_read(experiments, assertion_columns=assertion_columns)]
    if figure_lines:
        notes.append(figures_note(experiments, filtered=bool(clauses), skipped=uncounted))
    if stripping and rows:
        notes.append(
            "A filter on the runs matches a case when any of its experiments matches; the "
            "experiments that did not match were fetched back onto the row, so what you see "
            "is the whole case."
        )
    keys_line = (
        keys_note(column_results[0] if column_results else None, rows, hide_echo=assertion_columns)
        if wanted is None
        # The projection marker already accounts for every field of the row,
        # and the keys note answers the same question one call earlier. Saying
        # it twice on a page the caller asked to be narrow is the one place a
        # note is worse than no note.
        else None
    )
    if keys_line is not None:
        notes.append(keys_line)
    if search:
        notes.append(
            "search matched the cases' data, not the runs' output; to search the output, "
            'filter on it (output contains "…").'
        )
    caveat = sort_caveat(sort_field)
    if caveat is not None:
        notes.append(caveat)
    if unrestored:
        notes.append(unrestored_note(unrestored))
    if not rows:
        # An empty page still says what could be asked next: the keys, the
        # search semantics and the legend are what turn it into a second call.
        reason = why_empty(
            strips_runs=stripping,
            is_filtered=bool(clauses),
            is_searched=bool(search),
            page=page,
            total=total,
        )
        return "\n".join([header, reason, *figure_lines, "", *notes])

    return render(
        rows,
        experiments,
        total=total,
        page=page,
        size=size,
        header=header,
        notes=notes,
        assertion_columns=assertion_columns,
        kinds=kinds,
        figures=figure_lines,
        fields=wanted,
    )


#: Feedback definitions read in one page. A workspace defines a handful; the
#: live one defines none. Past a hundred, the rest are read as plain numbers.
DEFINITIONS_PAGE: Final = 100


def _kinds(definitions: Any) -> ScoreKinds:
    """The score kinds, or none when the definitions could not be read —
    every score is then a number, which is what it was before OPIK-8394."""
    if not isinstance(definitions, dict):
        return NO_KINDS
    content = definitions.get("content")
    return ScoreKinds.of([d for d in content or [] if isinstance(d, dict)])


async def _label_counts(
    client: OpikReadClient,
    dataset_id: str,
    experiments: list[Experiment],
    figures: dict[str, Figures | BaseException],
    kinds: ScoreKinds,
    clauses: list[dict[str, str]],
) -> tuple[dict[tuple[str, str], dict[str, int]], list[str]]:
    """Count each categorical score's labels per experiment, in one wave.

    One stats call per (experiment, label), each the page's own filters plus
    a clause pinning the score to that label's number. Returns the counts
    keyed by (experiment id, score) and the score names left uncounted when
    the wave would exceed ``CATEGORY_CALL_CAP``; a count that fails is left
    out and the header shows the score as categorical without it.
    """
    wanted = label_counts_wanted(experiments, figures, kinds)
    if not wanted:
        return {}, []
    if len(wanted) > CATEGORY_CALL_CAP:
        return {}, sorted({name for _, name, _, _ in wanted})
    answers = await asyncio.gather(
        *(
            client.get_compared_stats(
                dataset_id,
                experiment_ids=[experiment.id],
                filters=json.dumps([*clauses, category_clause(name, value)], separators=(",", ":")),
            )
            for experiment, name, _, value in wanted
        ),
        return_exceptions=True,
    )
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for (experiment, name, label, _), answer in zip(wanted, answers, strict=True):
        if isinstance(answer, BaseException):
            continue
        runs = Figures.of(answer).runs
        if runs is None:
            continue
        counts.setdefault((experiment.id, name), {})[label] = runs
    return counts, []


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


def _validated_ids(experiment_ids: list[str] | None) -> list[str]:
    if experiment_ids is not None and not experiment_ids:
        # An empty array is still an argument, so it hands the call to this
        # runner. Telling that caller they need experiment_ids names the thing
        # they just passed; what they need is ids in it, or the argument gone.
        raise EntityArgValidationError(
            f"experiment_ids is empty. Name the runs to compare — "
            f"list('{_ENTITY}', experiment_ids=['<uuid>', '<uuid>']) — or drop the argument "
            f"to list the dataset's own cases: list('{_ENTITY}', dataset_id='<uuid>')."
        )
    if not experiment_ids:
        # Only reachable by calling the runner directly: the registry hands it
        # the call when ``experiment_ids`` is there at all.
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
    field, direction = compile_sort(COMPARED, sort)
    return (
        json.dumps([{"field": field, "direction": direction}], separators=(",", ":")),
        f"sort: {field} {direction.lower()}",
        field,
    )


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
