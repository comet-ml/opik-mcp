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
        (OpikServerError("x"), "upstream_5xx"),
        # ``CometProtocolError`` / ``OllieStreamError`` are our own control-flow
        # exceptions that the coarse ErrorKind taxonomy collapses into "unknown".
        # They're real bugs (contract drifts / stream failures) so Sentry still
        # captures them — only ``MissingConfigError`` and ``OllieNotEnabledError``
        # are skipped at the class level inside that bucket.
        (CometProtocolError("x"), "unknown"),
        (OllieStreamError("x"), "unknown"),
        (PodNotReadyError("x"), "timeout"),
        (httpx.ConnectError("x"), "network"),
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
async def test_sentry_skips_pydantic_validation_error(
    sentry_recorder: _SentryRecorder,
) -> None:
    """Pydantic ``ValidationError`` buckets to ``"validation"`` (same as
    ``OpikValidationError``), which sits in ``_USER_SIDE_ERROR_KINDS``.
    The skip is keyed by the bucket string, not by exception class, so
    pydantic's unrelated class hierarchy is handled correctly.
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
    assert tags["error_kind"] == "upstream_5xx"


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
    assert tags["error_kind"] == "upstream_5xx"
