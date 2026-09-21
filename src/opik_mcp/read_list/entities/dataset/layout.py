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
from dataclasses import dataclass
from typing import Any

from opik_mcp.read_list.columns import one_line
from opik_mcp.read_list.entities.dataset.items import cell_limit, data_columns

#: Case columns in comparison mode. The plain listing shows up to eight; here
#: the caller came for the runs, and two keys are enough to recognise a case.
MAX_DATA_COLUMNS = 2
#: Score columns, ranked by how many rows carry them. The rest are named in a
#: note, so a cut score is never mistaken for a score nothing recorded.
MAX_SCORE_COLUMNS = 4
_MISSING = "-"
#: An experiment that ran the case and recorded nothing for the score. Not a
#: dash: a dash is "did not run", and the two were the same cell. A task or a
#: judge that raised looks exactly like this on the joined row, which carries
#: no error of its own; the trace does.
UNSCORED = "unscored"
#: Experiments are separated by a slash in a score cell and by a middle dot in
#: the pass cell, whose values already carry a slash: "1/1 / 0/2" is not
#: something anyone should have to parse.
RUN_SEPARATOR = " / "
PASS_SEPARATOR = "·"


@dataclass(frozen=True)
class Experiment:
    """One experiment in the comparison, in the order the caller named it."""

    id: str
    name: str
    label: str
    dataset_id: str
    dataset_name: str
    is_suite: bool
    # What decides whether lining these runs up means anything: the version
    # of the dataset each ran, whether it has finished, and how many cases it
    # covered. Read off the record the legend already needed, at no extra
    # call, and checked before the table is drawn — see ``compare._guards``.
    dataset_version_id: str | None = None
    dataset_version: str | None = None
    status: str | None = None
    trace_count: int | None = None


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
    for score in _entries(run):
        name, value = score.get("name"), score.get("value")
        if isinstance(name, str) and isinstance(value, int | float):
            out[name] = float(value)
    return out


def _entries(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in run.get("feedback_scores") or [] if isinstance(s, dict)]


def _entry(run: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((s for s in _entries(run) if s.get("name") == name), None)


@dataclass(frozen=True)
class ScoreKinds:
    """What the workspace's feedback definitions say a score *is*.

    A categorical score is stored as a number with a label beside it (``low``
    is ``0``), and the definition maps every label to its number. That is the
    one fact a row cannot always supply: a score written through the SDK with
    a bare value carries no label, and only the definition can restore it.
    The definition records no direction — nothing in Opik says whether a
    higher ``hallucination`` is better or worse — so there is nothing here
    about which way a delta points.
    """

    #: score name -> stored value -> label
    categorical: dict[str, dict[float, str]]

    @classmethod
    def of(cls, definitions: list[dict[str, Any]]) -> ScoreKinds:
        categorical: dict[str, dict[float, str]] = {}
        for definition in definitions:
            if definition.get("type") != "categorical":
                continue
            details = definition.get("details")
            categories = details.get("categories") if isinstance(details, dict) else None
            name = definition.get("name")
            if not isinstance(name, str) or not isinstance(categories, dict):
                continue
            categorical[name] = {
                float(value): str(label)
                for label, value in categories.items()
                if isinstance(value, int | float)
            }
        return cls(categorical=categorical)

    def label(self, name: str, value: float) -> str | None:
        return self.categorical.get(name, {}).get(value)


NO_KINDS = ScoreKinds(categorical={})


def is_categorical(runs: list[dict[str, Any]], name: str, kinds: ScoreKinds) -> bool:
    """Is this score a label rather than a number, by definition or by the rows?

    A run's entry carries ``category_name`` when the score was written with a
    label; that decides even with no definition in the workspace.
    """
    if name in kinds.categorical:
        return True
    return any((e := _entry(run, name)) is not None and e.get("category_name") for run in runs)


def category_of(run: dict[str, Any], name: str, kinds: ScoreKinds) -> str | None:
    """The label one run recorded for a categorical score, or the number when
    no label is known for it — a value the definition does not list is still
    what the run said."""
    entry = _entry(run, name)
    if entry is None:
        return None
    label = entry.get("category_name")
    if label:
        return str(label)
    value = entry.get("value")
    if isinstance(value, int | float):
        return kinds.label(name, float(value)) or number(float(value))
    return None


def authored(run: dict[str, Any], name: str) -> list[tuple[float, str]]:
    """Every author's value for one score on one run, with where it came from.

    The backend keeps one entry per score name and folds the authors into
    ``value_by_author``; a judge's ``sdk`` value and a reviewer's ``ui`` value
    for the same name are two opinions, not one number, and averaging them
    would hide the disagreement that is the point of recording both.
    """
    entry = _entry(run, name)
    if entry is None:
        return []
    by_author = entry.get("value_by_author")
    if not isinstance(by_author, dict) or len(by_author) < 2:
        return []
    out: list[tuple[float, str]] = []
    for opinion in by_author.values():
        if isinstance(opinion, dict) and isinstance(opinion.get("value"), int | float):
            out.append((float(opinion["value"]), str(opinion.get("source") or "?")))
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


def signed(value: float) -> str:
    """A difference with its sign, ``+0.5`` / ``-0.5``; zero is plain ``0``."""
    if round(value, 4) == 0:
        return "0"
    return f"{'+' if value > 0 else '-'}{number(abs(value))}"


@dataclass(frozen=True)
class ComparedRow:
    """One case, with its runs grouped and its worst run already found.

    Every cell of a row asks one of two questions — what did each experiment
    score, and which run should the caller open — and both were being
    re-derived per cell, the second one twice. The row answers them once.
    """

    case: dict[str, Any]
    experiments: list[Experiment]
    runs: dict[str, list[dict[str, Any]]]
    worst: tuple[Experiment, dict[str, Any]] | None
    kinds: ScoreKinds = NO_KINDS

    @classmethod
    def of(
        cls, case: dict[str, Any], experiments: list[Experiment], kinds: ScoreKinds = NO_KINDS
    ) -> ComparedRow:
        runs = runs_by_experiment(case)
        return cls(
            case=case,
            experiments=experiments,
            runs=runs,
            worst=_worst(runs, experiments),
            kinds=kinds,
        )

    @property
    def id(self) -> str:
        return str(self.case.get("id") or "")

    def data(self, key: str) -> str:
        value = (self.case.get("data") or {}).get(key)
        return "" if value is None else str(value)

    def score(self, name: str) -> str:
        """One score, every experiment's value, in the order the caller named them.

        Three kinds of cell, decided per score. A categorical score is its
        labels, one per run, and nothing is averaged or subtracted from a
        label. A score two authors wrote — a judge through the SDK and a
        reviewer in the UI — shows both opinions with their source, and no
        mean of the two. A number is the mean over the experiment's runs, and
        with exactly two experiments the cell carries E2 minus E1, signed: the
        sign is arithmetic, not a verdict, because no definition in Opik says
        which direction a metric improves in (see ``compare._how_to_read``).

        A dash is an experiment that did not run the case; ``unscored`` is one
        that ran it and recorded nothing for this score.
        """
        all_runs = [run for e in self.experiments for run in self.runs.get(e.id, [])]
        if is_categorical(all_runs, name, self.kinds):
            return RUN_SEPARATOR.join(self._labels(e, name) for e in self.experiments)
        if any(authored(run, name) for run in all_runs):
            return RUN_SEPARATOR.join(self._opinions(e, name) for e in self.experiments)
        values = [score_value(self.runs.get(e.id, []), name) for e in self.experiments]
        parts = []
        for e, v in zip(self.experiments, values, strict=True):
            if v is not None:
                parts.append(number(v))
            else:
                parts.append(UNSCORED if self.runs.get(e.id) else _MISSING)
        cell = RUN_SEPARATOR.join(parts)
        if len(values) == 2 and values[0] is not None and values[1] is not None:
            cell += f" Δ{signed(values[1] - values[0])}"
        return cell

    def _labels(self, experiment: Experiment, name: str) -> str:
        runs = self.runs.get(experiment.id)
        if not runs:
            return _MISSING
        labels = [label for run in runs if (label := category_of(run, name, self.kinds))]
        return ",".join(dict.fromkeys(labels)) if labels else UNSCORED

    def _opinions(self, experiment: Experiment, name: str) -> str:
        runs = self.runs.get(experiment.id)
        if not runs:
            return _MISSING
        parts: list[str] = []
        for run in runs:
            several = authored(run, name)
            if several:
                parts.extend(f"{number(v)} {source}" for v, source in several)
            elif (one := scores_of(run).get(name)) is not None:
                parts.append(number(one))
        return ", ".join(parts) if parts else UNSCORED

    def unscored(self) -> bool:
        """Did some experiment run this case and record no score at all?"""
        for experiment in self.experiments:
            runs = self.runs.get(experiment.id)
            if runs and not any(scores_of(run) for run in runs):
                return True
        return False

    def failed(self) -> bool:
        """Did some run fail an assertion?"""
        return any(run.get("status") == "failed" for own in self.runs.values() for run in own)

    def passed(self) -> str:
        """Passed runs out of total, per experiment, in the caller's order."""
        summaries = self.case.get("run_summaries_by_experiment")
        summaries = summaries if isinstance(summaries, dict) else {}
        parts = []
        for experiment in self.experiments:
            summary = summaries.get(experiment.id)
            if not isinstance(summary, dict):
                parts.append(_MISSING)
                continue
            parts.append(f"{summary.get('passed_runs', 0)}/{summary.get('total_runs', 0)}")
        return PASS_SEPARATOR.join(parts)

    def worst_trace(self) -> str:
        if self.worst is None:
            return _MISSING
        experiment, run = self.worst
        return f"{run.get('trace_id') or _MISSING} ({experiment.label})"

    def reason(self) -> str:
        """The first assertion the worst run failed, by name, with what the judge said.

        A suite checks several assertions on one case; a reason without the
        assertion it belongs to says that the case fell but not on what.
        """
        if self.worst is None:
            return _MISSING
        for assertion in self.worst[1].get("assertion_results") or []:
            if isinstance(assertion, dict) and assertion.get("passed") is False:
                name = str(assertion.get("value") or "").strip()
                reason = str(assertion.get("reason") or "").strip()
                if name and reason:
                    return f"{name}: {reason}"
                return name or reason
        return ""


def _worst(
    runs: dict[str, list[dict[str, Any]]], experiments: list[Experiment]
) -> tuple[Experiment, dict[str, Any]] | None:
    """The run a caller should open first, and whose experiment it was.

    The worst run is the single experiment item with the lowest sum of the
    scores it recorded — not the experiment with the lowest average, which an
    experiment that ran the case three times can win while every one of its
    runs passed. Its experiment owns the row's trace. A run that recorded
    nothing sums to zero, which makes it the one to open — the likeliest
    place for the error the joined row cannot carry. A categorical score
    enters the sum as its stored number, and the definition orders those
    (``low`` is 0, ``high`` is 2), so the lower label still loses.

    Within that experiment it is the first run that *failed*, because one that
    ran the case three times and failed once has two traces that show nothing;
    with no failed run it is the lowest-scoring one, which is the same item
    that picked the experiment.

    Ties go to the last experiment named, which is the newer run in the
    comparison a caller actually makes: the baseline's trace is the one they
    already know.

    A suite judged by assertions alone records no scores, so every total is
    zero and the tie rule would crown the last experiment on every row — a
    ranking nobody made. There the failed run is the only signal: the last
    experiment that has one owns the trace, and with none, there is nothing
    worth opening.
    """
    if not any(scores_of(run) for own in runs.values() for run in own):
        failed_by: tuple[Experiment, dict[str, Any]] | None = None
        for experiment in experiments:
            for run in runs.get(experiment.id, []):
                if run.get("status") == "failed":
                    failed_by = (experiment, run)
                    break
        return failed_by

    worst: tuple[float, Experiment, list[dict[str, Any]]] | None = None
    for experiment in experiments:
        own = runs.get(experiment.id, [])
        if not own:
            continue
        lowest = min(_total(run) for run in own)
        if worst is None or lowest <= worst[0]:
            worst = (lowest, experiment, own)
    if worst is None:
        return None
    _, experiment, own = worst
    failed = next((run for run in own if run.get("status") == "failed"), None)
    return experiment, failed or min(own, key=_total)


def _total(run: dict[str, Any]) -> float:
    """One experiment item's standing: the sum of the scores it recorded."""
    return sum(scores_of(run).values())


def render(
    rows: list[dict[str, Any]],
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
) -> str:
    """The page, as the agent reads it.

    Follows the shared list table: a count line, the per-experiment figures,
    the rows, then everything the table did to the data said under the data.
    The same layout serves plain ``evaluate()`` experiments and test-suite
    runs; only the two assertion columns depend on which it is.
    """
    data_keys, omitted_keys = data_columns(rows, MAX_DATA_COLUMNS)
    scores, omitted_scores = score_columns(rows)
    columns = ["id", *(f"data.{key}" for key in data_keys), *scores]
    # Every comparison names the run worth opening next. ``passed`` and
    # ``reason`` come from assertions, which only a test suite records:
    # opik-backend's ``run_passed`` subquery (ExperimentDAO) inner-joins on
    # ``evaluation_method = 'evaluation_suite'``, so for a plain dataset the
    # backend cannot fill them and the columns would be dashes read as loss.
    columns += ["passed", "worst_trace", "reason"] if assertion_columns else ["worst_trace"]
    limit = cell_limit(len(rows), len(columns))

    lines = [
        header,
        f"Found {total} dataset_items (page {page}, showing {len(rows)} of {total}):",
        *(figures or []),
        "",
        " | ".join(columns),
    ]
    cut = 0
    compared: list[ComparedRow] = []
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
        lines.append(" | ".join(values))

    under = [*notes]
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
            f"a {_MISSING} there means no run, not a zero score."
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
    if under:
        lines += ["", *under]
    if page * size < total:
        lines += ["", f"Use page={page + 1} for next {size} results."]
    return "\n".join(lines)


def _tally(rows: list[ComparedRow], *, scored: bool, assertions: bool) -> str | None:
    """How many cases on the page failed, and how many ran without a score.

    Two different things that read alike in a table: a case a judge marked
    down, and a case where the task or the judge raised and nothing was
    scored. The joined row carries no error — the trace does — so the second
    is counted as what it is here, a run with no score, and the caller is
    told where the error would be. Kept apart from the low scores: an errored
    case in a "how many scored under 0.5" count is a wrong count.
    """
    parts: list[str] = []
    if assertions:
        failed = sum(1 for row in rows if row.failed())
        if failed:
            parts.append(f"{_cases(failed)} failed an assertion")
    if scored:
        unscored = sum(1 for row in rows if row.unscored())
        if unscored:
            parts.append(
                f"{_cases(unscored)} {'has' if unscored == 1 else 'have'} a run with no score at "
                "all (unscored): what a task or judge that raised looks like here, apart from the "
                "low scores — open its worst_trace for error_info"
            )
    if not parts:
        return None
    return f"On this page, {'; '.join(parts)}."


def _cases(n: int) -> str:
    return f"{n} case{'s' if n != 1 else ''}"


def _not_run_by_every(case: dict[str, Any], experiments: list[Experiment]) -> bool:
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
