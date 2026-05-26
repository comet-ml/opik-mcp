import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    transport_probe,
)
from opik_mcp.analytics.mcp_client_info import (
    classify_host_llm_family,
    classify_mcp_host,
    collect_session_props,
)
from opik_mcp.analytics.wrappers import (
    _maybe_emit_session_initialized,
    _reset_seen_sessions_for_tests,
    instrument_tool,
)
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


# --- session_initialized enrichment ------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_probe_and_sessions() -> Iterator[None]:
    transport_probe.reset_for_tests()
    _reset_seen_sessions_for_tests()
    yield
    transport_probe.reset_for_tests()
    _reset_seen_sessions_for_tests()


@pytest.mark.parametrize(
    "raw, expected_bucket",
    [
        ("claude-desktop", "claude-desktop"),
        ("Claude-Desktop", "claude-desktop"),
        ("claude-code/0.42", "claude-code"),
        ("cursor", "cursor"),
        ("cline-extension", "cline"),
        ("continue", "continue"),
        ("windsurf", "windsurf"),
        ("roo-cline", "roo"),
        ("mcp-inspector", "mcp-inspector"),
        ("acme-internal-wrapper-yaro", "other"),
        ("", "other"),
    ],
)
def test_classify_mcp_host(raw: str, expected_bucket: str) -> None:
    assert classify_mcp_host(raw) == expected_bucket


@pytest.mark.parametrize(
    "bucket, family",
    [
        ("claude-desktop", "anthropic"),
        ("claude-code", "anthropic"),
        ("cursor", "cursor"),
        ("cline", "mixed"),
        ("continue", "mixed"),
        ("roo", "mixed"),
        ("windsurf", "mixed"),
        ("mcp-inspector", "inspector"),
        ("other", "unknown"),
    ],
)
def test_classify_host_llm_family(bucket: str, family: str) -> None:
    assert classify_host_llm_family(bucket) == family


_EXPECTED_DEFAULT_SESSION_PROPS: dict[str, str] = {
    "mcp_host": "other",
    "mcp_client_version": "unknown",
    "mcp_protocol_version": "unknown",
    "host_llm_family": "unknown",
    "caps_sampling": "false",
    "caps_elicitation": "false",
    "caps_roots": "false",
    "caps_tasks": "false",
}


def test_collect_session_props_none_session_returns_defaults() -> None:
    """A capture path firing before the init handshake MUST NOT crash; the
    8-key dict still ships, populated with ``"other"`` / ``"unknown"`` / ``"false"``
    sentinels so downstream consumers can rely on the schema.
    """
    assert collect_session_props(None) == _EXPECTED_DEFAULT_SESSION_PROPS


def test_collect_session_props_missing_client_params_returns_defaults() -> None:
    """``session`` present but ``client_params is None`` is the race-condition
    case: the client opened a stream but hasn't completed ``initialize`` yet.
    """
    session_obj = SimpleNamespace(client_params=None)
    assert collect_session_props(session_obj) == _EXPECTED_DEFAULT_SESSION_PROPS


def test_collect_session_props_missing_intermediates_falls_back() -> None:
    """``client_params`` with missing ``clientInfo`` / ``capabilities`` is what
    happens when a non-conforming host stamps a partial handshake; defensive
    ``getattr`` chain MUST NOT raise and MUST emit the defaults dict.
    """
    params = SimpleNamespace()  # no clientInfo, no capabilities, no protocolVersion
    session_obj = SimpleNamespace(client_params=params)
    assert collect_session_props(session_obj) == _EXPECTED_DEFAULT_SESSION_PROPS


def test_collect_session_props_buckets_unknown_host_for_privacy() -> None:
    """Direct privacy contract: a host stamping a per-install identifier as
    its ``clientInfo.name`` MUST bucket to ``"other"`` at the source. Mirrors
    the indirect coverage via ``_maybe_emit_session_initialized`` but pins the
    contract on the extractor itself so future refactors can't drift one
    consumer's bucketing without breaking this test.
    """
    canary = "acme-internal-wrapper-leak-canary-9b2a"
    client_info = SimpleNamespace(name=canary, version="0.1")
    params = SimpleNamespace(clientInfo=client_info, protocolVersion="", capabilities=None)
    session_obj = SimpleNamespace(client_params=params)

    props = collect_session_props(session_obj)
    assert props["mcp_host"] == "other"
    assert canary not in props.values()


def test_maybe_emit_session_initialized_full_props(recorder: _Recorder) -> None:
    """The enriched emit MUST contain bucketed host, family, and caps_* booleans."""
    client_info = SimpleNamespace(name="claude-desktop", version="1.2.3")
    capabilities = SimpleNamespace(
        sampling=SimpleNamespace(),
        elicitation=None,
        roots=SimpleNamespace(),
        tasks=None,
    )
    params = SimpleNamespace(
        clientInfo=client_info,
        protocolVersion="2025-06-01",
        capabilities=capabilities,
    )
    session_obj = SimpleNamespace(client_params=params)
    ctx = SimpleNamespace(session=session_obj)

    _maybe_emit_session_initialized({"ctx": ctx})

    assert len(recorder.events) == 1
    et, props = recorder.events[0]
    assert et == EVENT_SESSION_INITIALIZED
    assert props["mcp_host"] == "claude-desktop"
    assert props["mcp_client_version"] == "1.2.3"
    assert props["mcp_protocol_version"] == "2025-06-01"
    assert props["host_llm_family"] == "anthropic"
    assert props["caps_sampling"] == "true"
    assert props["caps_elicitation"] == "false"
    assert props["caps_roots"] == "true"
    assert props["caps_tasks"] == "false"


def test_maybe_emit_session_initialized_marks_handshake(recorder: _Recorder) -> None:
    """Both transport_probe flags MUST flip when session_initialized fires."""
    session_obj = SimpleNamespace(client_params=None)
    ctx = SimpleNamespace(session=session_obj)

    _maybe_emit_session_initialized({"ctx": ctx})

    assert transport_probe.first_rpc_received() is True
    assert transport_probe.session_reached() is True


def test_maybe_emit_session_initialized_buckets_unknown_host(recorder: _Recorder) -> None:
    """Privacy: a host stamping a per-install name MUST bucket to 'other'."""
    canary_host = "acme-internal-wrapper-leak-canary-9b2a"
    client_info = SimpleNamespace(name=canary_host, version="0.1")
    params = SimpleNamespace(
        clientInfo=client_info,
        protocolVersion="",
        capabilities=None,
    )
    session_obj = SimpleNamespace(client_params=params)
    ctx = SimpleNamespace(session=session_obj)

    _maybe_emit_session_initialized({"ctx": ctx})

    _, props = recorder.events[0]
    assert props["mcp_host"] == "other"
    # capabilities=None must surface all caps_* as "false" so a downstream
    # change that flipped this to "true" would break BI signal.
    assert props["caps_sampling"] == "false"
    assert props["caps_elicitation"] == "false"
    assert props["caps_roots"] == "false"
    assert props["caps_tasks"] == "false"
    assert canary_host not in json.dumps(props), "raw host name leaked"


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
async def test_sentry_capture_buckets_raw_mcp_host_for_privacy(
    sentry_recorder: _SentryRecorder,
) -> None:
    """Privacy parity with BI: a host stamping a per-install name MUST
    bucket to ``"other"`` in Sentry tags too. Without ``collect_session_props``
    the raw string would land in Sentry verbatim, drifting from the BI
    cardinality contract that ``classify_mcp_host`` enforces.
    """
    canary_host = "acme-internal-wrapper-leak-canary-9b2a"

    class _ClientInfo:
        def __init__(self) -> None:
            self.name = canary_host
            # A long, non-semver build hash — must bucket to "unknown".
            self.version = "deadbeef-not-a-semver-suspicious-long"

    class _Params:
        def __init__(self) -> None:
            self.clientInfo = _ClientInfo()
            self.protocolVersion = ""
            self.capabilities = None

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
    assert tags["mcp_host"] == "other"
    assert tags["mcp_client_version"] == "unknown"
    assert canary_host not in json.dumps(tags), "raw host leaked into Sentry tags"


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


# --- Wrapper-exception unwrap (the real production shape) ---------------- #
#
# Every read/list/write tool in this codebase ends a failure with:
#     raise ToolError(_format_client_error(...)) from e
# Until the analytics classifier learned to unwrap, every one of these
# events showed up in BI as "unknown / ToolError" — masking auth, not_found,
# upstream_5xx, and timeout patterns indiscriminately. These tests pin the
# end-to-end contract through the decorator surface (not just the bucket
# helper) so a regression that drops the unwrap fails close to production.


def _wrap_with_cause(wrapper: Exception, inner: Exception) -> Exception:
    """Materialize ``raise wrapper from inner`` and return the caught wrapper.

    Mirrors the shape every tool's error path produces in production. Using
    a real ``raise ... from`` (vs. setting ``__cause__`` by hand) keeps the
    test honest about ``__suppress_context__`` and the implicit context slot.
    """
    try:
        raise wrapper from inner
    except Exception as e:
        return e


@pytest.mark.parametrize(
    "inner, expected_kind, expected_status",
    [
        # The full Phase-1 taxonomy, repeated through a ToolError wrapper.
        # If any cell here regresses to "unknown" the unwrap is broken.
        (OpikAuthError("x"), "auth", 401),
        (OpikPermissionError("x"), "permission", 403),
        (OpikNotFoundError("x"), "not_found", 404),
        (OpikValidationError("x"), "validation", 400),
        (OpikServerError("x"), "upstream_5xx", 500),
        (CometAuthError("x"), "auth", 401),
        (CometPermissionError("x"), "permission", 403),
        (PodNotReadyError("x"), "timeout", None),
        (httpx.ReadTimeout("x"), "timeout", None),
        (httpx.ConnectError("x"), "network", None),
        (OllieAuthError("x"), "auth", None),
        # MissingConfigError stays "unknown" — but the Sentry skip-list still
        # has to recognize it through the wrapper (covered separately below).
        (MissingConfigError("x"), "unknown", None),
    ],
)
@pytest.mark.anyio
async def test_tool_error_wrapper_unwraps_to_real_cause(
    recorder: _Recorder,
    inner: Exception,
    expected_kind: str,
    expected_status: int | None,
) -> None:
    """A ``ToolError`` carrying a real cause must surface the cause's bucket
    + status, while ``exception_type`` keeps the wrapper class (where in our
    code the failure surfaced) and ``cause_type`` carries the leaf class
    (what actually broke). Both pieces are needed to split dashboards by
    "which tool boundary" AND "which upstream"."""
    wrapper = ToolError("user-facing error message")

    @instrument_tool("read")
    async def fn() -> str:
        raise _wrap_with_cause(wrapper, inner)

    with pytest.raises(ToolError):
        await fn()

    _, props = recorder.events[0]
    assert props["success"] == "false"
    assert props["error_kind"] == expected_kind
    # Wrapper class preserved — tells dashboards "this came through a tool
    # boundary" rather than escaping from somewhere unexpected.
    assert props["exception_type"] == "ToolError"
    # Cause class preserved — the actual upstream class.
    assert props["cause_type"] == type(inner).__name__
    if expected_status is None:
        assert "http_status" not in props
    else:
        assert props["http_status"] == str(expected_status)


@pytest.mark.anyio
async def test_bare_tool_error_emits_no_cause_type(recorder: _Recorder) -> None:
    """A ``ToolError`` raised standalone (e.g. ``read_tool.py`` formatting an
    ambiguous-ID hint) has nothing to unwrap. ``error_kind`` stays
    ``"unknown"`` — same as pre-unwrap — and ``cause_type`` is absent."""

    @instrument_tool("read")
    async def fn() -> str:
        raise ToolError("no cause")

    with pytest.raises(ToolError):
        await fn()

    _, props = recorder.events[0]
    assert props["error_kind"] == "unknown"
    assert props["exception_type"] == "ToolError"
    # No cause means no cause_type — the prop is only emitted when the
    # unwrap actually finds a distinct upstream.
    assert "cause_type" not in props


@pytest.mark.anyio
async def test_tool_error_wrapping_http_status_error_routes_by_wire_status(
    recorder: _Recorder,
) -> None:
    """``httpx.HTTPStatusError`` carries the wire status on its response, not
    a typed class. Through the wrapper that lookup must still happen so a
    422 lands in ``validation`` and not ``unknown``."""
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(422, request=request)
    inner = httpx.HTTPStatusError("unprocessable", request=request, response=response)

    @instrument_tool("write")
    async def fn() -> str:
        raise _wrap_with_cause(ToolError("payload rejected"), inner)

    with pytest.raises(ToolError):
        await fn()

    _, props = recorder.events[0]
    assert props["error_kind"] == "validation"
    assert props["http_status"] == "422"
    assert props["exception_type"] == "ToolError"
    assert props["cause_type"] == "HTTPStatusError"


@pytest.mark.anyio
async def test_ollie_stream_error_unwraps_to_upstream_cause(recorder: _Recorder) -> None:
    """``OllieStreamError`` is raised both as a leaf and as a wrapper around
    upstream HTTP failures (``ollie_client.py`` raising from a 404; pod error
    SSE frames carrying an HTTP cause). Wrapped case must route by cause."""

    @instrument_tool("ask_ollie")
    async def fn() -> str:
        raise _wrap_with_cause(OllieStreamError("stream died"), OpikServerError("upstream 500"))

    with pytest.raises(OllieStreamError):
        await fn()

    _, props = recorder.events[0]
    assert props["error_kind"] == "upstream_5xx"
    assert props["http_status"] == "500"
    assert props["exception_type"] == "OllieStreamError"
    assert props["cause_type"] == "OpikServerError"


# --- Sentry routing follows the unwrapped cause -------------------------- #


@pytest.mark.parametrize(
    "inner",
    [
        # The full user-side allowlist — every one must skip Sentry even
        # when transported through a ToolError envelope (which is how they
        # actually arrive in production from read/list/write tools).
        OpikAuthError("x"),
        OpikPermissionError("x"),
        OpikValidationError("x"),
        OpikNotFoundError("x"),
        CometAuthError("x"),
        CometPermissionError("x"),
        OllieAuthError("x"),
        # Class-level user-side skip via _USER_SIDE_EXCEPTIONS. Pre-unwrap
        # the isinstance check ran against the wrapper class and failed open
        # — Sentry got paged for every MissingConfigError-via-ToolError.
        MissingConfigError("x"),
        OllieNotEnabledError("x"),
    ],
)
@pytest.mark.anyio
async def test_sentry_skips_user_side_failures_through_tool_error_wrapper(
    sentry_recorder: _SentryRecorder, inner: Exception
) -> None:
    """Production read/list/write tools always wrap. The Sentry skip-list
    used to run isinstance against the wrapper (always False) — so every
    user-side failure paged Sentry. After the unwrap, the check runs on the
    real cause and the skip applies as designed."""

    @instrument_tool("read")
    async def fn() -> str:
        raise _wrap_with_cause(ToolError("user-facing"), inner)

    with pytest.raises(ToolError):
        await fn()

    assert sentry_recorder.calls == [], (
        f"user-side {type(inner).__name__}-via-ToolError must not page Sentry"
    )


@pytest.mark.parametrize(
    "inner, expected_kind",
    [
        # Server-side bugs and infra failures — Sentry's intended payload.
        # Each one is the wrapped equivalent of the bare-cause tests above.
        (OpikServerError("x"), "upstream_5xx"),
        (PodNotReadyError("x"), "timeout"),
        (httpx.ConnectError("x"), "network"),
        (CometProtocolError("x"), "unknown"),
        # An unexpected RuntimeError carrying nothing typed — the classic
        # "real bug" shape. Even through a wrapper, must reach Sentry.
        (RuntimeError("unexpected"), "unknown"),
    ],
)
@pytest.mark.anyio
async def test_sentry_captures_non_user_side_failures_through_tool_error_wrapper(
    sentry_recorder: _SentryRecorder, inner: Exception, expected_kind: str
) -> None:
    """The mirror of the skip test: causes that aren't user-side still
    page Sentry through a wrapper. ``error_kind`` reflects the unwrapped
    cause; the captured exception is the wrapper (so the Sentry stack trace
    shows the chain Python rendered)."""

    @instrument_tool("read")
    async def fn() -> str:
        raise _wrap_with_cause(ToolError("user-facing"), inner)

    with pytest.raises(ToolError):
        await fn()

    assert len(sentry_recorder.calls) == 1
    captured_exc, tags, _extras, _transaction, _fingerprint = sentry_recorder.calls[0]
    # The wrapper goes to Sentry (its chain renders the cause in the trace).
    assert isinstance(captured_exc, ToolError)
    # But the bucket tag reflects the unwrapped cause.
    assert tags["error_kind"] == expected_kind
