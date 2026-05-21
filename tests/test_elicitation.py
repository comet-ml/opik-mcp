"""Unit tests for the elicitation helper (OPIK-6567).

The helper sits between MCP's `Context.elicit` primitive and `ask_ollie`,
the sole caller in Phase 1. These tests pin three things:

* capability detection -- never surface a prompt to a host that didn't
  advertise the `elicitation` capability, never blow up when
  `request_context` is unset.
* action mapping -- the MCP spec uses `accept`/`decline`/`cancel`; the
  helper collapses that plus a timeout fallback into a 4-state
  `ElicitDecision`. The empty schema means the button press IS the
  answer; there's no inner form data to second-guess.
* timeout discipline -- the host has no obligation to bound the dialog,
  so the helper does it via `asyncio.wait_for`. Pinning that here keeps
  a regression from quietly removing the bound and pinning every host's
  tool-call slot open.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from opik_mcp.elicitation import (
    ElicitDecision,
    confirm_with_user,
    host_supports_elicitation,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- Fakes -------------------------------------------------------------- #


class _FakeSession:
    def __init__(self, *, supports: bool) -> None:
        self._supports = supports
        self.probed_with: Any = None

    def check_client_capability(self, capability: Any) -> bool:
        self.probed_with = capability
        return self._supports


class _FakeRequestContext:
    def __init__(self, session: _FakeSession | None) -> None:
        self.session = session


class _Accepted:
    action = "accept"
    data = None


class _Declined:
    action = "decline"
    data = None


class _Cancelled:
    action = "cancel"
    data = None


class _FakeContext:
    """Stand-in for fastmcp.Context that records elicit calls + canned reply."""

    def __init__(
        self,
        *,
        session: _FakeSession | None,
        elicit_result: Any = None,
        elicit_sleep_s: float = 0.0,
        elicit_raises: BaseException | None = None,
    ) -> None:
        self.request_context = _FakeRequestContext(session)
        self._result = elicit_result
        self._sleep = elicit_sleep_s
        self._raises = elicit_raises
        self.elicit_calls: list[tuple[str, type]] = []

    async def elicit(self, *, message: str, schema: type) -> Any:
        self.elicit_calls.append((message, schema))
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises
        return self._result


# --- host_supports_elicitation ----------------------------------------- #


def test_host_supports_returns_true_when_capability_advertised() -> None:
    session = _FakeSession(supports=True)
    ctx = _FakeContext(session=session)
    assert host_supports_elicitation(ctx) is True  # type: ignore[arg-type]
    # The probe must check specifically for `elicitation` -- a regression
    # that asked for a different capability would silently disable every
    # caller of `confirm_with_user`.
    assert getattr(session.probed_with, "elicitation", None) is not None


def test_host_supports_returns_false_when_not_advertised() -> None:
    ctx = _FakeContext(session=_FakeSession(supports=False))
    assert host_supports_elicitation(ctx) is False  # type: ignore[arg-type]


def test_host_supports_returns_false_when_request_context_missing() -> None:
    class _BrokenCtx:
        @property
        def request_context(self) -> Any:
            raise AttributeError("not in a request")

    assert host_supports_elicitation(_BrokenCtx()) is False  # type: ignore[arg-type]


def test_host_supports_swallows_probe_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exploding host shouldn't crash the tool path -- log and fall back."""

    class _ExplodingSession:
        def check_client_capability(self, capability: Any) -> bool:
            raise RuntimeError("boom")

    ctx = _FakeContext(session=_ExplodingSession())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="opik_mcp.elicitation"):
        assert host_supports_elicitation(ctx) is False  # type: ignore[arg-type]
    assert any("capability probe failed" in r.message for r in caplog.records)


# --- confirm_with_user: action mapping --------------------------------- #


async def test_confirm_accept_returns_accept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Accepted(),
    )
    with caplog.at_level(logging.INFO, logger="opik_mcp.elicitation"):
        outcome = await confirm_with_user(
            ctx,  # type: ignore[arg-type]
            prompt="ok?",
            timeout_s=5,
            tool="ask_ollie",
            entity_type="tool_use",
            entity_id="abc",
        )
    assert outcome.decision is ElicitDecision.ACCEPT
    assert outcome.decision.approved is True
    assert ctx.elicit_calls and ctx.elicit_calls[0][0] == "ok?"
    # Audit log shape -- operators grep on `event=elicitation`.
    line = "\n".join(r.message for r in caplog.records)
    assert "event=elicitation" in line
    assert "tool=ask_ollie" in line
    assert "decision=accept" in line


async def test_confirm_decline_returns_deny() -> None:
    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Declined(),
    )
    outcome = await confirm_with_user(
        ctx,  # type: ignore[arg-type]
        prompt="ok?",
        timeout_s=5,
        tool="write",
        entity_type="comment.create",
        entity_id=None,
    )
    assert outcome.decision is ElicitDecision.DENY


async def test_confirm_cancel_returns_cancel() -> None:
    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Cancelled(),
    )
    outcome = await confirm_with_user(
        ctx,  # type: ignore[arg-type]
        prompt="ok?",
        timeout_s=5,
        tool="write",
        entity_type="comment.create",
        entity_id=None,
    )
    assert outcome.decision is ElicitDecision.CANCEL


async def test_confirm_unknown_action_treated_as_cancel() -> None:
    """A future MCP spec bump might introduce a new action; we MUST default
    to the safest interpretation rather than accidentally treating it as an
    approve."""

    class _Weird:
        action = "future-action"
        data = None

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Weird(),
    )
    outcome = await confirm_with_user(
        ctx,  # type: ignore[arg-type]
        prompt="ok?",
        timeout_s=5,
        tool="write",
        entity_type="score.create",
        entity_id=None,
    )
    assert outcome.decision is ElicitDecision.CANCEL


# --- confirm_with_user: capability + timeout --------------------------- #


async def test_confirm_unsupported_skips_elicit_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hosts that didn't advertise must NEVER see `ctx.elicit` -- otherwise
    we'd raise a spec-violation every time ask_ollie wants a confirmation."""
    ctx = _FakeContext(
        session=_FakeSession(supports=False),
        elicit_result=_Accepted(),  # would-be ACCEPT if called
    )
    with caplog.at_level(logging.INFO, logger="opik_mcp.elicitation"):
        outcome = await confirm_with_user(
            ctx,  # type: ignore[arg-type]
            prompt="ok?",
            timeout_s=5,
            tool="write",
            entity_type="score.create",
            entity_id="abc",
        )
    assert outcome.decision is ElicitDecision.UNSUPPORTED
    assert outcome.latency_ms == 0
    assert ctx.elicit_calls == []
    assert any("decision=unsupported" in r.message for r in caplog.records)


async def test_confirm_timeout_returns_cancel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """asyncio.wait_for fallback -- without this, a host that opens the
    dialog and walks away pins our tool-call slot for the host's default
    ceiling (often minutes)."""
    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_sleep_s=10.0,  # far longer than the timeout below
        elicit_result=_Accepted(),
    )
    with caplog.at_level(logging.INFO, logger="opik_mcp.elicitation"):
        outcome = await confirm_with_user(
            ctx,  # type: ignore[arg-type]
            prompt="ok?",
            timeout_s=0.05,
            tool="write",
            entity_type="score.create",
            entity_id=None,
        )
    assert outcome.decision is ElicitDecision.CANCEL
    # The operator-visible distinction between timeout and explicit cancel
    # lives in the log line, not the outcome dataclass.
    assert any("reason=timeout" in r.message for r in caplog.records)


async def test_confirm_timeout_zero_means_unbounded() -> None:
    """`timeout_s=0` disables the wait_for bound -- escape hatch for
    interactive debugging. Pin so a refactor doesn't quietly turn 0 into
    'immediate timeout' (the asyncio.wait_for(0) interpretation)."""
    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Accepted(),
    )
    outcome = await confirm_with_user(
        ctx,  # type: ignore[arg-type]
        prompt="ok?",
        timeout_s=0,
        tool="write",
        entity_type="score.create",
        entity_id=None,
    )
    assert outcome.decision is ElicitDecision.ACCEPT
