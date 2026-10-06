"""A stand-in for opik-backend, good enough for the read and write surface.

A threaded HTTP server the hermetic suite points the real server process at.
It answers with payloads shaped like the real ones (the envelopes, the field
names, the quirks: ``kpi-cards`` takes its filters as a JSON *string*, the
evaluators path needs its trailing slash, a grouped metric comes back
unfilled) and records every request, so a test can assert what we sent and
not only what we rendered. The payloads are ``tests/hermetic/fixtures/*.json``,
filled by ``stub_records``; the comparison routes compute theirs in
``stub_compare``.

The write routes answer with the status opik-backend declares (201 for a
create, 204 for most of the rest) and store nothing. The stub is deliberately
dumb: no auth beyond the bearers a test declares dead, no paging beyond what a
caller passes, no state. A stub that grows logic starts to disagree with the
backend it stands for, and then the tests pass for the wrong reason.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.hermetic.fixtures import load
from tests.hermetic.stub_compare import (
    BadRequest,
    CompareSuite,
    Comparison,
    ExperimentSpec,
    default_experiments,
    experiment,
    query_value,
)
from tests.hermetic.stub_records import (
    CASE_ID,
    EXPERIMENT_A,
    EXPERIMENT_B,
    ISSUE_ID,
    OTHER_SUITE_ID,
    PROJECT_ID,
    PROJECT_NAME,
    PROMPT_ID,
    PROMPT_NAME,
    SPAN_ID,
    SUITE_ID,
    SUITE_NAME,
    THREAD_ID,
    THREAD_MODEL_ID,
    THREAD_TRACE_IDS,
    TRACE_ID,
    dataset,
    fill,
    fill_list,
    issue_details,
    metric_results,
    page,
    page_size,
    prompt_version,
    span,
    thread,
    traces,
)
from tests.hermetic.stub_spend import spend_answer

#: The ids the probes import from here. New tests take them from stub_records.
__all__ = [
    "CASE_ID",
    "EXPERIMENT_A",
    "EXPERIMENT_B",
    "ISSUE_ID",
    "OTHER_SUITE_ID",
    "PROJECT_ID",
    "PROJECT_NAME",
    "PROMPT_ID",
    "PROMPT_NAME",
    "SPAN_ID",
    "SUITE_ID",
    "SUITE_NAME",
    "THREAD_ID",
    "TRACE_ID",
    "StubBackend",
]

#: What an Opik URL puts in front of every route (``https://host/opik/api``).
API_PREFIX = "/api"


@dataclass
class Request:
    """One call the server made, as the backend saw it."""

    method: str
    path: str
    query: dict[str, list[str]]
    body: dict[str, object] | None
    #: Header names lower-cased, so a test does not depend on how httpx
    #: spells them.
    headers: dict[str, str] = field(default_factory=dict)
    #: The body when it is a JSON array, which ``body`` leaves out.
    json_body: object = None

    @property
    def payload(self) -> dict[str, object]:
        """The JSON body, for a request that must have carried one."""
        assert self.body is not None, f"{self.method} {self.path} carried no body"
        return self.body

    def filters(self) -> object:
        """The ``filters`` this request carried, in whichever form it uses."""
        if self.body is not None and "filters" in self.body:
            raw = self.body["filters"]
            # kpi-cards declares filters as a String; the metric endpoint uses
            # real arrays, one per entity.
            return json.loads(raw) if isinstance(raw, str) else raw
        raw_query = self.query.get("filters", [None])[0]
        return json.loads(raw_query) if raw_query else None


def _issue() -> dict[str, object]:
    """One Diagnostics issue, as the list page ranks them."""
    return fill("issue")


@dataclass
class StubBackend:
    """The recorded conversation, plus the knobs a test needs to bend."""

    requests: list[Request] = field(default_factory=list)
    #: Paths that should answer 500, to prove a failing part does not take the
    #: whole read down with it.
    failing: set[str] = field(default_factory=set)
    #: Paths that should answer 400 with an ``ErrorMessage`` and a key that is
    #: not one, to prove only the error strings reach the caller.
    rejecting: set[str] = field(default_factory=set)
    #: Paths that should answer 403, as a key valid for the workspace but not
    #: an admin's does for the spend routes.
    forbidding: set[str] = field(default_factory=set)
    #: Replacement bodies for the AI Spend routes, keyed by fixture name
    #: (``ai_spend_agents``), for a probe that needs a different window.
    spend_payloads: dict[str, object] = field(default_factory=dict)
    #: The project the Opik routes serve. The spend types send every call to
    #: ``claude-code``, so their tests rename the one project here.
    project_name: str = PROJECT_NAME
    #: Score names the project has recorded.
    score_names: list[str] = field(default_factory=lambda: ["Hallucination", "Answer Relevance"])
    #: Usage keys the project has recorded.
    usage_keys: list[str] = field(
        default_factory=lambda: ["total_tokens", "prompt_tokens", "completion_tokens"]
    )
    #: The suite the comparison routes serve.
    suite: CompareSuite = field(default_factory=CompareSuite)
    #: Experiments addressable by id, whatever suite they ran.
    experiments: dict[str, ExperimentSpec] = field(default_factory=default_experiments)
    #: The workspace's feedback definitions, as ``GET /feedback-definitions``
    #: pages them. None by default, which is what the live workspace has.
    feedback_definitions: list[dict[str, object]] = field(default_factory=list)
    #: How many versions the prompt has. Above the read's inline limit the
    #: parent read has to say so rather than quietly showing the first 100.
    prompt_version_count: int = 3
    #: How many spans the trace has, and how many turns the thread has.
    #: Both reads inline up to 200 and then say what they left out, so a
    #: probe of that claim needs a collection the stub can grow past it.
    span_count: int = 1
    thread_turn_count: int = len(THREAD_TRACE_IDS)
    #: The Diagnostics job's state for the project, or ``None`` for "never
    #: enabled", which the backend spells as a 404, not as a record.
    agent_insights_job: dict[str, object] | None = field(
        default_factory=lambda: fill("agent_insights_job")
    )
    #: Diagnostics issues the project has, newest-seen first. Keyed by status
    #: so a probe can ask for the closed ones the default page leaves out.
    issues: list[dict[str, object]] = field(default_factory=lambda: [_issue()])
    #: Bearer tokens the backend no longer accepts. Every route, token
    #: introspection included, answers 401 to a request that carries one. A
    #: test adds a token here partway through a session to expire it.
    dead_bearers: set[str] = field(default_factory=set)
    #: The workspace token introspection names for a live OAuth token.
    oauth_workspace: str = "stub-oauth-workspace"

    port: int = 0
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------- #

    def start(self) -> str:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
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

    def writes(self) -> list[Request]:
        """The requests that went to a write route, in the order sent."""
        return [r for r in self.requests if self._write(r.method, r.path) is not None]

    def reset(self) -> None:
        """Back to the defaults, keeping the socket.

        For a server process that outlives one test: it was started pointing
        at this port, so the stub has to be the same object for the next test.
        """
        defaults = StubBackend()
        for spec in fields(self):
            if spec.name not in {"port", "_httpd", "_thread"}:
                setattr(self, spec.name, getattr(defaults, spec.name))

    # --- the route table --------------------------------------------------- #

    def answer(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        query: dict[str, list[str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        query = query or {}
        if _bearer(headers or {}) in self.dead_bearers:
            # opik-backend's AuthFilter, which runs before any resource.
            return 401, {"code": 401, "message": "User not allowed to access workspace"}
        if any(fragment in path for fragment in self.failing):
            return 500, {"message": "stub failure"}
        if any(fragment in path for fragment in self.rejecting):
            return 400, {"errors": ["name must be unique"], "trace": "stub internals"}
        if any(fragment in path for fragment in self.forbidding):
            return 403, {"message": "stub forbids this path"}
        if method == "POST" and path == "/opik/auth-oauth":
            return 200, load("oauth_introspection", workspace=self.oauth_workspace)
        spend = spend_answer(method, path, self.spend_payloads)
        if spend is not None:
            return spend
        written = self._write(method, path)
        if written is not None:
            return written
        try:
            answered = self._dataset_routes(path, query)
        except BadRequest as refusal:
            return 400, {"message": str(refusal)}
        if answered is not None:
            return answered
        spec = self.experiments.get(path.removeprefix("/v1/private/experiments/"))
        if spec is not None:
            return 200, experiment(path.rsplit("/", 1)[-1], spec)

        project = f"/v1/private/projects/{PROJECT_ID}"
        versions = self.prompt_version_count
        routes: dict[str, Callable[[], object]] = {
            "/is-alive/ping": lambda: {"message": "Healthy Server", "healthy": True},
            "/v1/private/feedback-definitions": lambda: page(self.feedback_definitions),
            "/v1/private/projects": lambda: page([fill("project", project_name=self.project_name)]),
            project: lambda: fill("project", project_name=self.project_name),
            f"{project}/kpi-cards": lambda: load("kpi_cards"),
            f"{project}/metrics": lambda: metric_results(
                body or {}, recorded={*self.usage_keys, *self.score_names}
            ),
            "/v1/private/projects/feedback-scores/names": lambda: {
                "scores": [{"name": name} for name in self.score_names]
            },
            f"{project}/token-usage/names": lambda: {"names": self.usage_keys},
            f"{project}/activities": lambda: page(fill_list("activities")),
            "/v1/private/automations/evaluators/": lambda: page(fill_list("automation_rules")),
            "/v1/private/traces": lambda: traces(query, body or {}, turns=self.thread_turn_count),
            f"/v1/private/traces/{TRACE_ID}": lambda: fill("trace"),
            "/v1/private/traces/threads": lambda: page([thread()]),
            "/v1/private/traces/threads/retrieve": thread,
            "/v1/private/spans": lambda: page(
                [span(index) for index in range(min(self.span_count, page_size(query)))],
                total=self.span_count,
            ),
            f"/v1/private/spans/{SPAN_ID}": span,
            "/v1/private/datasets": lambda: page([dataset(SUITE_ID), dataset(OTHER_SUITE_ID)]),
            "/v1/private/experiments": lambda: self._experiment_page(query),
            "/v1/private/prompts": lambda: page([fill("prompt")]),
            f"/v1/private/prompts/{PROMPT_ID}": lambda: fill("prompt"),
            f"/v1/private/prompts/{PROMPT_ID}/versions": lambda: page(
                [prompt_version(n) for n in range(min(versions, page_size(query)))],
                total=versions,
            ),
            "/v1/private/toggles/": lambda: load("toggles"),
            "/v1/private/agent-insights/issues": lambda: self._issue_page(query),
        }
        if path in routes:
            return 200, routes[path]()
        if path.startswith("/v1/private/agent-insights/jobs/"):
            if self.agent_insights_job is None:
                return 404, {"message": "no job for this project"}
            return 200, self.agent_insights_job
        if path.startswith("/v1/private/agent-insights/issues/"):
            issue_id = path.rsplit("/", 1)[-1]
            issue = next((one for one in self.issues if one["id"] == issue_id), _issue())
            return 200, issue_details(issue)
        return 404, {"message": f"stub has no route for {method} {path}"}

    def _dataset_routes(self, path: str, query: dict[str, list[str]]) -> tuple[int, object] | None:
        comparison = Comparison(self.suite, self.experiments)
        if path.endswith("/items/experiments/items/output/columns"):
            return 200, comparison.output_columns(query)
        if path.endswith("/items/experiments/items/stats"):
            return 200, comparison.compare_stats(query)
        if path.endswith("/items/experiments/items"):
            return 200, comparison.compare_page(query)
        if path.startswith("/v1/private/datasets/items/"):
            return comparison.one_item(path.rsplit("/", 1)[-1])
        if path.startswith("/v1/private/datasets/") and path.endswith("/items"):
            return 200, comparison.items_page(query)
        if path.startswith("/v1/private/datasets/") and path.count("/") == 4:
            return 200, dataset(path.rsplit("/", 1)[-1])
        return None

    # --- the write routes ------------------------------------------------- #

    def _write(self, method: str, path: str) -> tuple[int, object] | None:
        """A write route's answer, or ``None`` when this is not one.

        Statuses are the ones opik-backend's OpenAPI spec declares for each
        route. A create answers 201 with no body and most of the rest 204, so
        a success envelope's ``backend_body`` is empty for all of them except
        a prompt version, which echoes what was stored.

        Two routes answer by what the stub holds rather than always
        succeeding, because the server does something different on each
        answer. A Diagnostics job that exists turns ``enable`` into a 409,
        which the server follows with a PATCH. A project with no job turns
        ``trigger`` into a 404, which the server reports as "enable it first".
        """
        for verb, pattern, status in _WRITE_ROUTES:
            if verb == method and pattern.fullmatch(path):
                return status, None
        if method == "POST" and _THREAD_COMMENT.fullmatch(path):
            if path.split("/")[-2] != THREAD_MODEL_ID:
                return 404, {"errors": ["Thread not found"]}
            return 201, None
        if method == "POST" and path == "/v1/private/prompts/versions":
            return 200, prompt_version(self.prompt_version_count)
        job = _JOB.fullmatch(path)
        if job is None:
            return None
        trigger = job.group("trigger") is not None
        if method == "POST" and trigger:
            if self.agent_insights_job is None:
                return 404, {"errors": ["Job not found"]}
            return 202, None
        if method == "POST":
            if self.agent_insights_job is not None:
                return 409, {"errors": ["Job already exists"]}
            return 201, {"project_id": job.group("project_id"), "status": "enabled"}
        if method == "PATCH" and not trigger:
            if self.agent_insights_job is None:
                return 404, {"errors": ["Job not found"]}
            return 200, {**self.agent_insights_job, "status": "enabled"}
        return None

    # --- the routes that read what was asked for --------------------------- #

    def _experiment_page(self, query: dict[str, list[str]]) -> dict[str, object]:
        """Every experiment the stub holds, narrowed by the ``name`` substring."""
        wanted = query_value(query, "name", "").lower()
        rows: list[object] = [
            experiment(experiment_id, spec)
            for experiment_id, spec in self.experiments.items()
            if wanted in spec.name.lower()
        ]
        return page(rows)

    def _issue_page(self, query: dict[str, list[str]]) -> dict[str, object]:
        """Open issues unless a status was asked for: the Diagnostics default."""
        wanted = query_value(query, "status", "open")
        return page([issue for issue in self.issues if issue["status"] == wanted])


# --- the HTTP plumbing ----------------------------------------------------- #


_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: The write routes that always succeed, with the status each one declares.
#: A score or a comment can target traces, spans or threads; each has its
#: own route.
_WRITE_ROUTES: tuple[tuple[str, re.Pattern[str], int], ...] = tuple(
    (verb, re.compile(pattern), status)
    for verb, pattern, status in (
        ("POST", "/v1/private/traces", 201),
        ("PATCH", f"/v1/private/traces/{_UUID}", 204),
        ("POST", "/v1/private/traces/batch", 204),
        ("POST", "/v1/private/spans", 201),
        ("POST", "/v1/private/spans/batch", 204),
        ("PUT", f"/v1/private/(traces|spans)/{_UUID}/feedback-scores", 204),
        ("PUT", "/v1/private/(traces|spans|traces/threads)/feedback-scores", 204),
        ("POST", f"/v1/private/(traces|spans)/{_UUID}/comments", 201),
        ("POST", "/v1/private/datasets", 201),
        ("PUT", "/v1/private/datasets/items", 204),
        ("POST", "/v1/private/experiments", 201),
        ("POST", "/v1/private/experiments/items", 204),
        ("PUT", "/v1/private/traces/threads/(close|open)", 204),
        ("PATCH", f"/v1/private/agent-insights/issues/{_UUID}", 204),
    )
)
_THREAD_COMMENT = re.compile("/v1/private/traces/threads/[^/]+/comments")
_JOB = re.compile(f"/v1/private/agent-insights/jobs/(?P<project_id>{_UUID})(?P<trigger>/trigger)?")


def _bearer(headers: dict[str, str]) -> str | None:
    """The token of a ``Bearer`` header, or the bare header opik-backend
    also accepts for an API key; ``None`` when there is none."""
    raw = headers.get("authorization")
    if not raw:
        return None
    return raw.removeprefix("Bearer ").strip()


def _handler_for(stub: StubBackend) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:
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
            headers = {name.lower(): value for name, value in self.headers.items()}
            as_object = body if isinstance(body, dict) else None
            query = parse_qs(parsed.query)
            stub.requests.append(Request(method, path, query, as_object, headers, body))
            status, payload = stub.answer(method, path, as_object, query, headers)
            # A 201 or a 204 from opik-backend carries no body, and the server
            # reads an empty one differently from ``null``.
            encoded = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if encoded:
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

        def do_PUT(self) -> None:
            self._serve("PUT")

        def do_PATCH(self) -> None:
            self._serve("PATCH")

    return Handler
