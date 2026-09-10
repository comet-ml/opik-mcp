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

    # --- the payloads ------------------------------------------------------ #

    def answer(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        if any(fragment in path for fragment in self.failing):
            return 500, {"message": "stub failure"}

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
            return 200, _page([_trace()])
        if path == f"/v1/private/traces/{TRACE_ID}":
            return 200, _trace()
        if path == "/v1/private/spans":
            return 200, _page([])
        return 404, {"message": f"stub has no route for {method} {path}"}


# --- payload shapes, as the real backend sends them ------------------------ #


def _page(content: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "content": content,
        "page": 1,
        "size": len(content),
        "total": len(content) if total is None else total,
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
            status, payload = stub.answer(method, path, body)
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
