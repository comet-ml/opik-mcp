"""One row per case, one column per score: the comparison's table.

The shape is the decision here. The obvious layout gives every experiment its
own column for every score, which on a realistic suite (three scores, two
experiments, a handful of case keys) is eighteen columns inside a page budget
of eight thousand characters — about seventeen characters a cell, which is not
a table, it is a smear. So a score keeps one column and the experiments share
its cell, in the order the caller named them, and the legend under the table
says which is which. That reads the same with two experiments and with ten,
and it leaves the width for the case data and the judge's reason, which are
the parts a caller cannot reconstruct from an average.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from opik_mcp.read_list.entities.test_suite.items import cell_limit, data_columns

#: Case columns in comparison mode. The plain listing shows up to eight; here
#: the caller came for the runs, and two keys are enough to recognise a case.
MAX_DATA_COLUMNS = 2
#: Score columns, ranked by how many rows carry them. The rest are named in a
#: note, so a cut score is never mistaken for a score nothing recorded.
MAX_SCORE_COLUMNS = 4
_MISSING = "-"


@dataclass(frozen=True)
class Experiment:
    """One experiment in the comparison, in the order the caller named it."""

    id: str
    name: str
    label: str
    dataset_id: str
    dataset_name: str
    is_suite: bool


def runs_by_experiment(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The row's runs, grouped by the experiment that produced them.

    An experiment appears more than once when its execution policy ran the
    case more than once; it is missing entirely when a run-level filter
    matched one of the others and the backend stripped it off the row.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in row.get("experiment_items") or []:
        if isinstance(item, dict) and item.get("experiment_id"):
            grouped.setdefault(str(item["experiment_id"]), []).append(item)
    return grouped


def scores_of(run: dict[str, Any]) -> dict[str, float]:
    """One run's scores as a map, from the backend's list of named entries."""
    out: dict[str, float] = {}
    for score in run.get("feedback_scores") or []:
        if not isinstance(score, dict):
            continue
        name, value = score.get("name"), score.get("value")
        if isinstance(name, str) and isinstance(value, int | float):
            out[name] = float(value)
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score_value(runs: list[dict[str, Any]], name: str) -> float | None:
    """What one experiment scored on one case: the mean over its runs.

    With the usual single run this is that run's score. With an execution
    policy of several runs it is their average, which is the honest summary of
    a case that passed twice and failed once; which run to open is a different
    question, answered by the worst-run trace on the same row.
    """
    return _mean([s[name] for run in runs if (s := scores_of(run)) and name in s])


def score_columns(rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """The score names on the page, most filled first: (shown, omitted)."""
    filled: Counter[str] = Counter()
    for row in rows:
        names = {name for run in _all_runs(row) for name in scores_of(run)}
        for name in names:
            filled[name] += 1
    ranked = sorted(filled, key=lambda name: (-filled[name], name))
    return ranked[:MAX_SCORE_COLUMNS], ranked[MAX_SCORE_COLUMNS:]


def _all_runs(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in row.get("experiment_items") or [] if isinstance(item, dict)]


def number(value: float) -> str:
    """A score as the caller reads it: 0.9, 1, 0.65 — never 0.30000000000004."""
    return f"{round(value, 4):g}"


def score_cell(row: dict[str, Any], name: str, experiments: list[Experiment]) -> str:
    """One score, every experiment's value, in the order the caller named them.

    With exactly two experiments the cell carries the gap between them. It is
    unsigned here: which direction counts as better depends on the metric, and
    saying so is OPIK-8394's job, not this one's.
    """
    grouped = runs_by_experiment(row)
    values = [score_value(grouped.get(experiment.id, []), name) for experiment in experiments]
    cell = " / ".join(_MISSING if value is None else number(value) for value in values)
    if len(values) == 2 and values[0] is not None and values[1] is not None:
        cell += f" Δ{number(abs(values[0] - values[1]))}"
    return cell


def worst_run(
    row: dict[str, Any], experiments: list[Experiment]
) -> tuple[Experiment, dict[str, Any]] | None:
    """The run a caller should open first, and whose experiment it was.

    The worst experiment is the one with the lowest total across the scores it
    recorded; within it, the first run that failed, because an experiment that
    ran the case three times and failed once has two traces that show nothing.
    """
    worst: tuple[float, Experiment, dict[str, Any]] | None = None
    grouped = runs_by_experiment(row)
    for experiment in experiments:
        runs = grouped.get(experiment.id, [])
        if not runs:
            continue
        total = sum(sum(scores_of(run).values()) for run in runs) / len(runs)
        if worst is None or total < worst[0]:
            failed = [run for run in runs if run.get("status") == "failed"]
            worst = (total, experiment, (failed or runs)[0])
    if worst is None:
        return None
    return worst[1], worst[2]


def render(
    rows: list[dict[str, Any]],
    experiments: list[Experiment],
    *,
    total: int,
    page: int,
    size: int,
    header: str,
    notes: list[str],
    suite_columns: bool = False,
) -> str:
    """The page, as the agent reads it.

    Follows the shared list table: a count line, the rows, then everything the
    table did to the data said under the data.
    """
    data_keys, omitted_keys = data_columns(rows, MAX_DATA_COLUMNS)
    scores, omitted_scores = score_columns(rows)
    columns = ["id", *(f"data.{key}" for key in data_keys), *scores]
    if suite_columns:
        columns += ["passed", "worst_trace", "reason"]
    limit = cell_limit(len(rows), len(columns))

    lines = [
        header,
        f"Found {total} test_suite_items (page {page}, showing {len(rows)} of {total}):",
        "",
        " | ".join(columns),
    ]
    cut = 0
    for row in rows:
        values = []
        for column in columns:
            text = _cell(row, column, experiments, scores)
            if len(text) > limit:
                text = text[: limit - 3] + "..."
                cut += 1
            values.append(text)
        lines.append(" | ".join(values))

    under = [*notes]
    if omitted_keys:
        under.append(
            f"Case columns are the suite's data keys, showing {len(data_keys)} of "
            f"{len(data_keys) + len(omitted_keys)} (the runs take the width); "
            f"omitted: {', '.join(omitted_keys)}."
        )
    if omitted_scores:
        under.append(
            f"Showing {len(scores)} of {len(scores) + len(omitted_scores)} scores by fill rate; "
            f"omitted: {', '.join(omitted_scores)}."
        )
    if cut:
        under.append(
            f"{cut} value{'s' if cut != 1 else ''} cut at {limit} chars; "
            "fewer rows per page (size=…) raise the cap."
        )
    if under:
        lines += ["", *under]
    if page * size < total:
        lines += ["", f"Use page={page + 1} for next {size} results."]
    return "\n".join(lines)


def _cell(
    row: dict[str, Any],
    column: str,
    experiments: list[Experiment],
    scores: list[str],
) -> str:
    if column == "id":
        return str(row.get("id") or "")
    if column.startswith("data."):
        value = (row.get("data") or {}).get(column.removeprefix("data."))
        return "" if value is None else str(value)
    if column in scores:
        return score_cell(row, column, experiments)
    return _suite_cell(row, column, experiments)


def _suite_cell(row: dict[str, Any], column: str, experiments: list[Experiment]) -> str:
    if column == "passed":
        return passed_cell(row, experiments)
    worst = worst_run(row, experiments)
    if worst is None:
        return _MISSING
    experiment, run = worst
    if column == "worst_trace":
        return f"{run.get('trace_id') or _MISSING} ({experiment.label})"
    return failure_reason(run)


def passed_cell(row: dict[str, Any], experiments: list[Experiment]) -> str:
    """Passed runs out of total, per experiment, in the caller's order."""
    summaries = row.get("run_summaries_by_experiment")
    summaries = summaries if isinstance(summaries, dict) else {}
    parts = []
    for experiment in experiments:
        summary = summaries.get(experiment.id)
        if not isinstance(summary, dict):
            parts.append(_MISSING)
            continue
        parts.append(f"{summary.get('passed_runs', 0)}/{summary.get('total_runs', 0)}")
    return " / ".join(parts)


def failure_reason(run: dict[str, Any]) -> str:
    """What the judge said about the first assertion this run failed."""
    for assertion in run.get("assertion_results") or []:
        if isinstance(assertion, dict) and assertion.get("passed") is False:
            return str(assertion.get("reason") or "")
    return ""
