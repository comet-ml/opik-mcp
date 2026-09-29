"""What the write files share: the case table's shape and the checks each case runs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from mcp import ClientSession

from opik_mcp.writes.registry import WRITE_OPERATIONS
from tests.hermetic.servers import WORKSPACE, issues, result_json
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID
from tests.read_list.test_link_shape import live_project_url

#: Ids the caller chooses for what it creates, so the url can name them.
NEW_TRACE_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a1"
NEW_SPAN_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a2"
DATASET_ITEM_ID = "0199c6a4-3a4c-7f1e-9d2b-0000000000a3"
STARTED_AT = "2026-09-25T10:00:00Z"
ENDED_AT = "2026-09-25T10:00:02Z"

LOGS = f"/{WORKSPACE}/projects/{PROJECT_ID}/logs"
DIAGNOSTICS = f"/{WORKSPACE}/projects/{PROJECT_ID}/diagnostics"


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


@dataclass(frozen=True)
class Wire:
    """A backend and a way to open sessions to a server that talks to it."""

    transport: str
    backend: StubBackend
    connect: Callable[[], AbstractAsyncContextManager[ClientSession]]


def operations_of(cases: Sequence[WriteCase]) -> list[str]:
    """The operations a table covers, in registry order."""
    covered = {case.operation for case in cases}
    return [operation for operation in WRITE_OPERATIONS if operation in covered]


async def assert_the_write_lands_and_links(wire: Wire, case: WriteCase) -> None:
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


async def assert_refused_before_anything_is_sent(wire: Wire, operation: str) -> None:
    async with wire.connect() as session:
        result = await session.call_tool(
            "write", {"operation": operation, "data": {"not_a_field": 1}}
        )
        schema_result = await session.call_tool("schema", {"operation": operation})
    assert result.isError
    envelope = result_json(result)

    assert envelope["error"] == "validation_failed"
    assert envelope["operation"] == operation
    assert any(issue["field"] == "not_a_field" for issue in issues(envelope)), envelope
    assert "expected_schema" not in envelope
    message = str(envelope["message"])
    assert f"write({operation!r}, data=…)" in message
    assert f"schema({operation!r})" in message
    schema = result_json(schema_result)["schema"]
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert envelope["example"]
    assert wire.backend.writes() == []
