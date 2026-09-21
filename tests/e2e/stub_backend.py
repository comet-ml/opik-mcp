"""A stand-in for opik-backend, good enough to answer the project overview.

The e2e suite runs the server as a real subprocess over stdio, which is how a
host launches it. What it could not reach was anything that talks to Opik, so
the whole read surface stopped at "needs credentials" and the features this
ticket added had no end-to-end cover at all.

This is the smallest thing that closes that: a threaded HTTP server that
answers the handful of endpoints the overview and the metric series call, with
payloads shaped like the real ones (the envelopes, the field names, the
quirks — ``kpi-cards`` takes its filters as a JSON *string*, the evaluators
path needs its trailing slash, a grouped metric comes back unfilled). It
records every request, so a test can assert what we sent and not only what we
rendered.

It is deliberately dumb: no auth, no paging beyond what a caller passes, no
state. A stub that grows logic starts to disagree with the backend it stands
for, and then the tests pass for the wrong reason.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

#: What an Opik URL puts in front of every route (``https://host/opik/api``).
API_PREFIX = "/api"

PROJECT_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000001"
PROJECT_NAME = "checkout-agent"
TRACE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000002"
SPAN_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000003"

SUITE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000010"
SUITE_NAME = "support-qa"
OTHER_SUITE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000011"
OTHER_SUITE_NAME = "billing-qa"
EXPERIMENT_A = "0199c6a4-3a4c-7f1e-9d2b-000000000020"
EXPERIMENT_B = "0199c6a4-3a4c-7f1e-9d2b-000000000021"
EXPERIMENT_OTHER_SUITE = "0199c6a4-3a4c-7f1e-9d2b-000000000022"

#: The entities the claim table probes and nothing else needed yet. A thread
#: is keyed by a caller-chosen string, not a UUID, which is the whole reason
#: ``read('thread', …)`` cannot be addressed the way a trace is.
THREAD_ID = "checkout-session-8842"
THREAD_TRACE_IDS = (
    "0199c6a4-3a4c-7f1e-9d2b-000000000030",
    "0199c6a4-3a4c-7f1e-9d2b-000000000031",
)
PROMPT_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000040"
PROMPT_NAME = "refund-answer"
ISSUE_ID = "0199c6a4-3a4c-7f1e-9d2b-000000000050"

#: What the backend stores for a test suite, on the dataset's ``type`` and on
#: the experiment's ``evaluation_method``. Not ``test_suite``: opik-backend's
#: OPIK-5795 plans that rename and has not done it.
TEST_SUITE_METHOD = "evaluation_suite"


@dataclass
class Request:
    """One call the server made, as the backend saw it."""

    method: str
    path: str
    query: dict[str, list[str]]
    body: dict[str, Any] | None

    @property
    def payload(self) -> dict[str, Any]:
        """The JSON body, for a request that must have carried one."""
        assert self.body is not None, f"{self.method} {self.path} carried no body"
        return self.body

    def filters(self) -> Any:
        """The ``filters`` this request carried, in whichever form it uses."""
        if self.body is not None and "filters" in self.body:
            raw = self.body["filters"]
            # kpi-cards declares filters as a String; the metric endpoint uses
            # real arrays, one per entity.
            return json.loads(raw) if isinstance(raw, str) else raw
        raw_query = self.query.get("filters", [None])[0]
        return json.loads(raw_query) if raw_query else None


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
    #: — the error is on the trace — which is exactly what the table has to
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
    """The suite the comparison routes serve, described rather than stored.

    ``case_count`` is the whole suite; rows are built on demand for the page
    asked for, so a 100,000-case suite costs the same to stand up as a
    20-case one. That is the point: the scaling scenario compares the two.
    """

    case_count: int = 20
    output_keys: tuple[str, ...] = ("input", "answer", "reasoning")
    #: Whether a filtered page comes back with only one experiment per row.
    #: The real backend strips the experiments that did not match a run-level
    #: filter; this is that behaviour, declared instead of re-derived from the
    #: filter language, which a stub has no business implementing.
    strip_to_experiment: str | None = None


def _default_experiments() -> dict[str, ExperimentSpec]:
    """Two runs of one suite, the second regressing on every fourth case."""
    return {
        EXPERIMENT_A: ExperimentSpec(name="rerank-v1"),
        EXPERIMENT_B: ExperimentSpec(name="rerank-v3", fails_every=4),
        EXPERIMENT_OTHER_SUITE: ExperimentSpec(
            name="billing-v1",
            dataset_id=OTHER_SUITE_ID,
            dataset_name=OTHER_SUITE_NAME,
        ),
    }


@dataclass
class StubBackend:
    """The recorded conversation, plus the knobs a test needs to bend."""

    requests: list[Request] = field(default_factory=list)
    #: Paths that should answer 500, to prove a failing part does not take the
    #: whole read down with it.
    failing: set[str] = field(default_factory=set)
    #: Score names the project has recorded.
    score_names: list[str] = field(default_factory=lambda: ["Hallucination", "Answer Relevance"])
    #: Usage keys the project has recorded.
    usage_keys: list[str] = field(
        default_factory=lambda: ["total_tokens", "prompt_tokens", "completion_tokens"]
    )
    #: The suite the comparison routes serve.
    suite: CompareSuite = field(default_factory=CompareSuite)
    #: Experiments addressable by id, whatever suite they ran.
    experiments: dict[str, ExperimentSpec] = field(default_factory=_default_experiments)
    #: The workspace's feedback definitions, as ``GET /feedback-definitions``
    #: pages them. None by default, which is what the live workspace has.
    feedback_definitions: list[dict[str, Any]] = field(default_factory=list)
    #: How many versions the prompt has. Above the read's inline limit the
    #: parent read has to say so rather than quietly showing the first 100.
    prompt_version_count: int = 3
    #: How many spans the trace has, and how many turns the thread has.
    #: Both reads inline up to 200 and then say what they left out, so a
    #: probe of that claim needs a collection the stub can grow past it.
    span_count: int = 1
    thread_turn_count: int = len(THREAD_TRACE_IDS)
    #: The Diagnostics job's state for the project, or ``None`` for "never
    #: enabled" — which the backend spells as a 404, not as a record.
    agent_insights_job: dict[str, Any] | None = field(
        default_factory=lambda: {
            "project_id": PROJECT_ID,
            "status": "enabled",
            "last_scan_at": "2026-09-09T02:00:00Z",
        }
    )
    #: Diagnostics issues the project has, newest-seen first. Keyed by status
    #: so a probe can ask for the closed ones the default page leaves out.
    issues: list[dict[str, Any]] = field(default_factory=lambda: [_issue()])

    port: int = 0
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------- #

    def start(self) -> str:
        handler = _handler_for(self)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --- what a test asks it ----------------------------------------------- #

    def sent(self, path_fragment: str) -> list[Request]:
        return [r for r in self.requests if path_fragment in r.path]

    def one(self, path_fragment: str) -> Request:
        matches = self.sent(path_fragment)
        assert len(matches) == 1, f"{path_fragment}: {len(matches)} requests, expected 1"
        return matches[0]

    def called(self, path_fragment: str) -> bool:
        return bool(self.sent(path_fragment))

    def _metric_results(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Series shaped by what was asked for, including the quirks.

        A grouped answer is not filled — each group carries only the buckets
        it appeared in — and ``__others__`` arrives unaggregated, several
        points to one bucket. A sub-metric the project never recorded charts
        nothing at all, which is the case that looks like a quiet window and
        is not.
        """
        breakdown = body.get("breakdown")
        if breakdown:
            sub = breakdown.get("sub_metric")
            known = {*self.usage_keys, *self.score_names, "p50", "p90", "p99"}
            if sub is not None and sub not in known:
                return []
            # Deliberately ragged, and the first series is not the widest: a
            # group that ran only on the middle day has no bucket in gpt-4o's
            # data at all, so taking the axis from the first series loses a
            # row, and reading cells by position misdates the rest.
            return [
                {"name": "gpt-4o", "data": _points({"2026-09-01": 10, "2026-09-03": 12})},
                {"name": "claude-opus-4", "data": _points({"2026-09-02": 4})},
                {
                    "name": "__others__",
                    "data": [
                        {"time": "2026-09-02T00:00:00Z", "value": 2},
                        {"time": "2026-09-02T00:00:00Z", "value": 3},
                    ],
                },
            ]
        return _ungrouped_results(str(body.get("metric_type")))

    # --- the comparison payloads ------------------------------------------- #

    def _output_columns(self) -> dict[str, Any]:
        return {
            "columns": [
                {"name": key, "types": ["string"], "filterField": f"output.{key}"}
                for key in self.suite.output_keys
            ]
        }

    def _compare_page(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """One page of cases with their runs attached.

        Two behaviours of the real endpoint are reproduced because the feature
        exists to deal with them, and one is deliberately not. A filter on
        ``id`` is a case-level filter: it selects one row and leaves its runs
        alone. A run-level filter strips the runs that did not match, which is
        what ``strip_to_experiment`` stands for. Everything else about the
        filter language is the backend's business, not the stub's.
        """
        experiment_ids = _ids(query)
        page = int(_one(query, "page", "1"))
        size = int(_one(query, "size", "10"))
        clauses = _filters(query)

        pinned = next((c.get("value") for c in clauses if c.get("field") == "id"), None)
        if pinned is not None:
            index = _case_index(str(pinned))
            rows = [] if index is None else [self._case_row(index, experiment_ids)]
            return _compare_envelope(rows, page=1, total=len(rows))

        strip = self.suite.strip_to_experiment if _is_run_level(clauses) else None
        start = (page - 1) * size
        rows = [
            self._case_row(index, experiment_ids, strip_to=strip)
            for index in range(start, min(start + size, self.suite.case_count))
        ]
        return _compare_envelope(rows, page=page, total=self.suite.case_count)

    def _items_page(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """One page of the dataset's own cases — ``GET /{id}/items``.

        The filters are read far enough to be a 400 when they are not the
        array the backend deserializes, and no further: what each clause
        *means* is opik-backend's business, and a stub that reimplemented it
        would be asserting against itself. Tests read the clauses back off
        ``backend.one("/items").filters()`` to see what was sent.
        """
        _filters(query)  # for the 400 alone: which rows match is not the stub's business
        page = int(_one(query, "page", "1"))
        size = int(_one(query, "size", "10"))
        start = (page - 1) * size
        rows = [
            _dataset_item(index) for index in range(start, min(start + size, self.suite.case_count))
        ]
        return _page(rows, total=self.suite.case_count)

    def _one_item(self, item_id: str) -> tuple[int, Any]:
        """``GET /datasets/items/{itemId}`` — the case, whole and uncut."""
        index = _case_index(item_id)
        if index is None:
            return 404, {"message": f"Dataset item id: {item_id} not found"}
        return 200, _dataset_item(index)

    def _compare_stats(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Count, means and a median over the runs the filter matched.

        Honest where the comparison reads it: the count is the number of
        experiment items of the named experiments whose scores satisfy every
        ``feedback_scores.<name>`` clause, so a test can count the fixture by
        hand and check the header against it. A clause on anything else is
        applied as the backend would only for ``data.<key> =``; the rest of
        the filter language is not the stub's business.
        """
        experiment_ids = _ids(query)
        clauses = _filters(query)
        matched: list[dict[str, Any]] = []
        for index in range(self.suite.case_count):
            row = self._case_row(index, experiment_ids)
            if not _case_matches(row, clauses):
                continue
            matched.extend(item for item in row["experiment_items"] if _item_matches(item, clauses))
        scores: dict[str, list[float]] = {}
        for item in matched:
            for score in item["feedback_scores"]:
                scores.setdefault(score["name"], []).append(float(score["value"]))
        durations = sorted(float(item["duration"]) for item in matched)
        median = durations[len(durations) // 2] if durations else 0.0
        cost = (
            sum(item["total_estimated_cost"] for item in matched) / len(matched) if matched else 0.0
        )
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
                    {
                        "name": f"feedback_scores.{name}",
                        "value": sum(values) / len(values),
                        "type": "AVG",
                    }
                    for name, values in sorted(scores.items())
                ),
                {"name": "usage.total_tokens", "value": 120.0, "type": "AVG"},
            ]
        }

    def _case_row(
        self,
        index: int,
        experiment_ids: list[str],
        *,
        strip_to: str | None = None,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        summaries: dict[str, Any] = {}
        for position, experiment_id in enumerate(experiment_ids):
            spec = self.experiments.get(experiment_id)
            if spec is None or (strip_to is not None and experiment_id != strip_to):
                continue
            failing = spec.fails(index)
            erroring = spec.errors(index)
            passed_runs = 0
            for run in range(spec.runs_per_item):
                # With several runs the first one passes and the rest carry the
                # failure, so the worst run is a different trace from the
                # experiment's first.
                run_failed = failing and (run > 0 or spec.runs_per_item == 1)
                passed_runs += 0 if run_failed or erroring else 1
                items.append(
                    _experiment_item(
                        index, position, run, experiment_id, spec, run_failed, erroring
                    )
                )
            summaries[experiment_id] = {
                "passed_runs": passed_runs,
                "total_runs": spec.runs_per_item,
                # The stub does not model ``pass_threshold``: a case passes
                # here only when every one of its runs did.
                "status": "passed" if passed_runs == spec.runs_per_item else "failed",
            }
        return {
            "id": _case_id(index),
            "dataset_item_id": _case_id(index),
            "source": "manual",
            "data": {
                "question": f"Case {index}: what is the capital of France?",
                "expected_answer": "Paris",
            },
            "experiment_items": items,
            "run_summaries_by_experiment": summaries,
            "created_at": "2026-09-08T10:00:00Z",
        }

    # --- the payloads ------------------------------------------------------ #

    def answer(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        query: dict[str, list[str]] | None = None,
    ) -> tuple[int, Any]:
        if any(fragment in path for fragment in self.failing):
            return 500, {"message": "stub failure"}

        try:
            if path.endswith("/items/experiments/items/output/columns"):
                _ids(query or {})
                return 200, self._output_columns()
            if path.endswith("/items/experiments/items/stats"):
                return 200, self._compare_stats(query or {})
            if path.endswith("/items/experiments/items"):
                return 200, self._compare_page(query or {})
            if path.startswith("/v1/private/datasets/items/"):
                return self._one_item(path.rsplit("/", 1)[-1])
            if path.startswith("/v1/private/datasets/") and path.endswith("/items"):
                return 200, self._items_page(query or {})
        except _BadRequest as refusal:
            return 400, {"message": str(refusal)}
        if path == "/v1/private/feedback-definitions":
            return 200, _page(self.feedback_definitions)
        experiment = self.experiments.get(path.removeprefix("/v1/private/experiments/"))
        if experiment is not None:
            return 200, _experiment(path.rsplit("/", 1)[-1], experiment)
        if path.startswith("/v1/private/datasets/") and path.count("/") == 4:
            return 200, _dataset(path.rsplit("/", 1)[-1])

        if path == "/v1/private/projects":
            return 200, _page([_project()])
        if path == f"/v1/private/projects/{PROJECT_ID}":
            return 200, _project()
        if path.endswith("/kpi-cards"):
            return 200, {"stats": _kpi_stats()}
        if path.endswith("/metrics"):
            return 200, {"results": self._metric_results(body or {})}
        if path == "/v1/private/projects/feedback-scores/names":
            return 200, {"scores": [{"name": name} for name in self.score_names]}
        if path.endswith("/token-usage/names"):
            return 200, {"names": self.usage_keys}
        if path.endswith("/activities"):
            return 200, _page(
                [
                    {
                        "type": "experiment",
                        "name": "rerank-v3",
                        "id": "0199c6a4-3a4c-7f1e-9d2b-000000000003",
                        "created_at": "2026-09-08T10:00:00Z",
                    }
                ]
            )
        if path == "/v1/private/automations/evaluators/":
            return 200, _page(
                [
                    {
                        "id": "r-1",
                        "name": "hallucination-judge",
                        "type": "llm_as_judge",
                        "enabled": True,
                        "sampling_rate": 0.5,
                    }
                ],
                total=1,
            )
        if path == "/v1/private/traces":
            rows = self._traces(query or {}, body or {})
            threaded = bool(rows) and rows[0].get("thread_id") == THREAD_ID
            return 200, _page(rows, total=self.thread_turn_count if threaded else len(rows))
        if path == f"/v1/private/traces/{TRACE_ID}":
            return 200, _trace()
        if path == "/v1/private/traces/threads":
            return 200, _page([_thread_record()])
        if path == "/v1/private/traces/threads/retrieve":
            return 200, _thread_record()
        if path == "/v1/private/spans":
            return 200, _page(
                [_span(index) for index in range(min(self.span_count, _size(query or {})))],
                total=self.span_count,
            )
        if path == f"/v1/private/spans/{SPAN_ID}":
            return 200, _span()
        if path == "/v1/private/datasets":
            return 200, _page([_dataset(SUITE_ID), _dataset(OTHER_SUITE_ID)])
        if path == "/v1/private/experiments":
            return 200, self._experiment_page(query or {})
        if path == "/v1/private/prompts":
            return 200, _page([_prompt()])
        if path == f"/v1/private/prompts/{PROMPT_ID}":
            return 200, _prompt()
        if path == f"/v1/private/prompts/{PROMPT_ID}/versions":
            asked = min(self.prompt_version_count, _size(query or {}))
            return 200, _page(
                [_prompt_version(n) for n in range(asked)],
                total=self.prompt_version_count,
            )
        if path == "/v1/private/toggles/":
            return 200, {"AGENT_INSIGHTS_ENABLED": "true"}
        if path.startswith("/v1/private/agent-insights/jobs/"):
            if self.agent_insights_job is None:
                return 404, {"message": "no job for this project"}
            return 200, self.agent_insights_job
        if path == "/v1/private/agent-insights/issues":
            return 200, self._issue_page(query or {})
        if path.startswith("/v1/private/agent-insights/issues/"):
            return 200, _issue_details(path.rsplit("/", 1)[-1], self.issues)
        return 404, {"message": f"stub has no route for {method} {path}"}

    # --- the routes that read what was asked for --------------------------- #

    def _traces(self, query: dict[str, list[str]], body: dict[str, Any]) -> list[dict[str, Any]]:
        """The project's traces, or a thread's turns when filtered to one.

        ``read('thread', …)`` fetches the turns by filtering traces on
        ``thread_id``, so a stub that always answers the same single trace
        would let a thread read look right with no turns in it.
        """
        raw = _one(query, "filters", "") or json.dumps(body.get("filters") or [])
        if THREAD_ID in raw:
            asked = min(self.thread_turn_count, _size(query, body))
            return [_thread_turn(index, self.thread_turn_count) for index in range(asked)]
        return [_trace()]

    def _experiment_page(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Every experiment the stub holds, narrowed by the ``name`` substring."""
        wanted = _one(query, "name", "")
        rows = [
            _experiment(experiment_id, spec)
            for experiment_id, spec in self.experiments.items()
            if wanted.lower() in spec.name.lower()
        ]
        return _page(rows, total=len(rows))

    def _issue_page(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Open issues unless a status was asked for — the Diagnostics default."""
        wanted = _one(query, "status", "open")
        rows = [issue for issue in self.issues if issue["status"] == wanted]
        return _page(rows, total=len(rows))


# --- payload shapes, as the real backend sends them ------------------------ #


def _page(content: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "content": content,
        "page": 1,
        "size": len(content),
        "total": len(content) if total is None else total,
    }


#: A case's payload carries one value no page can print whole: the reason a
#: dataset item has a read of its own.
_NOTES = "why this case is here. " * 60


def _dataset_item(index: int) -> dict[str, Any]:
    """One case as ``DatasetItem`` serialises it, outside any comparison.

    The ``data`` keys are the columns ``list('dataset_item', dataset_id=…)``
    discovers from the page, so they are what the projection claim is read
    against; ``notes`` is the value no page can print whole, which is what a
    cut and the read that lifts it are proved with.
    """
    return {
        "id": _case_id(index),
        "dataset_id": SUITE_ID,
        "source": "trace",
        "trace_id": _trace_id(index, 0, 0),
        "data": {
            "question": f"Case {index}: what is the capital of France?",
            "expected_answer": "Paris",
            "notes": f"Case {index}: {_NOTES}",
        },
        "tags": ["regression"],
        "created_at": "2026-09-08T10:00:00Z",
        "last_updated_at": "2026-09-08T10:00:00Z",
        "created_by": "stub-user",
        "last_updated_by": "stub-user",
    }


def _filters(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    """The ``filters`` param as ``FiltersFactory`` reads it.

    A JSON array of clauses, or a 400. Nothing here interprets a clause: what
    one matches is opik-backend's business, and a stub that reimplemented it
    would start to disagree with the backend it stands for.
    """
    raw = _one(query, "filters", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise _BadRequest(f"Invalid filters query parameter '{raw}'") from None
    if not isinstance(parsed, list) or not all(isinstance(one, dict) for one in parsed):
        raise _BadRequest(f"Invalid filters query parameter '{raw}'")
    return parsed


def _one(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key) or []
    return values[0] if values else default


def _size(query: dict[str, list[str]], body: dict[str, Any] | None = None) -> int:
    """The page size asked for, from wherever this endpoint takes it.

    A collection the caller asked 200 of must come back at 200, not whole:
    the inline limit is the server's, and a stub that ignores it would let
    "up to 200 inlined" pass on a read that inlined everything.
    """
    if body and isinstance(body.get("size"), int):
        return int(body["size"])
    raw = _one(query, "size", "")
    return int(raw) if raw.isdigit() else 10


def _ids(query: dict[str, list[str]]) -> list[str]:
    """``experiment_ids`` as opik-backend's ``ParamsValidator.getIds`` reads it.

    It deserializes the whole param as JSON into a ``List<UUID>`` and answers
    400 for anything else — a comma-separated list included, which is what
    every other multi-value param in this API takes. The stub is strict about
    it for one reason: it accepted comma-joined once, and the feature shipped
    to a real backend that did not.
    """
    raw = _one(query, "experiment_ids", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise _BadRequest(f"Invalid query param ids '{raw}'") from None
    if not isinstance(parsed, list) or not all(isinstance(one, str) for one in parsed):
        raise _BadRequest(f"Invalid query param ids '{raw}'")
    return parsed


class _BadRequest(Exception):
    """A 400 the stub answers with, the way the backend's validators do."""


#: The comparison ids are built from the case index so a row can be found from
#: the id the server sends back in a refetch, without the stub keeping state.
def _case_id(index: int) -> str:
    return f"0199c6a4-3a4c-7f1e-9d2b-1{index:07d}0000"


def _case_index(case_id: str) -> int | None:
    tail = case_id.rsplit("-", 1)[-1]
    if len(tail) != 12 or not tail.startswith("1") or not tail.isdigit():
        return None
    return int(tail[1:8])


def _trace_id(index: int, position: int, run: int) -> str:
    return f"0199c6a4-3a4c-7f1e-9d2b-2{index:07d}{position}{run:03d}"


#: The filter fields that make the real backend drop the runs that did not
#: match, taken from its EXPERIMENT_ITEM filter strategy.
_RUN_LEVEL_FIELDS = ("feedback_scores", "output", "duration")


_COMPARISONS = {
    "=": lambda a, b: a == b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _item_matches(item: dict[str, Any], clauses: list[dict[str, Any]]) -> bool:
    """Every ``feedback_scores.<name>`` clause, against one run's scores."""
    scores = {s["name"]: float(s["value"]) for s in item["feedback_scores"]}
    for clause in clauses:
        if clause.get("field") != "feedback_scores":
            continue
        compare = _COMPARISONS.get(str(clause.get("operator")))
        value = scores.get(str(clause.get("key")))
        if compare is None or value is None or not compare(value, float(clause["value"])):
            return False
    return True


def _case_matches(row: dict[str, Any], clauses: list[dict[str, Any]]) -> bool:
    """Every ``data.<key> = …`` clause, against the case's data."""
    for clause in clauses:
        field_name = str(clause.get("field", ""))
        key = str(clause.get("key") or "")
        if field_name.startswith("data.") and not key:
            field_name, key = "data", field_name.removeprefix("data.")
        if field_name != "data" or clause.get("operator") != "=":
            continue
        if str(row["data"].get(key)) != str(clause.get("value")):
            return False
    return True


def _is_run_level(clauses: list[dict[str, Any]]) -> bool:
    return any(
        str(c.get("field", "")).split(".")[0] in _RUN_LEVEL_FIELDS
        for c in clauses
        if isinstance(c, dict)
    )


def _compare_envelope(rows: list[dict[str, Any]], *, page: int, total: int) -> dict[str, Any]:
    return {
        "content": rows,
        "page": page,
        "size": len(rows),
        "total": total,
        "columns": [
            {"name": key, "types": ["string"], "filterField": f"data.{key}"}
            for key in ("question", "expected_answer")
        ],
        "sortable_by": ["id", "created_at", "duration", "feedback_scores"],
    }


def _experiment_item(
    index: int,
    position: int,
    run: int,
    experiment_id: str,
    spec: ExperimentSpec,
    failed: bool,
    errored: bool = False,
) -> dict[str, Any]:
    scores = spec.failing_scores if failed else spec.passing_scores
    answer = "Lyon" if failed else "Paris"
    if errored:
        # What the joined endpoint returns for a run whose task or judge
        # raised: the item, its trace, and none of the three fields that say
        # how it went. Nothing here is an error flag, because the payload has
        # none (``ExperimentItemCompare``); the error is on the trace.
        return {
            "id": _trace_id(index, position, run),
            "experiment_id": experiment_id,
            "dataset_item_id": _case_id(index),
            "trace_id": _trace_id(index, position, run),
            "output": {},
            "feedback_scores": [],
            "duration": 1200.0 + index,
            "usage": {},
            "total_estimated_cost": 0.0,
            "created_at": "2026-09-08T10:00:00Z",
        }
    return {
        "id": f"{_trace_id(index, position, run)}",
        "experiment_id": experiment_id,
        "dataset_item_id": _case_id(index),
        "trace_id": _trace_id(index, position, run),
        "output": {
            "input": f"Case {index}: what is the capital of France?",
            "answer": answer,
            "reasoning": f"{spec.name} reasoning for case {index}",
        },
        "feedback_scores": [{"name": name, "value": value} for name, value in scores.items()],
        "assertion_results": [
            {
                "value": "Names the capital",
                "passed": not failed,
                "reason": (
                    f"Case {index}: the answer names {answer}, not Paris." if failed else None
                ),
            }
        ],
        "status": "failed" if failed else "passed",
        "duration": 1200.0 + index,
        "usage": {"total_tokens": 120},
        "total_estimated_cost": 0.0001,
        "created_at": "2026-09-08T10:00:00Z",
    }


def _experiment(experiment_id: str, spec: ExperimentSpec) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "name": spec.name,
        "dataset_id": spec.dataset_id,
        "dataset_name": spec.dataset_name,
        "evaluation_method": spec.evaluation_method,
        "type": "regular",
        "status": "completed",
        "created_at": "2026-09-08T09:00:00Z",
        "last_updated_at": "2026-09-08T09:30:00Z",
        "trace_count": 20,
        "feedback_scores": [
            {"name": name, "value": value} for name, value in spec.passing_scores.items()
        ],
    }


def _dataset(dataset_id: str) -> dict[str, Any]:
    name = SUITE_NAME if dataset_id == SUITE_ID else OTHER_SUITE_NAME
    return {
        "id": dataset_id,
        "name": name,
        "type": TEST_SUITE_METHOD,
        "created_at": "2026-09-01T09:00:00Z",
        "last_updated_at": "2026-09-08T09:00:00Z",
    }


def _thread_record() -> dict[str, Any]:
    """Thread metadata, as both the list page and ``retrieve`` send it.

    ``first_message`` / ``last_message`` are ``argMin``/``argMax`` over the
    turns' own bodies — the same bytes the messages carry — which is why the
    read asks for them truncated.
    """
    return {
        "id": THREAD_ID,
        "thread_id": THREAD_ID,
        "project_id": PROJECT_ID,
        "status": "inactive",
        "start_time": "2026-09-02T10:00:00Z",
        "end_time": "2026-09-02T10:02:00Z",
        "duration": 120_000.0,
        "number_of_messages": len(THREAD_TRACE_IDS),
        "first_message": {"question": "where is my refund?"},
        "last_message": {"answer": "5-7 business days"},
        "total_estimated_cost": 0.0004,
        "created_at": "2026-09-02T10:00:00Z",
        "last_updated_at": "2026-09-02T10:02:00Z",
    }


def _thread_turn(index: int, total: int) -> dict[str, Any]:
    """One trace carrying the thread's id — a turn, once the read projects it."""
    return {
        "id": _turn_id(index),
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "name": "answer",
        # Descending on purpose: the read sorts turns into conversation order
        # itself, and a stub that hands them over already sorted could not
        # tell a working sort from a missing one.
        "start_time": f"2026-09-02T{10 + (total - index) // 60:02d}:{(total - index) % 60:02d}:00Z",
        "end_time": f"2026-09-02T{10 + (total - index) // 60:02d}:{(total - index) % 60:02d}:30Z",
        "duration": 30_000.0,
        # The first turn's body is the one the backend cut, for the same
        # reason only the first span's is: the notice counts the turns that
        # lost bytes, and that count is only meaningful below the total.
        "input": CUT_SPAN_OUTPUT if index == 0 else {"question": f"turn {index}"},
        "output": {"answer": f"answer {index}"},
        "source": "sdk",
    }


def _prompt() -> dict[str, Any]:
    return {
        "id": PROMPT_ID,
        "name": PROMPT_NAME,
        "description": "The refund-window answer",
        "created_at": "2026-09-01T09:00:00Z",
        "last_updated_at": "2026-09-08T09:00:00Z",
    }


def _prompt_version(index: int) -> dict[str, Any]:
    return {
        "id": f"0199c6a4-3a4c-7f1e-9d2b-00000000004{index + 1}",
        "prompt_id": PROMPT_ID,
        "commit": f"v{index + 1}",
        "template": f"Answer the refund question, revision {index + 1}.",
        "type": "mustache",
        "created_at": f"2026-09-0{index + 1}T09:00:00Z",
    }


def _issue() -> dict[str, Any]:
    """One Diagnostics issue, as the list page ranks them."""
    return {
        "id": ISSUE_ID,
        "project_id": PROJECT_ID,
        "name": "Refund window stated wrongly",
        "title": "Refund window stated wrongly",
        "severity": "high",
        "status": "open",
        "total_occurrences": 42,
        "latest_count": 22,
        "last_seen": "2026-09-09",
        "trace_count": 37,
        "cause": "retrieve() returns nothing for refund questions",
        "suggested_fix": "Widen the retriever's window to the policy docs",
        "first_seen_at": "2026-09-01T09:00:00Z",
        "last_seen_at": "2026-09-09T09:00:00Z",
    }


def _issue_details(issue_id: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    """One issue plus its per-day rows, whose ``metadata`` carries the traces.

    The example trace ids arrive per report day and repeat across days; the
    read is the thing that dedupes them, so the stub repeats one on purpose.
    """
    record = next((one for one in issues if one["id"] == issue_id), _issue())
    return {
        **record,
        "details": [
            {
                "report_date": "2026-09-08",
                "occurrence_count": 20,
                "metadata": {"example_trace_ids": [TRACE_ID]},
            },
            {
                "report_date": "2026-09-09",
                "occurrence_count": 22,
                "metadata": {"example_trace_ids": [TRACE_ID, THREAD_TRACE_IDS[0]]},
            },
        ],
    }


def _project() -> dict[str, Any]:
    return {
        "id": PROJECT_ID,
        "name": PROJECT_NAME,
        "visibility": "private",
        "created_at": "2026-08-01T09:00:00Z",
        "last_updated_at": "2026-09-09T09:00:00Z",
        "last_updated_trace_at": "2026-09-09T08:59:00Z",
    }


def _kpi_stats() -> list[dict[str, Any]]:
    """Note the order: count, avg_duration, total_cost, errors.

    The UI renders them count, errors, duration, cost, so anything reading
    this list by position labels cost as duration.
    """
    return [
        {"type": "count", "current_value": 120.0, "previous_value": 80.0},
        {"type": "avg_duration", "current_value": 431.5, "previous_value": 502.0},
        {"type": "total_cost", "current_value": 0.000032, "previous_value": 0.0},
        {"type": "errors", "current_value": 5.0, "previous_value": 0.0},
    ]


def _points(values: dict[str, float | None]) -> list[dict[str, Any]]:
    return [{"time": f"{day}T00:00:00Z", "value": value} for day, value in values.items()]


def _ungrouped_results(metric: str) -> list[dict[str, Any]]:
    """One series per thing the metric fans out into, every bucket filled.

    The nulls in the duration series are the backend's own "nothing here",
    which is a different claim from a zero and has to survive as one.
    """
    if metric == "TRACE_ERROR_RATE":
        return [{"name": "trace_error_rate", "data": _points({"2026-09-01": 0, "2026-09-02": 25})}]
    if metric == "TRACE_COUNT":
        return [{"name": "traces", "data": _points({"2026-09-01": 0, "2026-09-02": 4})}]
    if metric == "DURATION":
        return [
            {"name": "duration.p50", "data": _points({"2026-09-01": None, "2026-09-02": 120.0})},
            {"name": "duration.p99", "data": _points({"2026-09-01": None, "2026-09-02": 980.0})},
        ]
    if metric == "COST":
        return [{"name": "cost", "data": _points({"2026-09-01": 0.0, "2026-09-02": 0.000032})}]
    return [{"name": metric, "data": _points({"2026-09-02": 1})}]


def _trace() -> dict[str, Any]:
    return {
        "id": TRACE_ID,
        "project_id": PROJECT_ID,
        "name": "checkout",
        "start_time": "2026-09-02T10:00:00Z",
        "end_time": "2026-09-02T10:00:01Z",
        "duration": 1000.0,
        "input": {"cart": 3},
        "output": {"ok": True},
        "source": "sdk",
    }


#: What opik-backend hands back for a field it cut: a string, because the
#: substring broke the JSON, of exactly the threshold length.
CUT_SPAN_OUTPUT = "y" * 10_001


def _turn_id(index: int) -> str:
    """A turn's trace id. The first two are named so a probe can point at one."""
    if index < len(THREAD_TRACE_IDS):
        return THREAD_TRACE_IDS[index]
    return f"0199c6a4-3a4c-7f1e-9d2b-3{index:07d}0000"


def _span_id(index: int) -> str:
    return SPAN_ID if index == 0 else f"0199c6a4-3a4c-7f1e-9d2b-4{index:07d}0000"


def _span(index: int = 0) -> dict[str, Any]:
    return {
        "id": _span_id(index),
        "trace_id": TRACE_ID,
        "project_id": PROJECT_ID,
        "name": "charge",
        "type": "llm",
        "start_time": "2026-09-02T10:00:00Z",
        "end_time": "2026-09-02T10:00:01Z",
        "input": {"amount": 12},
        # Only the first span carries the cut body: the point is that a read
        # counts the spans that lost bytes, and a tree where every span was
        # cut cannot tell that count from the span count. It also keeps a
        # 200-span tree from costing two megabytes to serve.
        "output": CUT_SPAN_OUTPUT if index == 0 else {"ok": True},
    }


# --- the HTTP plumbing ----------------------------------------------------- #


def _handler_for(stub: StubBackend) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            """Quiet: the test's own output is the interesting stream."""

        def _serve(self, method: str) -> None:
            parsed = urlparse(self.path)
            # An Opik URL carries the REST base (`.../opik/api`), so the paths
            # arrive prefixed. Routes here are written as the backend declares
            # them, and the prefix is stripped once, in one place.
            path = parsed.path.removeprefix(API_PREFIX)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            stub.requests.append(
                Request(
                    method=method,
                    path=path,
                    query=parse_qs(parsed.query),
                    body=body if isinstance(body, dict) else None,
                )
            )
            status, payload = stub.answer(method, path, body, parse_qs(parsed.query))
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        # BaseHTTPRequestHandler dispatches on these names; they are its
        # spelling, not ours.
        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

    return Handler
