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
        target_tool="add_score",
        summary="add a score",
        input={"value": 0.9},
    )

    events = [(et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL]
    assert len(events) == 1
    _, props = events[0]
    assert props["tool"] == "ask_ollie"
    assert props["target_tool"] == "add_score"
    assert props["had_summary"] == "true"


def test_write_auto_approval_missing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: recorder)

    write_auto_approval(
        workspace="ws",
        session_id="sess",
        tool_use_id="tu-1",
        target_tool="add_score",
        summary=None,
        input={},
    )
    _, props = next((et, p) for et, p in recorder.events if et == EVENT_AUTO_APPROVAL)
    assert props["had_summary"] == "false"
