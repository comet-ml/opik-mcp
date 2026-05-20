"""Tests for write-tool elicitation wiring (OPIK-6567).

Drives `opik_mcp.server.write` end-to-end with the dispatcher monkeypatched
out. The combinations matter: with `OPIK_MCP_CONFIRM_WRITES` defaulting to
`disabled`, a regression that ALWAYS elicited (or never did) would survive
the helper's unit tests; these pin the toggle's actual effect on the
user-visible tool surface.
"""

from __future__ import annotations

from typing import Any

import pytest

from opik_mcp import server
from opik_mcp.config import get_settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- Fakes -------------------------------------------------------------- #


class _FakeSession:
    def __init__(self, *, supports: bool) -> None:
        self._supports = supports

    def check_client_capability(self, capability: Any) -> bool:
        return self._supports


class _FakeRequestContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session


class _AcceptedShape:
    def __init__(self, confirm: bool) -> None:
        self.confirm = confirm


class _Accepted:
    def __init__(self, confirm: bool) -> None:
        self.action = "accept"
        self.data = _AcceptedShape(confirm=confirm)


class _Declined:
    action = "decline"
    data = None


class _FakeContext:
    def __init__(self, *, session: _FakeSession, elicit_result: Any = None) -> None:
        self.request_context = _FakeRequestContext(session)
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.elicit_calls: list[str] = []
        self._elicit_result = elicit_result

    async def info(self, msg: str) -> None:
        self.infos.append(msg)

    async def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    async def elicit(self, *, message: str, schema: type) -> Any:
        self.elicit_calls.append(message)
        return self._elicit_result


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Any:
    """Settings are LRU-cached. The flag we toggle (`opik_mcp_confirm_writes`)
    is read inside the write tool via `get_settings()`, so a leaked cache from
    a previous test would silently invert the behavior of the next one."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _dispatch_succeeded() -> dict[str, Any]:
    return {"ok": True, "operation": "comment.create"}


async def _noop_result(_obj: dict[str, Any]) -> dict[str, Any]:
    return _obj


# --- Tests -------------------------------------------------------------- #


async def test_disabled_setting_skips_elicit_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default behavior: no toggle set, no prompt, dispatch fires."""
    monkeypatch.delenv("OPIK_MCP_CONFIRM_WRITES", raising=False)
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(session=_FakeSession(supports=True))
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        ctx=ctx,
    )
    assert result == _dispatch_succeeded()
    assert ctx.elicit_calls == []  # never prompted
    assert len(calls) == 1


async def test_enabled_accept_proceeds_with_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Accepted(confirm=True),
    )
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        ctx=ctx,
    )
    assert result["ok"] is True
    assert len(ctx.elicit_calls) == 1
    assert "comment.create" in ctx.elicit_calls[0]
    assert len(calls) == 1


async def test_enabled_decline_returns_cancelled_envelope_no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User said no → MUST NOT call the BE, and the envelope MUST be
    self-describing so the LLM knows not to silently retry."""
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Declined(),
    )
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        ctx=ctx,
    )
    assert result == {
        "ok": False,
        "cancelled": True,
        "operation": "comment.create",
        "reason": "user_denied",
        "batch": False,
    }
    assert calls == []  # crucial: BE never touched


async def test_enabled_accept_false_is_treated_as_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-suspenders for hosts that return action=accept with the
    form field literally set to false. Without this, a misbehaving host
    would let writes through under `confirm_writes=enabled`."""
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Accepted(confirm=False),
    )
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        ctx=ctx,
    )
    assert result["cancelled"] is True
    assert result["reason"] == "user_denied"
    assert calls == []


async def test_enabled_unsupported_host_warns_and_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host without the elicitation capability: never block the write, but
    leave a single warning in the conversation so the operator notices the
    safety toggle is effectively a no-op on this host."""
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(session=_FakeSession(supports=False))
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        ctx=ctx,
    )
    assert result["ok"] is True
    assert len(calls) == 1
    assert any("does not advertise" in w for w in ctx.warnings)
    assert ctx.elicit_calls == []  # we never reach ctx.elicit on unsupported


async def test_dry_run_never_elicits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run is by definition a no-op; bothering the user for it would be
    actively annoying in agent loops that probe shape before committing."""
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")
    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"dry_run": True, "would_call": {}}

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Declined(),  # would-be denial if ever called
    )
    result = await server.write(
        operation="comment.create",
        data={"target": "trace", "target_id": "x", "text": "hi"},
        dry_run=True,
        ctx=ctx,
    )
    assert result == {"dry_run": True, "would_call": {}}
    assert ctx.elicit_calls == []
    assert len(calls) == 1


async def test_batch_payload_renders_size_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For batch writes the prompt should hint at the count so the user
    has a chance to spot a runaway loop before approving."""
    monkeypatch.setenv("OPIK_MCP_CONFIRM_WRITES", "enabled")

    async def _fake_dispatch(**_kw: Any) -> dict[str, Any]:
        return _dispatch_succeeded()

    monkeypatch.setattr("opik_mcp.server.run_write", _fake_dispatch)

    ctx = _FakeContext(
        session=_FakeSession(supports=True),
        elicit_result=_Declined(),
    )
    payload = [{"name": f"t-{i}"} for i in range(5)]
    result = await server.write(
        operation="trace.create",
        data=payload,
        ctx=ctx,
    )
    assert result["cancelled"] is True
    assert result["batch"] is True
    assert any("batch of 5" in m for m in ctx.elicit_calls)
