import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from opik_mcp.analytics import EVENT_ASK_OLLIE_COMPLETED
from opik_mcp.ask_ollie import run_ask_ollie
from opik_mcp.comet_client import CometAuthError, PodDiscovery
from opik_mcp.config import Settings
from opik_mcp.ollie_client import OllieAuthError, OllieStreamError, PodNotReadyError, SSEEvent
from opik_mcp.opik_client import OpikAuthError, OpikServerError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.ask_ollie._analytics", lambda: r)
    return r


def _settings() -> Settings:
    return Settings(opik_api_key="k", comet_workspace="ws-1")


async def _run_with(comet: Any, ollie: Any, recorder: _Recorder, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "query": "hi",
        "settings": _settings(),
        "comet_client": comet,
        "ollie_client": ollie,
    }
    kwargs.update(overrides)
    return await run_ask_ollie(**kwargs)


@pytest.mark.anyio
async def test_message_end_emits_completed_success(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[SSEEvent]:
        yield SSEEvent(event="message_delta", data={"payload": {"delta": "hello"}})
        yield SSEEvent(event="message_end", data={"payload": {}})

    ollie.stream_events = _stream

    await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["success"] == "true"
    assert p["completion_state"] == "message_end"
    assert int(p["pod_warmup_ms"]) >= 0
    assert int(p["total_duration_ms"]) >= 0
    assert int(p["event_count"]) == 2


@pytest.mark.anyio
async def test_pod_warmup_failure_emits_completed_error(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.wait_ready.side_effect = PodNotReadyError("boom")

    with pytest.raises(PodNotReadyError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    assert completed[0]["completion_state"] == "error"
    assert completed[0]["success"] == "false"


@pytest.mark.anyio
async def test_message_cancelled_state(recorder: _Recorder) -> None:
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[SSEEvent]:
        yield SSEEvent(event="message_cancelled", data={"payload": {}})

    ollie.stream_events = _stream
    await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert completed[0]["completion_state"] == "cancelled"
    assert completed[0]["success"] == "true"


@pytest.mark.anyio
async def test_discover_pod_auth_failure_emits_completed_error(recorder: _Recorder) -> None:
    """CometAuthError thrown from discover_pod (the very first network call)
    must still trigger the analytics finally — otherwise the most common
    real-world failure (bad/expired API key) is invisible to dashboards.

    Also pins the zero-event invariants: event_count=0 and ttfe=-1 because
    no SSE event ever arrived. A regression that initialised these to "1" or
    "0" instead of -1 would silently corrupt the "time to first event" metric.
    """
    comet = AsyncMock()
    comet.discover_pod.side_effect = CometAuthError("401")
    ollie = AsyncMock()  # never reached

    with pytest.raises(CometAuthError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["completion_state"] == "error"
    assert p["success"] == "false"
    assert p["event_count"] == "0"
    assert p["time_to_first_event_ms"] == "-1"
    # pod_warmup_ms is 0 because we never started warmup.
    assert p["pod_warmup_ms"] == "0"


@pytest.mark.anyio
async def test_create_session_auth_failure_emits_completed_error(recorder: _Recorder) -> None:
    """OllieAuthError at the create_session stage (after discovery and warmup
    succeeded) is its own production-realistic failure mode — the PPAUTH
    cookie expired between discovery and POST. Must still emit completed=error."""
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.side_effect = OllieAuthError("403")

    with pytest.raises(OllieAuthError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["completion_state"] == "error"
    assert p["success"] == "false"
    assert p["event_count"] == "0"
    assert p["time_to_first_event_ms"] == "-1"
    # Warmup completed before the failure — should record positive elapsed.
    assert int(p["pod_warmup_ms"]) >= 0


def _raise_wrapper_with_cause(wrapper: Exception, cause: Exception) -> Exception:
    """Materialize ``raise wrapper from cause`` and return the caught wrapper.

    Mirrors what ``ollie_client.py`` and ``ask_ollie.py`` do at every SSE
    error frame and HTTP error — using a real ``raise … from`` keeps the
    ``__cause__`` / ``__suppress_context__`` slots set the way Python does.
    """
    try:
        raise wrapper from cause
    except Exception as e:
        return e


@pytest.mark.anyio
async def test_ollie_stream_error_wrapping_upstream_5xx_unwraps_to_cause(
    recorder: _Recorder,
) -> None:
    """Production shape: the pod surfaces an upstream 500 as an ``error`` SSE
    frame; ``ask_ollie.py`` raises ``OllieStreamError`` with the real cause
    chained. The analytics emit must bucket by the cause (``upstream_5xx``)
    and stash both the wrapper class name (``OllieStreamError`` — where in our
    code the failure surfaced) and the leaf class (``OpikServerError`` — what
    actually broke)."""
    chained = _raise_wrapper_with_cause(
        OllieStreamError("pod error frame"),
        OpikServerError("backend exploded"),
    )
    comet = AsyncMock()
    comet.discover_pod.side_effect = chained
    ollie = AsyncMock()

    with pytest.raises(OllieStreamError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["success"] == "false"
    assert p["error_kind"] == "upstream_5xx"
    assert p["exception_type"] == "OllieStreamError"
    assert p["cause_type"] == "OpikServerError"


@pytest.mark.anyio
async def test_bare_ollie_stream_error_emits_no_cause_type(recorder: _Recorder) -> None:
    """A bare ``OllieStreamError`` (e.g. ``ollie_client.py:132`` raising
    'POST /sessions returned no session_id') has no upstream cause. The
    bucket stays ``unknown`` — same as pre-unwrap — and ``cause_type`` is
    absent, signalling 'no recoverable upstream' to dashboards rather than
    misleading them with a leaf class that wasn't really the failure."""
    comet = AsyncMock()
    comet.discover_pod.side_effect = OllieStreamError("no session_id")
    ollie = AsyncMock()

    with pytest.raises(OllieStreamError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    p = completed[0]
    assert p["error_kind"] == "unknown"
    assert p["exception_type"] == "OllieStreamError"
    assert "cause_type" not in p


@pytest.mark.anyio
async def test_ollie_stream_error_wrapping_auth_unwraps_to_cause(recorder: _Recorder) -> None:
    """A realistic mid-stream failure: PPAUTH expires between session create
    and stream start, surfacing as ``OllieStreamError from OpikAuthError``.
    Must route to ``auth`` so dashboards page the user-config gauge, not the
    server-bug gauge."""
    chained = _raise_wrapper_with_cause(
        OllieStreamError("stream rejected mid-flight"),
        OpikAuthError("ppauth expired"),
    )
    comet = AsyncMock()
    comet.discover_pod.side_effect = chained
    ollie = AsyncMock()

    with pytest.raises(OllieStreamError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    p = completed[0]
    assert p["error_kind"] == "auth"
    assert p["exception_type"] == "OllieStreamError"
    assert p["cause_type"] == "OpikAuthError"


@pytest.mark.anyio
async def test_host_cancellation_emits_cancelled_state(recorder: _Recorder) -> None:
    """Host-level CancelledError (injected from outside the task group) must be
    classified as 'cancelled', not 'error'."""
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    # Simulate host cancellation arriving during wait_ready
    ollie.wait_ready.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    assert p["completion_state"] == "cancelled"
    assert p["success"] == "true"  # cancellation is not an error


@pytest.mark.anyio
async def test_stream_loop_wrapped_failure_preserves_cause_chain(
    recorder: _Recorder,
) -> None:
    """Production raise site: ``ollie_client.stream_events`` raises
    ``OllieStreamError from OpikAuthError`` from inside the anyio task group.
    The BaseExceptionGroup unwrap at ask_ollie.py:615 used to re-raise with
    ``from None`` which clobbered ``__cause__`` — making every wrapped
    failure look like ``unknown / OllieStreamError`` in BI.

    This pins the corrected behavior: the cause chain MUST survive the
    task-group unwrap so analytics can route on the leaf class."""
    comet = AsyncMock()
    comet.discover_pod.return_value = PodDiscovery(compute_url="http://c", ppauth="p")
    ollie = AsyncMock()
    ollie.create_session.return_value = "sess-1"

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[SSEEvent]:
        # Yield once so the loop enters; then raise the production-shape
        # chained exception. The raise happens INSIDE the task group, so
        # anyio wraps it in a BaseExceptionGroup — that's the path we need
        # to exercise to verify the cause-preservation fix.
        yield SSEEvent(event="message_delta", data={"payload": {"delta": "hi"}})
        try:
            raise OpikAuthError("ppauth expired mid-stream")
        except OpikAuthError as inner:
            raise OllieStreamError("stream rejected") from inner

    ollie.stream_events = _stream

    with pytest.raises(OllieStreamError):
        await _run_with(comet, ollie, recorder)

    completed = [p for et, p in recorder.events if et == EVENT_ASK_OLLIE_COMPLETED]
    assert len(completed) == 1
    p = completed[0]
    # The whole point of preserving __cause__: a wrapped auth failure must
    # bucket as "auth" (so dashboards flag a user-config issue), not the
    # opaque "unknown" the bug used to produce.
    assert p["error_kind"] == "auth"
    assert p["exception_type"] == "OllieStreamError"
    assert p["cause_type"] == "OpikAuthError"
    assert p["success"] == "false"
