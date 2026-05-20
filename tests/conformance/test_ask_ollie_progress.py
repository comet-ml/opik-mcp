"""Conformance — `ask_ollie` progress frames are strictly monotonic.

MCP spec §Lifecycle/Timeouts: every `notifications/progress` frame in a
single tool call MUST have a strictly increasing `progress` value.
Strict hosts (Cursor, MCP Inspector strict mode) DROP THE CONNECTION on
a decrease — the symptom is a silent stall the user can't debug.

`ask_ollie` mixes progress from three concurrent sources (warmup
ticks, SSE events, idle heartbeat) into a single shared counter. Any
future refactor that splits the counter, re-bases the SSE loop at 1,
or re-orders the counter increment with the `report_progress` await
will regress this contract. This test is the only one in CI that
exercises the merged sequence, so it's load-bearing for the strict-host
matrix in docs/host-conformance.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from opik_mcp.ask_ollie import run_ask_ollie
from opik_mcp.comet_client import PodDiscovery
from opik_mcp.config import Settings
from opik_mcp.ollie_client import OnTick, SSEEvent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- Recording context ------------------------------------------------- #


class _ProgressRecorder:
    """Duck-typed Context that records every `report_progress(progress=…)` call.

    We don't care about `info` / `warning` here — those don't carry a
    progress value and the spec doesn't constrain their ordering."""

    def __init__(self) -> None:
        self.progress: list[float] = []

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.progress.append(progress)

    async def info(self, _msg: str) -> None:
        pass

    async def warning(self, _msg: str) -> None:
        pass


# --- Fake clients (warmup + SSE) --------------------------------------- #


class _StubCometClient:
    def __init__(self, discovery: PodDiscovery) -> None:
        self._discovery = discovery

    async def discover_pod(self, _workspace: str) -> PodDiscovery:
        return self._discovery


class _TickingOllieClient:
    """Fires N warmup ticks before reporting ready, then yields a mixed
    event stream — text deltas + a tool-call pair + message_end.

    The point is to exercise the three counter sources (warmup, SSE,
    heartbeat) in one run. Heartbeat fires only on idle, so we don't
    rely on it firing here — the SSE+warmup pair alone is enough to
    pin the cross-phase monotonicity that broke historically."""

    def __init__(self, *, warmup_ticks: int, events: list[SSEEvent]) -> None:
        self._warmup_ticks = warmup_ticks
        self._events = events
        self.confirms: list[tuple[str, str, str]] = []

    async def wait_ready(
        self, _compute_url: str, _ppauth: str, *, on_tick: OnTick | None = None
    ) -> None:
        if on_tick is None:
            return
        for i in range(self._warmup_ticks):
            # Pass a synthetic elapsed-seconds value; ask_ollie only uses
            # it to format the warmup message, not for monotonicity.
            await on_tick(float(i + 1))

    async def create_session(
        self,
        _compute_url: str,
        _ppauth: str,
        _workspace: str,
        _body: dict[str, Any],
    ) -> str:
        return "sess-monotonic"

    async def stream_events(
        self,
        _compute_url: str,
        _ppauth: str,
        _workspace: str,
        _session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        for evt in self._events:
            yield evt

    async def confirm_session(
        self,
        _compute_url: str,
        _ppauth: str,
        _workspace: str,
        session_id: str,
        *,
        tool_use_id: str,
        decision: str,
    ) -> None:
        self.confirms.append((session_id, tool_use_id, decision))


def _ev(event: str, payload: dict[str, Any]) -> SSEEvent:
    return SSEEvent(event=event, data={"parent_id": None, "payload": payload})


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "opik_api_key": "k",
        "comet_workspace": "ws",
        # Disable the heartbeat so this test stays deterministic — the
        # warmup + SSE pair already exercises the cross-source counter.
        # A separate test (heartbeat under idle) lives in test_ask_ollie.py.
        "opik_mcp_heartbeat_interval_s": 0,
    }
    base.update(overrides)
    return Settings(**base)


# --- Tests ------------------------------------------------------------- #


@pytest.mark.anyio
async def test_progress_values_are_strictly_increasing_across_warmup_and_sse() -> None:
    """The historical bug: warmup emitted `progress=30.0` (elapsed seconds)
    then the SSE loop restarted at `progress=1`, triggering a decrease.
    Today both share a counter — pin that here so a regression can't
    re-introduce the decrease silently."""
    comet = _StubCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _TickingOllieClient(
        warmup_ticks=3,
        events=[
            _ev("thinking_delta", {"delta": "thinking… "}),
            _ev("tool_call_start", {"display": "list_traces"}),
            _ev("tool_call_end", {"display": "list_traces"}),
            _ev("message_delta", {"delta": "Here you go."}),
            _ev("message_end", {}),
        ],
    )
    ctx = _ProgressRecorder()
    await run_ask_ollie(
        query="q",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
        ctx=ctx,  # type: ignore[arg-type]
    )
    # At minimum we saw the 3 warmup ticks + 5 SSE events = 8 progress
    # frames. An exact count is fragile (heartbeat could fire under load
    # even with interval=0 in the future), so we only assert the floor.
    assert len(ctx.progress) >= 8, (
        f"expected ≥8 progress frames (3 warmup + 5 SSE); got {ctx.progress}"
    )

    # The contract: strictly increasing across the entire run.
    for prev, curr in zip(ctx.progress, ctx.progress[1:], strict=False):
        assert curr > prev, (
            f"progress decreased or stalled: prev={prev} curr={curr} full_sequence={ctx.progress}"
        )


@pytest.mark.anyio
async def test_progress_values_are_integers_for_strict_hosts() -> None:
    """Cursor's strict mode rejects non-integer progress with a wire-level
    type error. We declare integer-only in the comment on
    `progress_counter`; pin it on the actual emitted values so that
    comment can't lie."""
    comet = _StubCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _TickingOllieClient(
        warmup_ticks=2,
        events=[_ev("message_delta", {"delta": "ok"}), _ev("message_end", {})],
    )
    ctx = _ProgressRecorder()
    await run_ask_ollie(
        query="q",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
        ctx=ctx,  # type: ignore[arg-type]
    )
    non_int = [p for p in ctx.progress if not isinstance(p, int) or isinstance(p, bool)]
    assert not non_int, f"non-integer progress values emitted (strict hosts will reject): {non_int}"


@pytest.mark.anyio
async def test_no_progress_emitted_when_ctx_is_absent() -> None:
    """Belt-and-suspenders: tools called without a Context (unit tests,
    direct dispatch) MUST NOT raise on the missing report_progress.
    If this regresses, every test in test_ask_ollie.py that omits ctx
    fails."""
    comet = _StubCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _TickingOllieClient(
        warmup_ticks=2,
        events=[_ev("message_end", {})],
    )
    # No ctx passed.
    result = await run_ask_ollie(
        query="q",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert result.complete is True
