"""``ask_ollie`` is off everywhere and hidden, not merely refused.

The gate runs at STARTUP, not import: the tool registers via ``@mcp.tool()`` and
is unregistered by ``apply_tool_visibility``. A test that only imports the module
would pass whether or not the gate works.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opik_mcp import server
from opik_mcp.analytics import boot_props
from opik_mcp.config import Settings


def _tool_names() -> set[str]:
    return {t.name for t in server.mcp._tool_manager.list_tools()}


@pytest.fixture(autouse=True)
def _restore_tool_registry() -> object:
    """``mcp`` is module-level, so a removal would leak into every later test."""
    saved = dict(server.mcp._tool_manager._tools)
    yield
    server.mcp._tool_manager._tools.clear()
    server.mcp._tool_manager._tools.update(saved)


def test_ask_ollie_is_hidden_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "get_settings", lambda: Settings())
    assert "ask_ollie" in _tool_names(), "precondition: registered at import"

    server.apply_tool_visibility(server.mcp)

    assert "ask_ollie" not in _tool_names()


def test_the_switch_is_reversible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing ships with this on, but it must still restore the tool -
    otherwise it is a deletion wearing a flag's clothes."""
    monkeypatch.setattr(server, "get_settings", lambda: Settings(opik_mcp_ask_ollie_enabled=True))

    server.apply_tool_visibility(server.mcp)

    assert "ask_ollie" in _tool_names()


def test_no_other_tool_is_disturbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "get_settings", lambda: Settings())
    before = _tool_names()

    server.apply_tool_visibility(server.mcp)

    assert before - _tool_names() == {"ask_ollie"}


def test_applying_the_gate_twice_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both startup paths call it, and ``remove_tool`` raises on an unknown name."""
    monkeypatch.setattr(server, "get_settings", lambda: Settings())

    server.apply_tool_visibility(server.mcp)
    server.apply_tool_visibility(server.mcp)

    assert "ask_ollie" not in _tool_names()


def test_the_old_string_spelling_fails_loudly() -> None:
    """This setting used to be Literal["enabled", "disabled"].

    As a bool those values are rejected, so a config carried over from the old
    spelling errors at startup instead of being silently read as false.
    """
    for stale in ("enabled", "disabled", "enable"):
        with pytest.raises(ValidationError):
            Settings(opik_mcp_ask_ollie_enabled=stale)  # type: ignore[arg-type]


def test_env_var_forms_map_the_safe_way() -> None:
    """A truthy value must be deliberate; the vague ones must land on off."""
    assert Settings(opik_mcp_ask_ollie_enabled="true").opik_mcp_ask_ollie_enabled is True  # type: ignore[arg-type]
    for falsey in ("false", "0", "no", "off"):
        assert Settings(opik_mcp_ask_ollie_enabled=falsey).opik_mcp_ask_ollie_enabled is False  # type: ignore[arg-type]


def test_the_boot_event_reports_whether_the_tool_is_advertised() -> None:
    """Otherwise "no ask_ollie calls" cannot be told from "tool was hidden"."""

    def props(settings: Settings) -> dict[str, str]:
        return boot_props.server_started_props(
            settings, fingerprint_props={}, lifecycle_source="main"
        )

    assert props(Settings())["ask_ollie_enabled"] == "false"
    assert props(Settings(opik_mcp_ask_ollie_enabled=True))["ask_ollie_enabled"] == "true"
