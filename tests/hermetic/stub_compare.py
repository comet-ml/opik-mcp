"""The dataset-item and comparison routes' answers, computed from a described suite.

A suite is described, not stored: ``CompareSuite.case_count`` says how many
cases it has and ``ExperimentSpec`` says how each experiment scored them, and
a page builds only the rows asked for. That is what lets a 100,000-case suite
cost the same to stand up as a 20-case one, which the scaling tests compare.

The row payloads are fixtures (``compare_row``, ``experiment_item``); the
figures ``/stats`` answers are computed here, from the same runs, because they
are arithmetic over the rows rather than a payload of their own.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tests.hermetic.fixtures import record
from tests.hermetic.stub_records import (
    EXPERIMENT_A,
    EXPERIMENT_B,
    EXPERIMENT_OTHER_SUITE,
    OTHER_SUITE_ID,
    OTHER_SUITE_NAME,
    SUITE_ID,
    SUITE_NAME,
    TEST_SUITE_METHOD,
    case_id,
    case_index,
    dataset_item,
    fill,
    page,
    run_trace_id,
)

Clause = dict[str, object]


class BadRequest(Exception):
    """A 400 the stub answers with, the way the backend's validators do."""


@dataclass
class ExperimentSpec:
    """One experiment the comparison routes know about.

    Scores are declared per outcome rather than per case so a test can say
    "B is the one that regresses" and read the numbers straight back out of
    the rendered table.
    """

    name: str
    dataset_id: str = SUITE_ID
    dataset_name: str = SUITE_NAME
    evaluation_method: str = TEST_SUITE_METHOD
    passing_scores: dict[str, float] = field(
        default_factory=lambda: {"correctness": 0.9, "hallucination": 1.0}
    )
    failing_scores: dict[str, float] = field(
        default_factory=lambda: {"correctness": 0.4, "hallucination": 1.0}
    )
    #: Every Nth case fails for this experiment; 0 means it never fails.
    fails_every: int = 0
    #: Every Nth case errors for this experiment: its task or its judge
    #: raised, so the run exists and carries no score, no assertion and no
    #: status. The joined endpoint has no error field to put anything else in
    #: (the error is on the trace), which is exactly what the table has to
    #: read as "errored" rather than as a zero.
    errors_every: int = 0
    #: How many times this experiment ran each case (``execution_policy``).
    runs_per_item: int = 1

    def fails(self, index: int) -> bool:
        return self.fails_every > 0 and (index + 1) % self.fails_every == 0

    def errors(self, index: int) -> bool:
        return self.errors_every > 0 and (index + 1) % self.errors_every == 0


@dataclass
class CompareSuite:
    """The suite the comparison routes serve."""

    case_count: int = 20
    output_keys: tuple[str, ...] = ("input", "answer", "reasoning")
    #: Whether a filtered page comes back with only one experiment per row.
    #: The real backend strips the experiments that did not match a run-level
    #: filter; this is that behaviour, declared instead of re-derived from the
    #: filter language, which a stub has no business implementing.
    strip_to_experiment: str | None = None


def default_experiments() -> dict[str, ExperimentSpec]:
    """Two runs of one suite, the second regressing on every fourth case."""
    return {
        EXPERIMENT_A: ExperimentSpec(name="rerank-v1"),
        EXPERIMENT_B: ExperimentSpec(name="rerank-v3", fails_every=4),
        EXPERIMENT_OTHER_SUITE: ExperimentSpec(
            name="billing-v1", dataset_id=OTHER_SUITE_ID, dataset_name=OTHER_SUITE_NAME
        ),
    }


def experiment(experiment_id: str, spec: ExperimentSpec) -> dict[str, object]:
    return fill(
        "experiment",
        experiment_id=experiment_id,
        experiment_name=spec.name,
        dataset_id=spec.dataset_id,
        dataset_name=spec.dataset_name,
        evaluation_method=spec.evaluation_method,
        feedback_scores=_scores(spec.passing_scores),
    )


@dataclass(frozen=True)
class _Run:
    """One experiment's run of one case: what a row and the figures are built from."""

    experiment_id: str
    spec: ExperimentSpec
    index: int
    position: int
    run: int
    is_failed: bool
    is_errored: bool

    @property
    def scores(self) -> dict[str, float]:
        if self.is_errored:
            return {}
        return self.spec.failing_scores if self.is_failed else self.spec.passing_scores

    @property
    def duration(self) -> float:
        return 1200.0 + self.index

    @property
    def cost(self) -> float:
        return 0.0 if self.is_errored else 0.0001

    def payload(self) -> dict[str, object]:
        common = {
            "run_trace_id": run_trace_id(self.index, self.position, self.run),
            "experiment_id": self.experiment_id,
            "case_id": case_id(self.index),
            "duration": self.duration,
        }
        if self.is_errored:
            # What the joined endpoint returns for a run whose task or judge
            # raised: the item, its trace, and none of the three fields that
            # say how it went (``ExperimentItemCompare`` has no error flag).
            return record("experiment_item_errored", **common)
        answer = "Lyon" if self.is_failed else "Paris"
        return record(
            "experiment_item",
            **common,
            index=self.index,
            answer=answer,
            experiment_name=self.spec.name,
            feedback_scores=_scores(self.scores),
            passed=not self.is_failed,
            reason=f"Case {self.index}: the answer names {answer}, not Paris."
            if self.is_failed
            else None,
            status="failed" if self.is_failed else "passed",
        )


def _scores(scores: dict[str, float]) -> list[object]:
    return [{"name": name, "value": value} for name, value in scores.items()]


def _case_data(index: int) -> dict[str, str]:
    return {
        "question": f"Case {index}: what is the capital of France?",
        "expected_answer": "Paris",
    }


@dataclass
class Comparison:
    """The routes under ``/datasets/{id}/items`` and ``/datasets/items/{id}``."""

    suite: CompareSuite
    experiments: dict[str, ExperimentSpec]

    def output_columns(self, query: dict[str, list[str]]) -> dict[str, object]:
        ids(query)
        return {
            "columns": [
                {"name": key, "types": ["string"], "filterField": f"output.{key}"}
                for key in self.suite.output_keys
            ]
        }

    def compare_page(self, query: dict[str, list[str]]) -> dict[str, object]:
        """One page of cases with their runs attached.

        Two behaviours of the real endpoint are reproduced because the feature
        exists to deal with them. A filter on ``id`` is a case-level filter: it
        selects one row and leaves its runs alone. A run-level filter strips
        the runs that did not match, which is what ``strip_to_experiment``
        stands for. The rest of the filter language is the backend's business.
        """
        experiment_ids = ids(query)
        page_number = int(query_value(query, "page", "1"))
        size = int(query_value(query, "size", "10"))
        clauses = filters(query)

        pinned = next((c.get("value") for c in clauses if c.get("field") == "id"), None)
        if pinned is not None:
            index = case_index(str(pinned))
            rows = [] if index is None else [self._row(index, experiment_ids)]
            return _compare_envelope(rows, page_number=1, total=len(rows))

        strip = self.suite.strip_to_experiment if _is_run_level(clauses) else None
        start = (page_number - 1) * size
        rows = [
            self._row(index, experiment_ids, strip_to=strip)
            for index in range(start, min(start + size, self.suite.case_count))
        ]
        return _compare_envelope(rows, page_number=page_number, total=self.suite.case_count)

    def compare_stats(self, query: dict[str, list[str]]) -> dict[str, object]:
        """Count, means and a median over the runs the filter matched.

        Honest where the comparison reads it: the count is the number of runs
        of the named experiments whose scores satisfy every
        ``feedback_scores.<name>`` clause, so a test can count the fixture by
        hand and check the header against it. Of the rest of the filter
        language only ``data.<key> =`` is applied.
        """
        experiment_ids = ids(query)
        clauses = filters(query)
        matched = [
            run
            for index in range(self.suite.case_count)
            if _case_matches(_case_data(index), clauses)
            for run in self._runs(index, experiment_ids)
            if _run_matches(run, clauses)
        ]
        scores: dict[str, list[float]] = {}
        for run in matched:
            for name, value in run.scores.items():
                scores.setdefault(name, []).append(value)
        durations = sorted(run.duration for run in matched)
        median = durations[len(durations) // 2] if durations else 0.0
        cost = sum(run.cost for run in matched) / len(matched) if matched else 0.0
        return {
            "stats": [
                {"name": "experiment_items_count", "value": len(matched), "type": "COUNT"},
                {"name": "trace_count", "value": len(matched), "type": "COUNT"},
                {"name": "total_estimated_cost", "value": cost, "type": "AVG"},
                {
                    "name": "duration",
                    "value": {"p50": median, "p90": median, "p99": median},
                    "type": "PERCENTAGE",
                },
                *(
                    {"name": f"feedback_scores.{name}", "value": sum(v) / len(v), "type": "AVG"}
                    for name, v in sorted(scores.items())
                ),
                {"name": "usage.total_tokens", "value": 120.0, "type": "AVG"},
            ]
        }

    def items_page(self, query: dict[str, list[str]]) -> dict[str, object]:
        """One page of the dataset's own cases: ``GET /{id}/items``.

        The filters are read far enough to be a 400 when they are not the
        array the backend deserializes, and no further. Tests read the clauses
        back off ``backend.one("/items").filters()`` to see what was sent.
        """
        filters(query)
        page_number = int(query_value(query, "page", "1"))
        size = int(query_value(query, "size", "10"))
        start = (page_number - 1) * size
        end = min(start + size, self.suite.case_count)
        return page(
            [dataset_item(index) for index in range(start, end)], total=self.suite.case_count
        )

    def one_item(self, item_id: str) -> tuple[int, dict[str, object]]:
        """``GET /datasets/items/{itemId}``: the case, whole and uncut."""
        index = case_index(item_id)
        if index is None:
            return 404, {"message": f"Dataset item id: {item_id} not found"}
        return 200, dataset_item(index)

    def _runs(
        self, index: int, experiment_ids: list[str], *, strip_to: str | None = None
    ) -> list[_Run]:
        runs: list[_Run] = []
        for position, experiment_id in enumerate(experiment_ids):
            spec = self.experiments.get(experiment_id)
            if spec is None or (strip_to is not None and experiment_id != strip_to):
                continue
            for run in range(spec.runs_per_item):
                # With several runs the first one passes and the rest carry the
                # failure, so the worst run is a different trace from the
                # experiment's first.
                is_failed = spec.fails(index) and (run > 0 or spec.runs_per_item == 1)
                runs.append(
                    _Run(experiment_id, spec, index, position, run, is_failed, spec.errors(index))
                )
        return runs

    def _row(
        self, index: int, experiment_ids: list[str], *, strip_to: str | None = None
    ) -> dict[str, object]:
        runs = self._runs(index, experiment_ids, strip_to=strip_to)
        summaries: dict[str, object] = {}
        for experiment_id in dict.fromkeys(run.experiment_id for run in runs):
            own = [run for run in runs if run.experiment_id == experiment_id]
            passed = sum(1 for run in own if not (run.is_failed or run.is_errored))
            summaries[experiment_id] = {
                "passed_runs": passed,
                "total_runs": len(own),
                # ``pass_threshold`` is not modelled: a case passes here only
                # when every one of its runs did.
                "status": "passed" if passed == len(own) else "failed",
            }
        return record(
            "compare_row",
            case_id=case_id(index),
            data=_case_data(index),
            experiment_items=[run.payload() for run in runs],
            run_summaries=summaries,
        )


def _compare_envelope(rows: Sequence[object], *, page_number: int, total: int) -> dict[str, object]:
    return record("compare_page", content=list(rows), page=page_number, size=len(rows), total=total)


# --- the query parameters, read as opik-backend reads them --------------------- #


def filters(query: dict[str, list[str]]) -> list[Clause]:
    """The ``filters`` param as ``FiltersFactory`` reads it: a JSON array of
    clauses, or a 400. Nothing here interprets a clause."""
    raw = query_value(query, "filters", "")
    if not raw:
        return []
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest(f"Invalid filters query parameter '{raw}'") from None
    if not isinstance(parsed, list) or not all(isinstance(one, dict) for one in parsed):
        raise BadRequest(f"Invalid filters query parameter '{raw}'")
    return [{str(k): v for k, v in one.items()} for one in parsed]


def ids(query: dict[str, list[str]]) -> list[str]:
    """``experiment_ids`` as ``ParamsValidator.getIds`` reads it.

    It deserializes the whole param as JSON into a ``List<UUID>`` and answers
    400 for anything else, a comma-separated list included, which is what
    every other multi-value param in this API takes. The stub accepted
    comma-joined once, and the feature shipped to a real backend that did not.
    """
    raw = query_value(query, "experiment_ids", "")
    if not raw:
        return []
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest(f"Invalid query param ids '{raw}'") from None
    if not isinstance(parsed, list) or not all(isinstance(one, str) for one in parsed):
        raise BadRequest(f"Invalid query param ids '{raw}'")
    return [str(one) for one in parsed]


def query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key) or []
    return values[0] if values else default


#: The filter fields that make the real backend drop the runs that did not
#: match, taken from its EXPERIMENT_ITEM filter strategy.
_RUN_LEVEL_FIELDS = ("feedback_scores", "output", "duration")

_COMPARISONS: dict[str, Callable[[float, float], bool]] = {
    "=": lambda a, b: a == b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _run_matches(run: _Run, clauses: list[Clause]) -> bool:
    """Every ``feedback_scores.<name>`` clause, against one run's scores."""
    for clause in clauses:
        if clause.get("field") != "feedback_scores":
            continue
        compare = _COMPARISONS.get(str(clause.get("operator")))
        value = run.scores.get(str(clause.get("key")))
        if compare is None or value is None or not compare(value, float(str(clause["value"]))):
            return False
    return True


def _case_matches(data: dict[str, str], clauses: list[Clause]) -> bool:
    """Every ``data.<key> = …`` clause, against the case's data."""
    for clause in clauses:
        field_name = str(clause.get("field", ""))
        key = str(clause.get("key") or "")
        if field_name.startswith("data.") and not key:
            field_name, key = "data", field_name.removeprefix("data.")
        if field_name != "data" or clause.get("operator") != "=":
            continue
        if str(data.get(key)) != str(clause.get("value")):
            return False
    return True


def _is_run_level(clauses: list[Clause]) -> bool:
    return any(str(c.get("field", "")).split(".")[0] in _RUN_LEVEL_FIELDS for c in clauses)
