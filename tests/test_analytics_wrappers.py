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
        # pydantic validation on tool args.
        (_build_pydantic_error(), "tool_args_invalid"),
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


# --- Sentry capture wiring ------------------------------------------------ #


class _SentryRecorder:
    """Records every ``_report_to_sentry``-bound capture so wrapper tests can
    assert which kinds reach Sentry and what tags/extras/transaction/
    fingerprint went with them.
    """

    def __init__(self) -> None:
        self.calls: list[
            tuple[BaseException, dict[str, str], dict[str, Any], str | None, list[str] | None]
        ] = []

    def __call__(
        self,
        exc: BaseException,
        *,
        tags: dict[str, str] | None = None,
        extras: dict[str, Any] | None = None,
        transaction: str | None = None,
        fingerprint: list[str] | None = None,
    ) -> None:
        self.calls.append(
            (
                exc,
                dict(tags or {}),
                dict(extras or {}),
                transaction,
                list(fingerprint) if fingerprint is not None else None,
            )
        )


@pytest.fixture
def sentry_recorder(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> _SentryRecorder:
    sr = _SentryRecorder()
    # Patch at the source module — wrappers calls ``error_tracking.capture_exception(...)``,
    # which resolves the attribute on the module at each call, so a patch
    # at the source applies to every caller without per-site shims.
    monkeypatch.setattr("opik_mcp.error_tracking.capture_exception", sr)
    return sr


@pytest.mark.parametrize(
    "exc, expected_kind",
    [
        # Server-side bugs and infrastructure failures — Sentry's bread and butter.
        (OpikServerError("x"), "opik_http_5xx"),
        (CometProtocolError("x"), "comet_protocol_error"),
        (OllieStreamError("x"), "ollie_stream_error"),
        (PodNotReadyError("x"), "pod_warmup_timeout"),
        (httpx.ConnectError("x"), "network_error"),
        (ValueError("x"), "unknown"),
    ],
)
@pytest.mark.anyio
async def test_sentry_captures_non_user_side_failures(
    sentry_recorder: _SentryRecorder, exc: Exception, expected_kind: str
) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()

    assert len(sentry_recorder.calls) == 1
    captured_exc, tags, extras, transaction, fingerprint = sentry_recorder.calls[0]
    assert captured_exc is exc
    assert tags["tool_name"] == "read"
    assert tags["error_kind"] == expected_kind
    assert "duration_ms" in extras
    # Every captured tool failure must carry the tool name in both the
    # transaction (visible in Sentry's issue listing) and the fingerprint
    # (so shared-helper exceptions don't merge across tools).
    assert transaction == "read"
    assert fingerprint == ["{{ default }}", "read"]


@pytest.mark.parametrize(
    "exc",
    [
        # Every kind in ``_USER_SIDE_ERROR_KINDS`` — these are already
        # tracked in BI buckets and would just be noise in Sentry.
        MissingConfigError("x"),
        OpikAuthError("x"),
        OpikPermissionError("x"),
        OpikValidationError("x"),
        OpikNotFoundError("x"),
        CometAuthError("x"),
        CometPermissionError("x"),
        OllieNotEnabledError("x"),
        OllieAuthError("x"),
    ],
)
@pytest.mark.anyio
async def test_sentry_skips_user_side_failures(
    sentry_recorder: _SentryRecorder, exc: Exception
) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()

    assert sentry_recorder.calls == [], (
        f"user-side {type(exc).__name__} must not produce a Sentry event"
    )


@pytest.mark.anyio
async def test_sentry_skips_pydantic_tool_args_invalid(
    sentry_recorder: _SentryRecorder,
) -> None:
    """``tool_args_invalid`` is in the user-side skip set even though
    pydantic's ``ValidationError`` doesn't share the typed-exception
    hierarchy — the skip is keyed by ``error_kind`` string, not class.
    """
    exc = _build_pydantic_error()

    @instrument_tool("write")
    async def fn() -> str:
        raise exc

    with pytest.raises(type(exc)):
        await fn()

    assert sentry_recorder.calls == []


@pytest.mark.anyio
async def test_sentry_skips_cancelled(sentry_recorder: _SentryRecorder) -> None:
    """Host-initiated cancellation is not a failure — Sentry must not see it."""
    import asyncio

    @instrument_tool("read")
    async def fn() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await fn()

    assert sentry_recorder.calls == []


@pytest.mark.anyio
async def test_sentry_capture_includes_props_fn_output_as_tags(
    sentry_recorder: _SentryRecorder,
) -> None:
    """The same bucketing layer BI uses for analytics props is reused for
    Sentry tags — one source of truth for the low-card call shape.
    """

    def props_fn(result: Any, kwargs: dict[str, Any]) -> dict[str, str]:
        # All current real props_fn impls ignore ``result`` and read kwargs
        # only — verify the wrapper passes ``result=None`` on the failure
        # path so this contract stays intact.
        assert result is None
        return {
            "entity_type": kwargs.get("entity_type", ""),
            "id_kind": "uuid",
        }

    @instrument_tool("read", props_fn=props_fn)
    async def fn(*, entity_type: str, id: str) -> str:
        raise OpikServerError("backend exploded")

    with pytest.raises(OpikServerError):
        await fn(entity_type="trace", id="abc")

    _, tags, _, _, _ = sentry_recorder.calls[0]
    assert tags["entity_type"] == "trace"
    assert tags["id_kind"] == "uuid"
    # Wrapper tags still present alongside the props_fn ones.
    assert tags["tool_name"] == "read"
    assert tags["error_kind"] == "opik_http_5xx"


@pytest.mark.anyio
async def test_sentry_capture_attaches_mcp_client_tags_when_ctx_present(
    sentry_recorder: _SentryRecorder,
) -> None:
    """MCP host/client version — invaluable for triaging "is this Claude
    Code or Cursor?" when looking at a Sentry issue.
    """

    class _ClientInfo:
        def __init__(self) -> None:
            self.name = "claude-code"
            self.version = "0.4.2"

    class _Params:
        def __init__(self) -> None:
            self.clientInfo = _ClientInfo()

    class _Session:
        def __init__(self) -> None:
            self.client_params = _Params()

    class _Ctx:
        def __init__(self) -> None:
            self.session = _Session()

    @instrument_tool("read")
    async def fn(*, ctx: Any) -> str:
        raise OpikServerError("boom")

    with pytest.raises(OpikServerError):
        await fn(ctx=_Ctx())

    _, tags, _, _, _ = sentry_recorder.calls[0]
    assert tags["mcp_host"] == "claude-code"
    assert tags["mcp_client_version"] == "0.4.2"


@pytest.mark.anyio
async def test_sentry_capture_omits_mcp_tags_when_ctx_absent(
    sentry_recorder: _SentryRecorder,
) -> None:
    @instrument_tool("read")
    async def fn() -> str:
        raise OpikServerError("boom")

    with pytest.raises(OpikServerError):
        await fn()

    _, tags, _, _, _ = sentry_recorder.calls[0]
    assert "mcp_host" not in tags
    assert "mcp_client_version" not in tags


@pytest.mark.anyio
async def test_sentry_capture_survives_props_fn_failure(
    sentry_recorder: _SentryRecorder,
) -> None:
    """A buggy props_fn must not block the Sentry event — degraded context
    (no bucket tags) is better than no stack trace at all.
    """

    def bad_props_fn(_result: Any, _kwargs: dict[str, Any]) -> dict[str, str]:
        raise RuntimeError("props_fn bug")

    @instrument_tool("read", props_fn=bad_props_fn)
    async def fn() -> str:
        raise OpikServerError("boom")

    with pytest.raises(OpikServerError):
        await fn()

    assert len(sentry_recorder.calls) == 1
    _, tags, _, _, _ = sentry_recorder.calls[0]
    # Wrapper-provided tags still land.
    assert tags["tool_name"] == "read"
    assert tags["error_kind"] == "opik_http_5xx"
