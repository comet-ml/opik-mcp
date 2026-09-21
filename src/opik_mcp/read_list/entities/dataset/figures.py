"""Each experiment's figures over the cases a comparison matched.

A page of rows answers "which cases"; it does not answer "how many", and an
agent that needs the count was paging through rows to get it. opik-backend's
``…/items/experiments/items/stats`` takes the same filters as the joined list
and returns a count, an average per score and per cost, and duration
percentiles — pooled over the experiments passed. Called with one experiment
at a time it gives the per-experiment figures the UI does not show, so "how
many scored under 0.5 in E1 and in E2" is the header of one filtered call and
no rows need reading.

A categorical score has no meaningful average (``low`` is stored as ``0``),
so it is counted per label instead: one more stats call per experiment per
label, each pinning ``feedback_scores.<name> = <value>``. Those calls are
capped; over the cap the header says which labels can be counted and how.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opik_mcp.read_list.entities.dataset.layout import Experiment, ScoreKinds, number

#: Extra stats calls a page may spend counting categorical labels. Two
#: experiments and a three-label verdict are six; two experiments and two
#: five-label rubrics are twenty, and that is a page of rows' worth of calls.
CATEGORY_CALL_CAP = 12

_RUNS = "experiment_items_count"
_SCORE_PREFIX = "feedback_scores."
_COST = "total_estimated_cost"
_DURATION = "duration"


@dataclass(frozen=True)
class Figures:
    """One experiment's numbers over the runs the filter matched."""

    runs: int | None = None
    scores: dict[str, float] = field(default_factory=dict)
    cost: float | None = None
    p50_ms: float | None = None

    @classmethod
    def of(cls, body: dict[str, Any]) -> Figures:
        """Read the stats list the endpoint returns.

        Entries are ``{name, type, value}``; the same name appears once as an
        ``AVG`` and once as ``PERCENTAGE`` percentiles, so both keys decide.
        """
        runs: int | None = None
        scores: dict[str, float] = {}
        cost: float | None = None
        p50: float | None = None
        for entry in body.get("stats") or []:
            if not isinstance(entry, dict):
                continue
            name, kind, value = entry.get("name"), entry.get("type"), entry.get("value")
            if not isinstance(name, str):
                continue
            if name == _RUNS and kind == "COUNT" and isinstance(value, int):
                runs = value
            elif name.startswith(_SCORE_PREFIX) and kind == "AVG":
                mean = _as_float(value)
                if mean is not None:
                    scores[name.removeprefix(_SCORE_PREFIX)] = mean
            elif name == _COST and kind == "AVG":
                cost = _as_float(value)
            elif name == _DURATION and kind == "PERCENTAGE" and isinstance(value, dict):
                p50 = _as_float(value.get("p50"))
        return cls(runs=runs, scores=scores, cost=cost, p50_ms=p50)


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def category_clause(name: str, value: float) -> dict[str, str]:
    """The compiled clause that pins one categorical score to one label's number."""
    return {"field": "feedback_scores", "key": name, "operator": "=", "value": number(value)}


def label_counts_wanted(
    experiments: list[Experiment],
    figures: dict[str, Figures | BaseException],
    kinds: ScoreKinds,
) -> list[tuple[Experiment, str, str, float]]:
    """Every (experiment, score, label, value) a count call would answer.

    Only for categorical scores the experiments recorded — a definition nobody
    scored against costs nothing — and only for labels the definition names.
    """
    wanted: list[tuple[Experiment, str, str, float]] = []
    for experiment in experiments:
        got = figures.get(experiment.id)
        if not isinstance(got, Figures):
            continue
        for name in got.scores:
            for value, label in kinds.categorical.get(name, {}).items():
                wanted.append((experiment, name, label, value))
    return wanted


def render_figures(
    experiments: list[Experiment],
    figures: dict[str, Figures | BaseException],
    kinds: ScoreKinds,
    label_counts: dict[tuple[str, str], dict[str, int]],
    *,
    counts_skipped: bool,
) -> list[str]:
    """One line per experiment: runs, mean per score, mean cost, median duration.

    ``label_counts`` is keyed by (experiment id, score name); a categorical
    score renders its labels' counts in place of a mean, or, when the count
    calls were skipped for being too many, says so and how to get one.
    """
    # A body with nothing in it. When every experiment's is empty there are
    # no lines at all — no line beats a label with nothing after it. When one
    # experiment's is empty beside another's figures, its line says so, or the
    # missing line would read as a missing experiment.
    if all(figures.get(e.id) == Figures() for e in experiments):
        return []
    lines: list[str] = []
    for experiment in experiments:
        got = figures.get(experiment.id)
        if not isinstance(got, Figures):
            why = f" ({got})" if isinstance(got, Exception) and str(got) else ""
            lines.append(f"{experiment.label}: figures unavailable{why}")
            continue
        if got.runs == 0:
            lines.append(f"{experiment.label}: 0 runs match")
            continue
        if got == Figures():
            lines.append(f"{experiment.label}: no figures returned")
            continue
        parts: list[str] = []
        if got.runs is not None:
            parts.append(f"{got.runs} run{'s' if got.runs != 1 else ''}")
        scored = []
        for name in sorted(got.scores):
            if name in kinds.categorical:
                counted = label_counts.get((experiment.id, name))
                if counted:
                    by_label = ", ".join(f"{label} {n}" for label, n in counted.items())
                    scored.append(f"{name} {by_label}")
                elif counts_skipped:
                    scored.append(f"{name} categorical, not counted")
                else:
                    scored.append(f"{name} categorical")
            else:
                scored.append(f"{name} {number(got.scores[name])}")
        if scored:
            parts.append(", ".join(scored))
        if got.cost is not None:
            parts.append(f"avg cost {got.cost:.3g}")
        if got.p50_ms is not None:
            parts.append(f"p50 {got.p50_ms:.0f} ms")
        lines.append(f"{experiment.label}: {'; '.join(parts)}")
    return lines


def figures_note(experiments: list[Experiment], *, filtered: bool, skipped: list[str]) -> str:
    """How to read the figure lines, and what was left uncounted."""
    scope = "the runs matching the filter" if filtered else "every run"
    text = (
        f"The lines under the count are each experiment's figures over {scope}: runs, "
        "mean per score, mean cost, median duration."
    )
    if skipped:
        names = sorted(skipped)
        text += (
            f" {', '.join(names)} {'is' if len(names) == 1 else 'are'} categorical and not "
            f"counted per label here (more than {CATEGORY_CALL_CAP} calls); pin one label with "
            f"filters='feedback_scores.{names[0]} = <value>' to count it."
        )
    if filtered and len(experiments) > 1:
        # Unfiltered, a different count is different coverage, and the guard
        # above the table already says so.
        text += " A different run count per experiment means the filter matched them differently."
    return text
