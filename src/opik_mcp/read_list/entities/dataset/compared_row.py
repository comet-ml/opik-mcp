"""One case of the comparison: its runs, grouped by experiment, and what
each scored.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from opik_mcp.client.shapes import DatasetItem, ExperimentItem, FeedbackDefinition, FeedbackScore

#: An experiment that did not run the case.
MISSING = "-"
#: An experiment that ran the case and recorded nothing for the score. Not a
#: dash: a dash is "did not run", and the two were the same cell.
UNSCORED = "unscored"
#: An experiment that ran the case and recorded nothing at all, while another
#: scored it — a task or a judge that raised. The joined row carries no error
#: of its own (the trace does), so that asymmetry is the whole fingerprint,
#: and it is worth its own word: reading it as ``unscored`` put a crash in
#: with the cases nobody judged, and reading it as a number would put it in
#: with the low scores. See :meth:`ComparedRow.errored`.
ERRORED = "errored"
#: Experiments are separated by a slash in a score cell and by a middle dot in
#: the pass cell, whose values already carry a slash: "1/1 / 0/2" is not
#: something anyone should have to parse.
RUN_SEPARATOR = " / "
PASS_SEPARATOR = "·"

#: How a score renders, decided once per row and per score: a label, several
#: authors' opinions, or a number. Only a number is subtracted.
_LABELLED, _OPINIONS, _NUMBER = "labelled", "opinions", "number"


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
    # call, and checked before the table is drawn — see ``compare_guards.guards``.
    dataset_version_id: str | None = None
    dataset_version: str | None = None
    status: str | None = None
    trace_count: int | None = None


def runs_by_experiment(row: DatasetItem) -> dict[str, list[ExperimentItem]]:
    """The row's runs, grouped by the experiment that produced them.

    An experiment appears more than once when its execution policy ran the
    case more than once; it is missing entirely when a run-level filter
    matched one of the others and the backend stripped it off the row.
    """
    grouped: dict[str, list[ExperimentItem]] = {}
    for item in row.get("experiment_items") or []:
        if isinstance(item, dict) and item.get("experiment_id"):
            grouped.setdefault(str(item["experiment_id"]), []).append(item)
    return grouped


def scores_of(run: ExperimentItem) -> dict[str, float]:
    """One run's scores as a map, from the backend's list of named entries."""
    out: dict[str, float] = {}
    for score in _entries(run):
        name, value = score.get("name"), score.get("value")
        if isinstance(name, str) and isinstance(value, int | float):
            out[name] = float(value)
    return out


def _entries(run: ExperimentItem) -> list[FeedbackScore]:
    return [s for s in run.get("feedback_scores") or [] if isinstance(s, dict)]


def _entry(run: ExperimentItem, name: str) -> FeedbackScore | None:
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
    about which way a delta points, and the header says as much
    (``layout._header``).
    """

    #: score name -> stored value -> label
    categorical: dict[str, dict[float, str]]

    @classmethod
    def of(cls, definitions: Sequence[FeedbackDefinition]) -> ScoreKinds:
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


def is_categorical(runs: Sequence[ExperimentItem], name: str, kinds: ScoreKinds) -> bool:
    """Is this score a label rather than a number, by definition or by the rows?

    A run's entry carries ``category_name`` when the score was written with a
    label; that decides even with no definition in the workspace.
    """
    if name in kinds.categorical:
        return True
    return any((e := _entry(run, name)) is not None and e.get("category_name") for run in runs)


def category_of(run: ExperimentItem, name: str, kinds: ScoreKinds) -> str | None:
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


def authored(run: ExperimentItem, name: str) -> list[tuple[float, str]]:
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
    return [
        (float(value), str(opinion.get("source") or "?"))
        for opinion in by_author.values()
        if isinstance(opinion, dict) and isinstance(value := opinion.get("value"), int | float)
    ]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score_value(runs: Sequence[ExperimentItem], name: str) -> float | None:
    """What one experiment scored on one case: the mean over its runs.

    With the usual single run this is that run's score. With an execution
    policy of several runs it is their average, which is the honest summary of
    a case that passed twice and failed once; which run to open is a different
    question, answered by the worst-run trace on the same row.
    """
    return _mean([s[name] for run in runs if (s := scores_of(run)) and name in s])


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

    case: DatasetItem
    experiments: list[Experiment]
    runs: dict[str, list[ExperimentItem]]
    worst: tuple[Experiment, ExperimentItem] | None
    kinds: ScoreKinds = NO_KINDS

    @classmethod
    def of(
        cls, case: DatasetItem, experiments: list[Experiment], kinds: ScoreKinds = NO_KINDS
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

    def _kind(self, name: str) -> str:
        """Which of the three cells this score gets on this row.

        The cell and the column header have to agree about it — a header that
        marks a Δ the cell does not carry is worse than no marker — so the
        branch is taken once, here, and both read it.
        """
        all_runs = [run for e in self.experiments for run in self.runs.get(e.id, [])]
        if is_categorical(all_runs, name, self.kinds):
            return _LABELLED
        if any(authored(run, name) for run in all_runs):
            return _OPINIONS
        return _NUMBER

    def _values(self, name: str) -> list[float | None]:
        return [score_value(self.runs.get(e.id, []), name) for e in self.experiments]

    def compares(self, name: str) -> bool:
        """Does this row's cell for ``name`` carry a Δ?

        Which is what the header's ``direction unknown`` marker warns about,
        so the header asks the rows rather than guessing from the page.
        """
        if len(self.experiments) != 2 or self._kind(name) != _NUMBER:
            return False
        return all(value is not None for value in self._values(name))

    def score(self, name: str) -> str:
        """One score, every experiment's value, in the order the caller named them.

        Three kinds of cell, decided per score. A categorical score is its
        labels, one per run, and nothing is averaged or subtracted from a
        label. A score two authors wrote — a judge through the SDK and a
        reviewer in the UI — shows both opinions with their source, and no
        mean of the two. A number is the mean over the experiment's runs, and
        with exactly two experiments the cell carries E2 minus E1, signed: the
        sign is arithmetic, not a verdict, because no definition in Opik says
        which direction a metric improves in (see ``compare_notes.how_to_read``).

        A dash is an experiment that did not run the case; ``unscored`` is one
        that ran it and recorded nothing for this score; ``errored`` is one
        that recorded nothing at all while another scored the case, which is
        what a raised task or judge looks like here (:meth:`errored`).
        """
        kind = self._kind(name)
        if kind == _LABELLED:
            return RUN_SEPARATOR.join(self._labels(e, name) for e in self.experiments)
        if kind == _OPINIONS:
            return RUN_SEPARATOR.join(self._opinions(e, name) for e in self.experiments)
        values = self._values(name)
        parts = []
        for e, v in zip(self.experiments, values, strict=True):
            if v is not None:
                parts.append(number(v))
            else:
                parts.append(self._nothing(e) if self.runs.get(e.id) else MISSING)
        cell = RUN_SEPARATOR.join(parts)
        if len(values) == 2 and values[0] is not None and values[1] is not None:
            cell += f" Δ{signed(values[1] - values[0])}"
        return cell

    def _labels(self, experiment: Experiment, name: str) -> str:
        runs = self.runs.get(experiment.id)
        if not runs:
            return MISSING
        labels = [label for run in runs if (label := category_of(run, name, self.kinds))]
        return ",".join(dict.fromkeys(labels)) if labels else self._nothing(experiment)

    def _opinions(self, experiment: Experiment, name: str) -> str:
        runs = self.runs.get(experiment.id)
        if not runs:
            return MISSING
        parts: list[str] = []
        for run in runs:
            several = authored(run, name)
            if several:
                parts.extend(f"{number(v)} {source}" for v, source in several)
            elif (one := scores_of(run).get(name)) is not None:
                parts.append(number(one))
        return ", ".join(parts) if parts else self._nothing(experiment)

    def _scoreless(self, experiment: Experiment) -> bool:
        """Did this experiment run the case and record no score at all?"""
        runs = self.runs.get(experiment.id) or []
        return bool(runs) and not any(scores_of(run) for run in runs)

    def _ran(self) -> list[Experiment]:
        return [e for e in self.experiments if self.runs.get(e.id)]

    def errored(self) -> bool:
        """Did some experiment record nothing here while another scored the case?

        The joined row carries no error of its own: the compare query selects
        none (``ExperimentItemCompare`` has no error field), and an item's
        ``status`` is the assertions' verdict — passed or failed — not the
        task's. So what a task or a judge that raised leaves behind is this
        asymmetry: the same case, the same judges, and one run produced
        nothing at all. Its cells say ``errored`` rather than a number, so it
        cannot be read as a low score, and the note counts it apart.

        With one experiment, or with no run that scored, there is nothing to
        be asymmetric against and the case is :meth:`unscored` instead.
        """
        scoreless = [e for e in self._ran() if self._scoreless(e)]
        return bool(scoreless) and len(scoreless) < len(self._ran())

    def unscored(self) -> bool:
        """Did every experiment that ran this case record nothing?

        Which says less than an error does: with no run that scored it,
        nothing here claims the judges were meant to run on this case.
        """
        ran = self._ran()
        return bool(ran) and all(self._scoreless(e) for e in ran)

    def _nothing(self, experiment: Experiment) -> str:
        """What an experiment that ran the case and wrote no value in this
        column is called — which depends on what the rest of the row did."""
        return ERRORED if self._scoreless(experiment) and self.errored() else UNSCORED

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
                parts.append(MISSING)
                continue
            parts.append(f"{summary.get('passed_runs', 0)}/{summary.get('total_runs', 0)}")
        return PASS_SEPARATOR.join(parts)

    def worst_trace(self) -> str:
        if self.worst is None:
            return MISSING
        experiment, run = self.worst
        return f"{run.get('trace_id') or MISSING} ({experiment.label})"

    def reason(self) -> str:
        """The first assertion the worst run failed, by name, with what the judge said.

        A suite checks several assertions on one case; a reason without the
        assertion it belongs to says that the case fell but not on what.
        """
        if self.worst is None:
            return MISSING
        for assertion in self.worst[1].get("assertion_results") or []:
            if isinstance(assertion, dict) and assertion.get("passed") is False:
                name = str(assertion.get("value") or "").strip()
                reason = str(assertion.get("reason") or "").strip()
                if name and reason:
                    return f"{name}: {reason}"
                return name or reason
        return ""


def _worst(
    runs: dict[str, list[ExperimentItem]], experiments: list[Experiment]
) -> tuple[Experiment, ExperimentItem] | None:
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
        failed_by: tuple[Experiment, ExperimentItem] | None = None
        for experiment in experiments:
            for run in runs.get(experiment.id, []):
                if run.get("status") == "failed":
                    failed_by = (experiment, run)
                    break
        return failed_by

    worst: tuple[float, Experiment, list[ExperimentItem]] | None = None
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


def _total(run: ExperimentItem) -> float:
    """One experiment item's standing: the sum of the scores it recorded."""
    return sum(scores_of(run).values())
