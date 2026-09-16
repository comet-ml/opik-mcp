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
from opik_mcp.read_list.sorting import compile_sort

#: What opik-backend stores on an experiment that ran a test suite. Not
#: ``test_suite``: its OPIK-5795 plans that rename and has not done it, so
#: this is the one place that knows the stored spelling.
TEST_SUITE_METHOD = "evaluation_suite"

MAX_EXPERIMENTS = 10
#: The list tool's own page defaults, which a runner is handed as ``None``
#: when the caller did not choose them (see ``list_tool._run_whole``).
DEFAULT_SIZE = 25
MAX_SIZE = 100

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
    page = max(1, page or 1)
    size = max(1, min(size or DEFAULT_SIZE, MAX_SIZE))

    experiments = await _resolve(client, ids)
    suite_id = _suite_of(experiments, test_suite_id)

    clauses = compile_filters(_ENTITY, filters) if filters else []
    sorting, sort_label = _sorting(sort)

    body = await client.list_compared_test_suite_items(
        suite_id,
        experiment_ids=ids,
        filters=json.dumps(clauses, separators=(",", ":")) if clauses else None,
        sorting=sorting,
        search=search or None,
        page=page,
        size=size,
    )
    rows = [row for row in body.get("content") or [] if isinstance(row, dict)]
    total_raw = body.get("total")
    total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else len(rows)

    applied = [f"compare: {len(ids)} experiments"]
    if clauses:
        applied.append(f"filters: {render_filters(_ENTITY, clauses)}")
    if sort_label is not None:
        applied.append(sort_label)
    if search:
        applied.append(f'search: "{search}"')
    header = f"[list: {_ENTITY} | {' | '.join(applied)}]"

    suite_columns = any(experiment.is_suite for experiment in experiments)
    notes = [_legend(experiments, suite_columns=suite_columns)]
    if not rows:
        return f"{header}\n{_empty(bool(clauses), bool(search))}\n\n{notes[0]}"

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


def _legend(experiments: list[Experiment], *, suite_columns: bool) -> str:
    """Which experiment each value in a cell belongs to."""
    named = "; ".join(f"{e.label} = {e.name} ({e.id})" for e in experiments)
    passed = (
        f" passed is passed/total runs, {PASS_SEPARATOR.join(e.label for e in experiments)}."
        if suite_columns
        else ""
    )
    if len(experiments) == 1:
        return f"{named}. Score cells carry {experiments[0].label}'s value.{passed}"
    order = RUN_SEPARATOR.join(e.label for e in experiments)
    baseline = f"{experiments[0].label} is the baseline"
    gap = ", and Δ is the unsigned gap between them" if len(experiments) == 2 else ""
    return f"{named}. {baseline}; score cells read {order} in that order{gap}.{passed}"


def _empty(filtered: bool, searched: bool) -> str:
    if filtered:
        return (
            "No case matched. A filter on the runs matches a case when any of its "
            "experiments matches, so nothing here scored or ran the way you asked."
        )
    if searched:
        return "No case matched the search. Search matches the case data, not the runs' output."
    return "No cases found: these experiments have no items in common."
