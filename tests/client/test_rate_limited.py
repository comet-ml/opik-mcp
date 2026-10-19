"""A 429 from Opik reads as a rate limit: how long to wait and how to batch.

Before OPIK-8876 it was "Unexpected status 429", bucketed and paged as a 500
(Sentry OPIK-MCP-9E), and the agent retried at once.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from opik_mcp.analytics.events import EVENT_TOOL_CALLED
from opik_mcp.client.opik import OpikClient
from opik_mcp.server import mcp
from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import BackendError

OPIK_BASE = "https://opik.test"
_PROJECT = "demo"
_TRACE_ID = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []
        self.sentry_events: list[BaseException] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))

    def capture_exception(self, exc: BaseException, **_scope: object) -> None:
        self.sentry_events.append(exc)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    monkeypatch.setattr("opik_mcp.error_tracking.capture_exception", r.capture_exception)
    monkeypatch.setenv("OPIK_URL", OPIK_BASE)
    monkeypatch.setenv("OPIK_API_KEY", "key-abc")
    monkeypatch.setenv("OPIK_WORKSPACE", "ws")
    return r


async def _list_spans_answering(response: httpx.Response) -> str:
    with respx.mock(base_url=OPIK_BASE) as backend:
        backend.get("/v1/private/spans").mock(return_value=response)
        async with create_connected_server_and_client_session(mcp._mcp_server) as session:
            await session.initialize()
            result = await session.call_tool(
                "list",
                {
                    "entity_type": "span",
                    "project_name": _PROJECT,
                    "filters": f'trace_id = "{_TRACE_ID}"',
                },
            )
    assert result.isError
    first = result.content[0]
    assert isinstance(first, TextContent)
    return first.text


def _tool_called(recorder: _Recorder) -> dict[str, str]:
    props = [p for event, p in recorder.events if event == EVENT_TOOL_CALLED]
    assert len(props) == 1, recorder.events
    return props[0]


@pytest.mark.parametrize(
    ("headers", "wait"),
    [
        ({"RateLimit-Reset": "23", "Opik-Spans-Limit": "get_spans"}, "Wait 23 s"),
        ({}, "Wait about a minute"),
        # A reset far past the backend's 60 s window is capped, not repeated.
        ({"RateLimit-Reset": "99999"}, "Wait 600 s"),
    ],
)
@pytest.mark.anyio
async def test_a_429_says_how_long_to_wait_and_to_batch_and_does_not_page(
    recorder: _Recorder, headers: dict[str, str], wait: str
) -> None:
    text = await _list_spans_answering(
        httpx.Response(429, headers=headers, text="Too Many Requests: secret-body-text")
    )

    assert "rate-limited spans (429)" in text
    assert wait in text
    assert "per workspace" in text
    assert "list(…, project_name=…, filters=…)" in text
    assert "secret-body-text" not in text
    props = _tool_called(recorder)
    assert props["error_kind"] == "rate_limited"
    assert props["http_status"] == "429"
    assert recorder.sentry_events == [], "a rate limit is the caller's call pattern, not a bug"


@pytest.mark.anyio
async def test_a_500_still_pages(recorder: _Recorder) -> None:
    """The guard above is not vacuous: the same door still pages a server error."""
    await _list_spans_answering(httpx.Response(500))

    assert _tool_called(recorder)["error_kind"] == "upstream_5xx"
    assert len(recorder.sentry_events) == 1


@pytest.mark.anyio
async def test_a_rate_limited_write_says_to_wait_and_send_one_batch() -> None:
    client = OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")
    with respx.mock(base_url=OPIK_BASE) as backend:
        backend.put(f"/v1/private/traces/{_TRACE_ID}/feedback-scores").mock(
            return_value=httpx.Response(429, headers={"RateLimit-Reset": "5"})
        )
        with pytest.raises(BackendError) as raised:
            await run_write(
                operation="score.create",
                data={"target": "trace", "target_id": _TRACE_ID, "name": "h", "value": 0.5},
                client=client,
            )

    body = json.loads(raised.value.to_json())
    assert body["backend_error"]["status"] == 429
    assert "rate-limited 'score.create' (429)" in body["message"]
    assert "Wait about a minute" in body["message"]
