"""``send_feedback`` through the MCP session, against a stand-in analytics endpoint."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import get_args

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent

from opik_mcp.feedback.tool import ToolName
from tests.feedback.build import API_KEY, FakeEndpoint, build_feedback_server, fake_endpoint

GOOD = (
    "list on spans with a filter on a feedback score returned an empty page. The "
    "project page in the UI shows matching spans for the same window. I expected "
    "the same spans that the UI shows."
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEndpoint]:
    with fake_endpoint(monkeypatch) as recorded:
        yield recorded


@pytest.fixture
def server() -> FastMCP[object]:
    return build_feedback_server()


def _arguments(description: str = GOOD, **overrides: object) -> dict[str, object]:
    return {
        "category": "wrong_result",
        "impact": "workaround",
        "tool": "list",
        "target": "span",
        "description": description,
        **overrides,
    }


def _text(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent), result
    return block.text


async def _send(server: FastMCP[object], *calls: dict[str, object]) -> list[CallToolResult]:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await session.initialize()
        return [await session.call_tool("send_feedback", arguments) for arguments in calls]


@pytest.mark.anyio
async def test_a_report_reaches_the_team_as_one_event_with_its_fields(
    server: FastMCP[object], endpoint: FakeEndpoint
) -> None:
    [result] = await _send(server, _arguments(requested_by_user=True))

    assert not result.isError, _text(result)
    [sent] = endpoint.feedback_properties()
    assert {key: sent[key] for key in ("category", "impact", "tool", "target")} == {
        "category": "wrong_result",
        "impact": "workaround",
        "tool": "list",
        "target": "span",
    }
    assert sent["requested_by_user"] == "true"
    assert sent["description"] == GOOD
    assert sent["feedback_id"] in _text(result)


@pytest.mark.anyio
async def test_the_answer_is_a_short_confirmation(
    server: FastMCP[object], endpoint: FakeEndpoint
) -> None:
    [result] = await _send(server, _arguments())

    assert len(_text(result)) < 100, _text(result)


@pytest.mark.anyio
async def test_a_secret_in_the_description_never_leaves_the_process(
    server: FastMCP[object], endpoint: FakeEndpoint
) -> None:
    secrets = (
        API_KEY,
        "sk-ant-api03-Q1w2E3r4T5y6U7i8O9p0",
        "jane.doe@example.com",
        "0199a3f2-7c41-7d55-9e2b-1f6c0a8d4b37",
    )
    description = (
        f"read on a trace failed with 401 after I passed the key {secrets[0]}. The "
        f"trace input held {secrets[1]} and the user {secrets[2]}, trace id {secrets[3]}. "
        "I expected the trace to come back."
    )

    [result] = await _send(server, _arguments(description, category="error", tool="read"))

    payload = json.dumps(endpoint.events)
    assert not [secret for secret in secrets if secret in payload]
    assert "Removed 4" in _text(result)


@pytest.mark.anyio
async def test_the_call_returns_without_waiting_for_the_endpoint(
    server: FastMCP[object], endpoint: FakeEndpoint
) -> None:
    endpoint.is_open.clear()
    started = time.monotonic()

    [result] = await _send(server, _arguments())

    assert not result.isError, _text(result)
    assert time.monotonic() - started < 2, "send_feedback waited on the analytics endpoint"
    endpoint.is_open.set()
    assert len(endpoint.feedback_properties()) == 1


@pytest.mark.anyio
async def test_an_unexpected_failure_never_reaches_sentry(
    server: FastMCP[object], endpoint: FakeEndpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(
        "opik_mcp.error_tracking.capture_exception",
        lambda exc, **_: captured.append(exc),
    )

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("redaction broke")

    monkeypatch.setattr("opik_mcp.feedback.report.redact", broken)

    [result] = await _send(server, _arguments())

    assert result.isError
    assert captured == [], (
        "send_feedback's frame locals hold the description; src/opik_mcp/feedback/tool.py "
        "must keep reports_to_sentry=False"
    )


@pytest.mark.anyio
async def test_a_target_that_is_not_a_known_name_is_recorded_as_other(
    server: FastMCP[object], endpoint: FakeEndpoint
) -> None:
    await _send(server, _arguments(target="customer-acme-prod-project"))

    [sent] = endpoint.feedback_properties()
    assert sent["target"] == "other"
    assert "acme" not in json.dumps(endpoint.events)


@pytest.mark.anyio
async def test_the_description_reaches_no_other_event(
    server: FastMCP[object], endpoint: FakeEndpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked: list[tuple[str, dict[str, str]]] = []

    class _Recorder:
        def track_event(self, event_type: str, properties: dict[str, str]) -> None:
            tracked.append((event_type, properties))

    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", _Recorder)

    await _send(server, _arguments())

    assert [event for event, _ in tracked].count("opik_mcp_tool_called") == 1, tracked
    assert "feedback score" not in json.dumps(tracked)


async def _tool_names(server: FastMCP[object]) -> list[str]:
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await session.initialize()
        return [tool.name for tool in (await session.list_tools()).tools]


@pytest.mark.anyio
@pytest.mark.parametrize("switch", ["opik_mcp_analytics_enabled", "opik_mcp_feedback_enabled"])
async def test_the_tool_is_not_advertised_when_it_has_nowhere_to_send(switch: str) -> None:
    names = await _tool_names(build_feedback_server(**{switch: False}))

    assert "send_feedback" not in names


@pytest.mark.anyio
async def test_the_tool_argument_names_every_other_tool(server: FastMCP[object]) -> None:
    others = [name for name in await _tool_names(server) if name != "send_feedback"]

    assert sorted(get_args(ToolName)) == sorted([*others, "none"]), (
        "ToolName in src/opik_mcp/feedback/tool.py must list every other "
        f"tool and 'none'; tools/list sends {others}."
    )
