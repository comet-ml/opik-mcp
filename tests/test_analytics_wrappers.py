import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from opik_mcp.analytics import (
    EVENT_SESSION_INITIALIZED,
    EVENT_TOOL_CALLED,
    transport_probe,
)
from opik_mcp.analytics.wrappers import (
    _classify_host_llm_family,
    _classify_mcp_host,
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
    assert _classify_mcp_host(raw) == expected_bucket


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
    assert _classify_host_llm_family(bucket) == family


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
