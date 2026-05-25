from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics import EVENT_TOOL_CALLED
from opik_mcp.analytics.wrappers import instrument_tool
from opik_mcp.comet_client import (
    CometAuthError,
    CometPermissionError,
    CometProtocolError,
    OllieNotEnabledError,
)
from opik_mcp.config import MissingConfigError
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError
from opik_mcp.opik_client import (
    OpikAuthError,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)


def _build_pydantic_error() -> PydanticValidationError:
    """Construct a real ``pydantic.ValidationError`` (can't be instantiated directly)."""

    class _M(BaseModel):
        x: int

    try:
        _M.model_validate({"x": "not-an-int"})
    except PydanticValidationError as e:
        return e
    raise AssertionError("model_validate did not raise — pydantic upgraded?")


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
    "exc, expected_kind, expected_status",
    [
        # Permission vs. auth: ``OpikPermissionError`` extends ``OpikAuthError``,
        # so the more-specific class MUST resolve first. A regression that flips
        # this order would mask every 403 as a 401 in BI.
        (OpikAuthError("x"), "auth", 401),
        (OpikPermissionError("x"), "permission", 403),
        (OpikNotFoundError("x"), "not_found", 404),
        (OpikValidationError("x"), "validation", 400),
        (OpikServerError("x"), "upstream_5xx", 500),
        # Comet hierarchy mirrors Opik's: permission before auth, both class-keyed.
        (CometAuthError("x"), "auth", 401),
        (CometPermissionError("x"), "permission", 403),
        # Control-flow errors that don't map to an upstream status — these
        # signal "our client gave up before we got a real HTTP response".
        (CometProtocolError("x"), "unknown", None),
        (OllieNotEnabledError("x"), "unknown", None),
        (OllieStreamError("x"), "unknown", None),
        (MissingConfigError("x"), "unknown", None),
        # Timeouts: pod warmup timeout and httpx network timeouts both fall in
        # the "timeout" bucket; httpx.TimeoutException is the family base.
        (PodNotReadyError("x"), "timeout", None),
        (httpx.ReadTimeout("read timed out"), "timeout", None),
        # Network-level failures (no HTTP response received).
        (OllieAuthError("x"), "auth", None),
        (httpx.ConnectError("connect refused"), "network", None),
        (httpx.ReadError("read error"), "network", None),
        # Validation: typed Opik validation + pydantic argument validation
        # both land in the same coarse bucket — exception_type lets analytics
        # distinguish them downstream.
        (_build_pydantic_error(), "validation", None),
        # Genuine catch-all.
        (ValueError("x"), "unknown", None),
    ],
)
@pytest.mark.anyio
async def test_error_kind_mapping(
    recorder: _Recorder,
    exc: Exception,
    expected_kind: str,
    expected_status: int | None,
) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()
    _et, props = recorder.events[0]
    assert props["success"] == "false"
    assert props["error_kind"] == expected_kind
    # ``exception_type`` carries the granular class name alongside the coarse
    # bucket so dashboards can drill in without re-introducing per-class kinds.
    assert props["exception_type"] == type(exc).__name__
    if expected_status is None:
        assert "http_status" not in props
    else:
        assert props["http_status"] == str(expected_status)


@pytest.mark.anyio
async def test_http_status_error_uses_response_status(recorder: _Recorder) -> None:
    """``httpx.HTTPStatusError`` carries its status on the response object."""
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(422, request=request)
    exc = httpx.HTTPStatusError("unprocessable", request=request, response=response)

    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(httpx.HTTPStatusError):
        await fn()
    _et, props = recorder.events[0]
    # 422 → 4xx that isn't auth/permission/not-found/timeout → "validation".
    assert props["error_kind"] == "validation"
    assert props["http_status"] == "422"
    assert props["exception_type"] == "HTTPStatusError"


@pytest.mark.anyio
async def test_baseexception_marks_failure_without_error_kind(recorder: _Recorder) -> None:
    """Non-``Exception`` ``BaseException`` (KeyboardInterrupt, SystemExit) is
    bucketing-exempt: only the class name surfaces on ``exception_type`` so the
    cancellation/error_kind contract isn't muddied by VM-level interrupts."""

    @instrument_tool("hello")
    async def fn() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await fn()
    _, props = recorder.events[0]
    assert props["success"] == "false"
    assert "error_kind" not in props
    assert props["exception_type"] == "KeyboardInterrupt"
    assert "http_status" not in props


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
    assert props["exception_type"] == "CancelledError"
    assert "http_status" not in props


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
