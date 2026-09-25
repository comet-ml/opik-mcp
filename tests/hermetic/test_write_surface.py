"""Every write operation, end to end, over both transports.

WHAT THIS COVERS THAT NOTHING ELSE DOES. ``tests/writes`` calls the
dispatcher in-process with respx standing in for httpx, so it proves the
request each operation builds and cannot prove what reaches a backend from a
running server: that the tool's arguments survive the MCP layer, that the
client sends the method and body the builder chose, that the url is built from
this session's workspace, and that an HTTP request forwards its caller's
bearer rather than anything the process holds.

Each case says what one call must send and what it must answer. The table is
checked against the registry, so an operation added without a case here fails
``test_every_write_operation_has_an_end_to_end_case``.

Every case runs over stdio (a server per test, holding its own key) and over
Streamable HTTP (one uvicorn process per module, an API-key bearer per
session). The stub stores nothing, so each case asserts on the requests it
recorded, not on a read-back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import pytest
from mcp import ClientSession

from opik_mcp.writes.registry import WRITE_OPERATIONS
from tests.hermetic.servers import (
    API_KEY,
    WORKSPACE,
    HttpServer,
    http_session,
    issues,
    result_json,
    stdio_session,
    stub_with_http_server,
)
from tests.hermetic.stub_backend import (
    EXPERIMENT_A,
    ISSUE_ID,
    PROJECT_ID,
    PROJECT_NAME,
    PROMPT_NAME,
    SUITE_NAME,
    THREAD_ID,
    THREAD_MODEL_ID,
    TRACE_ID,
    StubBackend,
)
from tests.read_list.test_link_shape import live_project_url

pytestmark = pytest.mark.hermetic

#: Ids the caller chooses for what it creates, so the url can name them.
NEW_TRACE_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a1"
NEW_SPAN_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a2"
DATASET_ITEM_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a3"
STARTED_AT = "2026-09-25T10:00:00Z"
ENDED_AT = "2026-09-25T10:00:02Z"

_LOGS = f"/{WORKSPACE}/projects/{PROJECT_ID}/logs"
_DIAGNOSTICS = f"/{WORKSPACE}/projects/{PROJECT_ID}/diagnostics"


@dataclass(frozen=True)
class Sent:
    """One request the backend must receive."""

    method: str
    path: str
    body: object


@dataclass(frozen=True)
class WriteCase:
    """One call to ``write`` and everything it must do.

    ``url_suffix`` is the part of the returned url after the UI base, which
    depends on where Opik lives; ``None`` means the answer carries no url.
    """

    case_id: str
    operation: str
    data: dict[str, object] | list[object]
    sent: tuple[Sent, ...]
    url_suffix: str | None
    item_count: int = 1
    #: Stub knobs this case needs, applied before the session opens.
    arrange: Callable[[StubBackend], None] | None = None
    #: Keys the success envelope must carry beyond the common ones.
    extra_keys: frozenset[str] = field(default_factory=frozenset)


def _without_job(stub: StubBackend) -> None:
    stub.agent_insights_job = None


CASES: tuple[WriteCase, ...] = (
    WriteCase(
        case_id="trace.create",
        operation="trace.create",
        data={
            "id": NEW_TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "checkout",
            "start_time": STARTED_AT,
            "input": {"question": "where is my refund?"},
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/traces",
                {
                    "id": NEW_TRACE_ID,
                    "project_id": PROJECT_ID,
                    "name": "checkout",
                    "start_time": STARTED_AT,
                    "input": {"question": "where is my refund?"},
                },
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=traces&trace={NEW_TRACE_ID}",
    ),
    WriteCase(
        case_id="trace.create-batch",
        operation="trace.create",
        data=[
            {"project_id": PROJECT_ID, "name": "first", "start_time": STARTED_AT},
            {"project_id": PROJECT_ID, "name": "second", "start_time": STARTED_AT},
        ],
        sent=(
            Sent(
                "POST",
                "/v1/private/traces/batch",
                {
                    "traces": [
                        {"project_id": PROJECT_ID, "name": "first", "start_time": STARTED_AT},
                        {"project_id": PROJECT_ID, "name": "second", "start_time": STARTED_AT},
                    ]
                },
            ),
        ),
        # A batch links to the page it landed on, never to one of its rows.
        url_suffix=f"{_LOGS}?logsType=traces",
        item_count=2,
    ),
    WriteCase(
        case_id="trace.update",
        operation="trace.update",
        data={
            "id": TRACE_ID,
            "project_id": PROJECT_ID,
            "end_time": ENDED_AT,
            "output": {"answer": "5-7 business days"},
        },
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/traces/{TRACE_ID}",
                {
                    "project_id": PROJECT_ID,
                    "end_time": ENDED_AT,
                    "output": {"answer": "5-7 business days"},
                },
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="span.create",
        operation="span.create",
        data={
            "id": NEW_SPAN_ID,
            "trace_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "openai.chat",
            "type": "llm",
            "start_time": STARTED_AT,
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/spans",
                {
                    "id": NEW_SPAN_ID,
                    "trace_id": TRACE_ID,
                    "project_id": PROJECT_ID,
                    "name": "openai.chat",
                    "type": "llm",
                    "start_time": STARTED_AT,
                },
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=traces&trace={TRACE_ID}&span={NEW_SPAN_ID}",
    ),
    WriteCase(
        case_id="score.create",
        operation="score.create",
        data={
            "target": "trace",
            "target_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "name": "helpfulness",
            "value": 0.8,
        },
        sent=(
            Sent(
                "PUT",
                f"/v1/private/traces/{TRACE_ID}/feedback-scores",
                {"name": "helpfulness", "value": 0.8, "source": "sdk"},
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="score.create-thread",
        operation="score.create",
        # A thread score has no singleton route; the array form is the only one.
        data=[
            {
                "target": "thread",
                "target_id": THREAD_ID,
                "project_name": PROJECT_NAME,
                "name": "resolved",
                "value": 1,
            }
        ],
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/feedback-scores",
                {
                    "scores": [
                        {
                            "thread_id": THREAD_ID,
                            "project_name": PROJECT_NAME,
                            "name": "resolved",
                            "value": 1.0,
                            "source": "sdk",
                        }
                    ]
                },
            ),
        ),
        # A project name is not resolved after a write has succeeded, so there
        # is no project id to build a link from.
        url_suffix=None,
    ),
    WriteCase(
        case_id="comment.create",
        operation="comment.create",
        data={
            "target": "trace",
            "target_id": TRACE_ID,
            "project_id": PROJECT_ID,
            "text": "retry with temperature=0",
        },
        sent=(
            Sent(
                "POST",
                f"/v1/private/traces/{TRACE_ID}/comments",
                {"text": "retry with temperature=0"},
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=traces&trace={TRACE_ID}",
    ),
    WriteCase(
        case_id="comment.create-thread",
        operation="comment.create",
        data={
            "target": "thread",
            "target_id": THREAD_ID,
            "project_id": PROJECT_ID,
            "text": "customer asked twice",
        },
        # The caller names the thread by its thread_id; the comment route takes
        # the model UUID, which the server looks up first.
        sent=(
            Sent(
                "POST",
                f"/v1/private/traces/threads/{THREAD_MODEL_ID}/comments",
                {"text": "customer asked twice"},
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=threads&thread={THREAD_ID}",
    ),
    WriteCase(
        case_id="prompt_version.save",
        operation="prompt_version.save",
        data={
            "name": PROMPT_NAME,
            "template": "Answer the refund question in one line.",
            "commit": "v4",
            "change_description": "shorter",
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/prompts/versions",
                {
                    "name": PROMPT_NAME,
                    "version": {
                        "template": "Answer the refund question in one line.",
                        "commit": "v4",
                    },
                    "change_description": "shorter",
                },
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="dataset.create",
        operation="dataset.create",
        data={"name": "refund-cases", "type": "test_suite"},
        # The backend still spells a test suite ``evaluation_suite``.
        sent=(
            Sent(
                "POST",
                "/v1/private/datasets",
                {"name": "refund-cases", "type": "evaluation_suite"},
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="dataset_item.upsert",
        operation="dataset_item.upsert",
        data={
            "dataset_name": SUITE_NAME,
            "items": [
                {"input": {"question": "refund?"}, "expected_output": {"answer": "5-7 days"}},
                {"input": {"question": "exchange?"}},
            ],
        },
        sent=(
            Sent(
                "PUT",
                "/v1/private/datasets/items",
                {
                    "dataset_name": SUITE_NAME,
                    "items": [
                        {
                            "data": {
                                "input": {"question": "refund?"},
                                "expected_output": {"answer": "5-7 days"},
                            },
                            "source": "sdk",
                        },
                        {"data": {"input": {"question": "exchange?"}}, "source": "sdk"},
                    ],
                },
            ),
        ),
        url_suffix=None,
        # Counted through the envelope, not as the one object that carries it.
        item_count=2,
    ),
    WriteCase(
        case_id="experiment.create",
        operation="experiment.create",
        data={"dataset_name": SUITE_NAME, "name": "rerank-v4"},
        sent=(
            Sent(
                "POST",
                "/v1/private/experiments",
                {"dataset_name": SUITE_NAME, "name": "rerank-v4"},
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="experiment_item.create",
        operation="experiment_item.create",
        data={
            "experiment_items": [
                {
                    "experiment_id": EXPERIMENT_A,
                    "dataset_item_id": DATASET_ITEM_ID,
                    "trace_id": TRACE_ID,
                }
            ]
        },
        sent=(
            Sent(
                "POST",
                "/v1/private/experiments/items",
                {
                    "experiment_items": [
                        {
                            "experiment_id": EXPERIMENT_A,
                            "dataset_item_id": DATASET_ITEM_ID,
                            "trace_id": TRACE_ID,
                        }
                    ]
                },
            ),
        ),
        url_suffix=None,
    ),
    WriteCase(
        case_id="thread.close",
        operation="thread.close",
        data={"thread_id": THREAD_ID, "project_id": PROJECT_ID},
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/close",
                {"thread_id": THREAD_ID, "project_id": PROJECT_ID},
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=threads&thread={THREAD_ID}",
    ),
    WriteCase(
        case_id="thread.open",
        operation="thread.open",
        data={"thread_id": THREAD_ID, "project_id": PROJECT_ID},
        sent=(
            Sent(
                "PUT",
                "/v1/private/traces/threads/open",
                {"thread_id": THREAD_ID, "project_id": PROJECT_ID},
            ),
        ),
        url_suffix=f"{_LOGS}?logsType=threads&thread={THREAD_ID}",
    ),
    WriteCase(
        case_id="agent_insights_issue.resolve",
        operation="agent_insights_issue.resolve",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "resolved"},
            ),
        ),
        # A resolved issue leaves the default page, so the link follows it.
        url_suffix=f"{_DIAGNOSTICS}/resolved?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_issue.close",
        operation="agent_insights_issue.close",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "closed"},
            ),
        ),
        url_suffix=f"{_DIAGNOSTICS}/resolved?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_issue.reopen",
        operation="agent_insights_issue.reopen",
        data={"issue_id": ISSUE_ID, "project_name": PROJECT_NAME},
        sent=(
            Sent(
                "PATCH",
                f"/v1/private/agent-insights/issues/{ISSUE_ID}",
                {"project_id": PROJECT_ID, "status": "open"},
            ),
        ),
        url_suffix=f"{_DIAGNOSTICS}?issue={ISSUE_ID}",
    ),
    WriteCase(
        case_id="agent_insights_job.enable",
        operation="agent_insights_job.enable",
        data={"project_name": PROJECT_NAME},
        sent=(Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {}),),
        url_suffix=_DIAGNOSTICS,
        arrange=_without_job,
    ),
    WriteCase(
        case_id="agent_insights_job.enable-existing",
        operation="agent_insights_job.enable",
        data={"project_name": PROJECT_NAME},
        # The job exists, so the create conflicts and the server flips its
        # status instead: enabling twice is safe.
        sent=(
            Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {}),
            Sent("PATCH", f"/v1/private/agent-insights/jobs/{PROJECT_ID}", {"status": "enabled"}),
        ),
        url_suffix=_DIAGNOSTICS,
    ),
    WriteCase(
        case_id="agent_insights_job.trigger",
        operation="agent_insights_job.trigger",
        data={"project_name": PROJECT_NAME},
        sent=(Sent("POST", f"/v1/private/agent-insights/jobs/{PROJECT_ID}/trigger", {}),),
        url_suffix=_DIAGNOSTICS,
        extra_keys=frozenset({"note"}),
    ),
)


# --- transports ------------------------------------------------------------ #


@dataclass(frozen=True)
class Wire:
    """A backend and a way to open sessions to a server that talks to it."""

    transport: str
    backend: StubBackend
    connect: Callable[[], AbstractAsyncContextManager[ClientSession]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def http_server() -> Iterator[tuple[StubBackend, HttpServer]]:
    with stub_with_http_server() as running:
        yield running


@pytest.fixture(params=["stdio", "http"])
def wire(request: pytest.FixtureRequest) -> Iterator[Wire]:
    if request.param == "http":
        stub, server = request.getfixturevalue("http_server")
        stub.reset()

        @asynccontextmanager
        async def over_http() -> AsyncIterator[ClientSession]:
            async with http_session(server, bearer=API_KEY) as session:
                yield session

        yield Wire("http", stub, over_http)
        return

    stub = StubBackend()
    stub.start()

    @asynccontextmanager
    async def over_stdio() -> AsyncIterator[ClientSession]:
        async with stdio_session(stub) as session:
            yield session

    try:
        yield Wire("stdio", stub, over_stdio)
    finally:
        stub.stop()


# --- the tests ------------------------------------------------------------- #


def test_every_write_operation_has_an_end_to_end_case() -> None:
    """A new operation needs a row in ``CASES``; this file is where it goes."""
    covered = {case.operation for case in CASES}
    missing = sorted(set(WRITE_OPERATIONS) - covered)
    assert not missing, (
        f"write operations with no hermetic case: {missing}. "
        "Add a WriteCase to CASES in tests/hermetic/test_write_surface.py."
    )


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_a_write_sends_its_request_and_answers_with_where_to_look(
    wire: Wire, case: WriteCase
) -> None:
    if case.arrange is not None:
        case.arrange(wire.backend)
    async with wire.connect() as session:
        result = await session.call_tool("write", {"operation": case.operation, "data": case.data})
    answer = result_json(result)
    assert not result.isError, answer

    sent = [Sent(r.method, r.path, r.json_body) for r in wire.backend.writes()]
    assert sent == list(case.sent)

    last = case.sent[-1]
    assert answer["ok"] is True
    assert answer["operation"] == case.operation
    assert (answer["method"], answer["path"]) == (last.method, last.path)
    assert answer["item_count"] == case.item_count
    assert case.extra_keys <= answer.keys()

    url = answer.get("url")
    if case.url_suffix is None:
        assert url is None, f"expected no url, got {url}"
    else:
        assert isinstance(url, str), f"expected a url ending {case.url_suffix}, got none"
        assert url.endswith(case.url_suffix), url
        assert live_project_url(url), f"not a page the UI serves: {url}"


@pytest.mark.anyio
async def test_each_request_carries_the_callers_credential_and_workspace(wire: Wire) -> None:
    """Over HTTP the bearer is the caller's; the process holds no key at all."""
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "x"}}
        )
    assert not result.isError, result_json(result)

    (request,) = wire.backend.writes()
    assert request.headers.get("comet-workspace") == WORKSPACE
    assert API_KEY in request.headers.get("authorization", "")


@pytest.mark.anyio
@pytest.mark.parametrize("operation", WRITE_OPERATIONS)
async def test_a_payload_the_operation_does_not_accept_is_refused_before_anything_is_sent(
    wire: Wire, operation: str
) -> None:
    """The ``validation_failed`` envelope carries what the caller needs to fix
    the call in one turn: which field, the schema, and a working example."""
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": operation, "data": {"not_a_field": 1}}
        )
    assert result.isError
    envelope = result_json(result)

    assert envelope["error"] == "validation_failed"
    assert envelope["operation"] == operation
    assert any(issue["field"] == "not_a_field" for issue in issues(envelope)), envelope
    schema = envelope["expected_schema"]
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert envelope["example"]
    assert wire.backend.writes() == []


@pytest.mark.anyio
async def test_a_backend_failure_comes_back_with_the_request_that_failed(wire: Wire) -> None:
    wire.backend.failing.add("/v1/private/datasets")
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": "dataset.create", "data": {"name": "refund-cases"}}
        )
    assert result.isError
    envelope = result_json(result)

    assert envelope["error"] == "backend_error"
    assert envelope["backend_error"] == {
        "status": 500,
        "body": {"message": "stub failure"},
        "method": "POST",
        "path": "/v1/private/datasets",
    }


@pytest.mark.anyio
async def test_a_scan_of_a_project_without_diagnostics_says_to_enable_it(wire: Wire) -> None:
    """A 404 from the trigger route is a missing prerequisite, and the answer
    names the operation that fixes it."""
    wire.backend.agent_insights_job = None
    async with wire.connect() as session:
        result = await session.call_tool(
            "write",
            {"operation": "agent_insights_job.trigger", "data": {"project_name": PROJECT_NAME}},
        )
    assert result.isError
    envelope = result_json(result)

    (issue,) = issues(envelope)
    assert issue["code"] == "diagnostics_not_enabled"
    assert "agent_insights_job.enable" in str(issue["message"])
