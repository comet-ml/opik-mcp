from typing import Any

import pytest

from opik_mcp.analytics import EVENT_TOOL_CALLED
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.comet_client import CometAuthError, CometProtocolError, OllieNotEnabledError
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikServerError,
    OpikValidationError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    return r


@pytest.mark.anyio
async def test_success_emits_tool_called(recorder: _Recorder) -> None:
    @instrument_tool("hello")
    async def fn() -> str:
        return "hi"

    assert await fn() == "hi"
    assert len(recorder.events) == 1
    et, props = recorder.events[0]
    assert et == EVENT_TOOL_CALLED
    assert props["tool_name"] == "hello"
    assert props["success"] == "true"
    assert "error_kind" not in props
    assert int(props["duration_ms"]) >= 0


@pytest.mark.parametrize(
    "exc, expected_kind",
    [
        (CometAuthError("x"), "comet_auth_failed"),
        (OllieNotEnabledError("x"), "ollie_not_enabled"),
        (CometProtocolError("x"), "comet_protocol_error"),
        (OpikAuthError("x"), "opik_http_4xx"),
        (OpikNotFoundError("x"), "opik_http_4xx"),
        (OpikValidationError("x"), "opik_http_4xx"),
        (OpikServerError("x"), "opik_http_5xx"),
        (PodNotReadyError("x"), "pod_warmup_timeout"),
        (OllieAuthError("x"), "ollie_auth_failed"),
        (OllieStreamError("x"), "ollie_stream_error"),
        (MissingConfigError("x"), "missing_config"),
        (ValueError("x"), "unknown"),
    ],
)
@pytest.mark.anyio
async def test_error_kind_mapping(recorder: _Recorder, exc: Exception, expected_kind: str) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()
    _et, props = recorder.events[0]
    assert props["success"] == "false"
    assert props["error_kind"] == expected_kind


@pytest.mark.anyio
async def test_baseexception_marks_failure_without_error_kind(recorder: _Recorder) -> None:
    @instrument_tool("hello")
    async def fn() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await fn()
    _, props = recorder.events[0]
    assert props["success"] == "false"
    assert "error_kind" not in props


@pytest.mark.anyio
async def test_cancelled_error_sets_error_kind_cancelled(recorder: _Recorder) -> None:
    """asyncio.CancelledError must yield error_kind='cancelled' and success='false'."""
    import asyncio

    @instrument_tool("read")
    async def fn() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await fn()
    _, props = recorder.events[0]
    assert props["success"] == "false"
    assert props["error_kind"] == "cancelled"


@pytest.mark.anyio
async def test_props_fn_merges_extras(recorder: _Recorder) -> None:
    def props_fn(result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
        return {"entity_type": kwargs.get("entity_type", "")}

    @instrument_tool("read", props_fn=props_fn)
    async def fn(*, entity_type: str) -> str:
        return "ok"

    await fn(entity_type="trace")
    _, props = recorder.events[0]
    assert props["entity_type"] == "trace"
