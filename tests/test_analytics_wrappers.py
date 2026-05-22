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
from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchPartialFailureError,
    BatchTooLargeError,
    UnknownOperationError,
    ValidationFailedError,
    WriteError,
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
    "exc, expected_kind",
    [
        # Auth/permission — subclass must match its specific bucket, NOT parent.
        # OpikPermissionError extends OpikAuthError but must surface as
        # "opik_permission_denied" so 403 vs 401 stay distinguishable in BI.
        (OpikAuthError("x"), "opik_auth_failed"),
        (OpikPermissionError("x"), "opik_permission_denied"),
        (OpikNotFoundError("x"), "opik_not_found"),
        (OpikValidationError("x"), "opik_validation_failed"),
        (OpikServerError("x"), "opik_http_5xx"),
        # Comet — same subclass-first contract as Opik.
        (CometAuthError("x"), "comet_auth_failed"),
        (CometPermissionError("x"), "comet_permission_denied"),
        (CometProtocolError("x"), "comet_protocol_error"),
        # Ollie streaming.
        (OllieNotEnabledError("x"), "ollie_not_enabled"),
        (PodNotReadyError("x"), "pod_warmup_timeout"),
        (OllieAuthError("x"), "ollie_auth_failed"),
        (OllieStreamError("x"), "ollie_stream_error"),
        # Config / network / tool-args.
        (MissingConfigError("x"), "missing_config"),
        # httpx network errors — common base RequestError covers the family.
        (httpx.ConnectError("connect refused"), "network_error"),
        (httpx.ReadTimeout("read timed out"), "network_error"),
        (httpx.ReadError("read error"), "network_error"),
        # pydantic validation on tool args (raw pydantic, e.g. from
        # ``RunExperimentConfig.model_validate(...)``).
        (_build_pydantic_error(), "tool_args_invalid"),
        # `write` tool structured errors — dispatcher wraps inner pydantic
        # failures in ValidationFailedError, so the bucket must surface as
        # write_validation_failed rather than tool_args_invalid.
        (
            ValidationFailedError.build("score.create", [], expected_schema={}, example={}),
            "write_validation_failed",
        ),
        (UnknownOperationError.build("bogus.op", ("score.create",)), "write_unknown_operation"),
        (
            AuthorizationDeniedError.build("score.create", "write:scores"),
            "write_authorization_denied",
        ),
        (BatchTooLargeError.build("score.create", 5000, 1000), "write_batch_too_large"),
        (
            BatchPartialFailureError.build("score.create", [], []),
            "write_batch_partial_failure",
        ),
        (
            BackendError.build("score.create", 500, {}, method="POST", path="/v1/x"),
            "write_backend_error",
        ),
        # Bare WriteError (future subclass / direct instance) falls into the
        # catch-all bucket rather than "unknown".
        (WriteError(error="backend_error"), "write_error_other"),
        # Genuine catch-all.
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
