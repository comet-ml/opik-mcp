from types import SimpleNamespace

import pytest

from opik_mcp.analytics import EVENT_AUTO_APPROVAL
from opik_mcp.audit import write_auto_approval


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


def test_write_auto_approval_emits_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="score.create",
        summary="add a score",
        input={"value": 0.9},
    )

    events = [(et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL]
    assert len(events) == 1
    _, props = events[0]
    assert props["tool"] == "ask_ollie"
    assert props["target_tool"] == "score.create"
    assert props["had_summary"] == "true"


def test_write_auto_approval_missing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="score.create",
        summary=None,
        input={},
    )
    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["had_summary"] == "false"


def test_write_auto_approval_stamps_host_context_with_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session-context block (mcp_host, host_llm_family, env keys)
    must be stamped on EVENT_AUTO_APPROVAL so BI can segment auto-approval
    rate on the same dimensions as tool_called / ask_ollie_completed."""
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    client_info = SimpleNamespace(name="claude-desktop", version="1.2.3")
    params = SimpleNamespace(
        clientInfo=client_info, protocolVersion="2025-06-01", capabilities=None
    )
    session = SimpleNamespace(client_params=params)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="score.create",
        summary="add a score",
        input={"value": 0.9},
        mcp_session=session,
    )

    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["mcp_host"] == "claude-desktop"
    assert props["host_llm_family"] == "anthropic"
    for key in ("is_ci", "is_container", "launch_method", "install_id_freshly_generated"):
        assert key in props


def test_write_auto_approval_buckets_unknown_target_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``target_tool`` is the pod-controlled ``tool_name`` from the
    ``confirm_required`` SSE frame — a future pod release that ships
    arbitrary strings must NOT blow up event cardinality. Anything outside
    the WRITE_OPERATIONS allowlist collapses to ``"other"``."""
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    canary = "pod-internal-handler-name-9b2a-not-an-op"
    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool=canary,
        summary=None,
        input={},
    )

    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["target_tool"] == "other"
    assert canary not in props.values()


def test_write_auto_approval_passes_through_known_target_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowlisted write operations pass through to BI unchanged so
    dashboards can split auto-approval rate by operation."""
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="score.create",
        summary=None,
        input={},
    )

    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["target_tool"] == "score.create"


def test_write_auto_approval_falls_back_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None session must not break the emit — host fields fall back to
    the ``other``/``unknown`` defaults so the schema stays stable."""
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="score.create",
        summary=None,
        input={},
        mcp_session=None,
    )

    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"
