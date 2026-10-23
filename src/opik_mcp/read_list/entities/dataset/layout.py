"""One row per case, one column per score: the comparison's table.

The shape is the decision here. The obvious layout gives every experiment its
own column for every score, which on a realistic dataset (three scores, two
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
from collections.abc import Sequence

from opik_mcp.client.shapes import DatasetItem, ExperimentItem
from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.entities.dataset.compared_row import (
    MISSING,
    NO_KINDS,
    ComparedRow,
    Experiment,
    ScoreKinds,
    runs_by_experiment,
    scores_of,
)
from opik_mcp.read_list.entities.dataset.items import cell_limit, data_columns
from opik_mcp.read_list.projection import check as check_fields
from opik_mcp.read_list.projection import fields_line, marker

#: Case columns in comparison mode. The plain listing shows up to eight; here
#: the caller came for the runs, and two keys are enough to recognise a case.
MAX_DATA_COLUMNS = 2
#: Score columns, ranked by how many rows carry them. The rest are named in a
#: note, so a cut score is never mistaken for a score nothing recorded.
MAX_SCORE_COLUMNS = 4
#: The prefix a projected caller names a run's score by. The column heading is
#: the bare score name, because the cell holds one value per experiment and
#: there is no other kind of score on the row — but ``feedback_scores.<name>``
#: is what the field is called everywhere else in these tools (in ``filters``,
#: in ``sort``, on a plain list row), and an argument that took a different
#: spelling per entity would be an argument the agent has to look up.
SCORE_PREFIX = "feedback_scores."
#: Columns of a comparison row that are neither a case key nor a score.
_ROW_COLUMNS = ("id", "worst_trace")
_ASSERTION_COLUMNS = ("passed", "reason")
#: The handle a projected comparison row keeps whichever fields were named:
#: the case's worst run is the trace the next call opens, and a row of scores
#: with no trace behind it is a number nobody can go and check.
_HANDLE = "worst_trace"
#: "every one of them" as a limit, for the two rankings that are asked for
#: the whole list rather than for the few that fit a table.
_NO_LIMIT = 10**6

#: What a score column is marked with when its cell carries a Δ. Opik
#: records no direction for a score, so the sign is arithmetic and the header
#: says so where the Δ is read — see :func:`_header`.
DIRECTION_UNKNOWN = "direction unknown"


def score_columns(
    rows: Sequence[DatasetItem], limit: int = MAX_SCORE_COLUMNS
) -> tuple[list[str], list[str]]:
    """The score names on the page, most filled first: (shown, omitted).

    ``limit`` is the table's width; a projected page passes the page's own
    length instead, because there the question is not which scores fit but
    which scores exist to be named.
    """
    filled: Counter[str] = Counter()
    for row in rows:
        names = {name for run in _all_runs(row) for name in scores_of(run)}
        for name in names:
            filled[name] += 1
    ranked = sorted(filled, key=lambda name: (-filled[name], name))
    return ranked[:limit], ranked[limit:]


def _all_runs(row: DatasetItem) -> list[ExperimentItem]:
    return [item for item in row.get("experiment_items") or [] if isinstance(item, dict)]


def compare_fields(
    rows: Sequence[DatasetItem], *, assertions: bool
) -> tuple[tuple[str, ...], list[str]]:
    """What a comparison page's rows carry: ``(names, score names)``.

    Spelled the way the rest of the tools spell them — ``data.<key>`` for the
    case, ``feedback_scores.<name>`` for a run's score — so the caller who
    filtered on a field can project on the same name.
    """
    keys, _ = data_columns(rows, _NO_LIMIT)
    scores, _ = score_columns(rows, _NO_LIMIT)
    names = [
        *_ROW_COLUMNS,
        *(f"data.{key}" for key in keys),
        *(f"{SCORE_PREFIX}{name}" for name in scores),
        *(_ASSERTION_COLUMNS if assertions else ()),
    ]
    return tuple(sorted(names)), scores


def _column_for(field: str) -> str:
    """The table column a field name asks for.

    ``feedback_scores.helpfulness`` is the ``helpfulness`` column — one column
    per score with the experiments sharing its cell, which is the whole shape
    of this table and not something a projection should reorganise. Everything
    else is its own name.
    """
    return field.removeprefix(SCORE_PREFIX) if field.startswith(SCORE_PREFIX) else field


def _named_columns(fields: tuple[str, ...], scores: list[str]) -> tuple[list[str], list[str]]:
    """The caller's fields as table columns, in the order they named them,
    with the handle that opens the case on the end whatever they named."""
    columns = ["id"]
    for field in fields:
        column = _column_for(field)
        if column not in columns:
            columns.append(column)
    if _HANDLE not in columns:
        columns.append(_HANDLE)
    return columns, [c for c in columns if c in scores]


def render(
    rows: Sequence[DatasetItem],
    experiments: list[Experiment],
    *,
    total: int,
    page: int,
    size: int,
    header: str,
    notes: list[str],
    assertion_columns: bool = False,
    kinds: ScoreKinds = NO_KINDS,
    figures: list[str] | None = None,
    fields: tuple[str, ...] | None = None,
) -> str:
    """The page, as the agent reads it.

    Follows the shared list table: a count line, the per-experiment figures,
    the rows, then everything the table did to the data said under the data.
    The same layout serves plain ``evaluate()`` experiments and test-suite
    runs; only the two assertion columns depend on which it is.

    ``fields`` is the caller naming the columns instead. It is where the
    comparison earns the most: the widths this table spends its budget
    rationing — two case keys of eight, four scores of however many, a reason
    long enough to read — are all rationing against each other, and a caller
    who wants one question and one score does not need any of it.
    """
    available, all_scores = compare_fields(rows, assertions=assertion_columns)
    omitted_keys: list[str] = []
    omitted_scores: list[str] = []
    if fields is not None:
        check_fields(fields, available, whole="list('dataset_item', experiment_ids=[…])")
        columns, scores = _named_columns(fields, all_scores)
        # Uncut, like every projection: the caller named the column to read it.
        limit = _NO_LIMIT
    else:
        data_keys, omitted_keys = data_columns(rows, MAX_DATA_COLUMNS)
        scores, omitted_scores = score_columns(rows)
        columns = ["id", *(f"data.{key}" for key in data_keys), *scores]
        # Every comparison names the run worth opening next. ``passed`` and
        # ``reason`` come from assertions, which only a test suite records:
        # opik-backend's ``run_passed`` subquery (ExperimentDAO) inner-joins on
        # ``evaluation_method = 'evaluation_suite'``, so for a plain dataset the
        # backend cannot fill them and the columns would be dashes read as loss.
        columns += ["passed", "worst_trace", "reason"] if assertion_columns else ["worst_trace"]
        limit = cell_limit(rows=len(rows), columns=len(columns))

    cut = 0
    compared: list[ComparedRow] = []
    body: list[str] = []
    for case in rows:
        row = ComparedRow.of(case, experiments, kinds)
        compared.append(row)
        values = []
        for column in columns:
            text = one_line(_cell(row, column, scores))
            if len(text) > limit:
                text = text[: limit - 3] + "..."
                cut += 1
            values.append(text)
        body.append(" | ".join(values))

    # The header is written after the rows because one of its columns depends
    # on them: only a rendered cell knows whether it carried a Δ.
    lines = [
        header,
        f"Found {total} dataset_items (page {page}, showing {len(rows)} of {total}):",
        *(figures or []),
        "",
        " | ".join(_header(columns, scores, compared)),
        *body,
    ]

    under = [*notes]
    if fields is not None:
        under.insert(
            0,
            marker(
                kept=columns,
                omitted=tuple(n for n in available if _column_for(n) not in columns),
                whole="Drop fields= for the table's own columns.",
            ),
        )
    if not scores:
        under.append(
            "These runs recorded no feedback scores; they are judged by assertions, and only "
            "a failed assertion's reason is shown."
            if assertion_columns
            else "These runs recorded no feedback scores, so there is nothing numeric to compare."
        )
    tally = _tally(compared, scored=bool(scores), assertions=assertion_columns)
    if tally:
        under.append(tally)
    partial = sum(1 for case in rows if _not_run_by_every(case, experiments))
    if partial:
        under.append(
            f"{partial} of {len(rows)} case{'s' if len(rows) != 1 else ''} "
            f"{'was' if partial == 1 else 'were'} not run by every experiment; "
            f"a {MISSING} there means no run, not a zero score."
        )
    if omitted_keys:
        under.append(
            f"Case columns are the dataset's data keys, showing {len(data_keys)} of "
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
    if fields is None and (offer := fields_line(available)) is not None:
        # A comparison is a list page like any other, and it names the fields
        # its rows carry for the same reason: the alternative is the caller
        # guessing whether the score is ``helpfulness`` or
        # ``feedback_scores.helpfulness``, and getting a refusal either way.
        under.append(offer)
    if under:
        lines += ["", *under]
    if page * size < total:
        lines += ["", f"Use page={page + 1} for next {size} results."]
    return "\n".join(lines)


def _header(columns: list[str], scores: list[str], rows: list[ComparedRow]) -> list[str]:
    """The column names, with every Δ-bearing score marked ``direction unknown``.

    Opik records what a score *is* and never which way it improves: a
    feedback definition carries a name, a description, a type and its
    details — min and max for a numerical one, the labels and their numbers
    for a categorical one — and nothing else (``FeedbackDefinition.java``;
    the one ``higher is better`` in the product is a per-widget choice on a
    dashboard's leaderboard, not a property of the score). The name is not
    evidence either: a workspace's ``hallucination`` may be scored so that 1
    is clean.

    So the Δ stays arithmetic, E2 minus E1, and the column that carries it
    says the direction is unknown where the Δ is read. The note under the
    table says what to do about it; a reader who never reaches the note still
    sees the marker. A column with no Δ — one experiment, a label, two
    authors — is unmarked, because there is no sign there to misread.

    The names are escaped like any cell: a case column is a data key the user
    chose, and one carrying a line break split this line in two.
    """
    marked = {name for name in scores if any(row.compares(name) for row in rows)}
    return [
        one_line(f"{column} ({DIRECTION_UNKNOWN})" if column in marked else column)
        for column in columns
    ]


def _tally(rows: list[ComparedRow], *, scored: bool, assertions: bool) -> str | None:
    """How many cases on the page failed, and how the rest are to be read.

    Three things read alike in a table and are not alike at all: a case every
    experiment scored, a case where one run produced nothing while another
    scored it (the task or the judge raised), and a case nobody scored. An
    errored case counted in with the low scores makes "how many scored under
    0.5" a wrong number, and counted in with the unscored it stops pointing
    at a trace worth opening.

    So the page's cases are partitioned, not merely flagged: fully scored,
    errored and unscored are exclusive and exhaustive, and the counts sum to
    the rows on the page — which is what lets a caller trust that no case was
    quietly counted twice or not at all. Silent when there is nothing to
    separate: a page where every run scored says so by saying nothing.

    Failed assertions are a different axis and keep their own count; a case
    can be fully scored and have failed one. "Fully scored" rather than
    "compared" because every case on the page was compared — that is what the
    page is — and the count is of the cases every experiment that ran them
    also scored.
    """
    parts: list[str] = []
    if assertions:
        failed = sum(1 for row in rows if row.failed())
        if failed:
            parts.append(f"{_cases(failed)} failed an assertion")
    if scored:
        errored = sum(1 for row in rows if row.errored())
        unscored = sum(1 for row in rows if row.unscored())
        if errored or unscored:
            # A zero keeps its number, because the three counts have to add
            # up to the page, and loses its explanation, because there is
            # nothing there to explain.
            parts.append(
                f"{len(rows) - errored - unscored} of {_cases(len(rows))} fully scored, "
                f"{errored} errored{_WHY_ERRORED if errored else ''} and "
                f"{unscored} unscored{_WHY_UNSCORED if unscored else ''}"
            )
    if not parts:
        return None
    return f"On this page, {'; '.join(parts)}."


#: Why an errored case is not a low score, and where its error is. Said
#: beside the count, which is the number a caller checks before believing a
#: page of scores.
_WHY_ERRORED = (
    " (a run recorded nothing while another experiment scored the same case — what a task or "
    "judge that raised looks like here; open its worst_trace for error_info)"
)
_WHY_UNSCORED = " (no run on the case recorded a score)"


def _cases(n: int) -> str:
    return f"{n} case{'s' if n != 1 else ''}"


def _not_run_by_every(case: DatasetItem, experiments: list[Experiment]) -> bool:
    """Did some compared experiment leave this case untouched?

    Its cells show ``-`` exactly like a run that scored nothing, so the page
    counts these rows and says which reading applies.
    """
    ran = runs_by_experiment(case)
    return any(experiment.id not in ran for experiment in experiments)


def _cell(row: ComparedRow, column: str, scores: list[str]) -> str:
    if column == "id":
        return row.id
    if column.startswith("data."):
        return row.data(column.removeprefix("data."))
    if column in scores:
        return row.score(column)
    if column == "passed":
        return row.passed()
    if column == "worst_trace":
        return row.worst_trace()
    return row.reason()
