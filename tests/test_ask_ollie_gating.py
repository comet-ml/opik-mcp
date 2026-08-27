"""``ask_ollie`` is opt-in, and hidden rather than merely refused.

Why hidden: measured over 30 days it failed 90.6% of the time for real MCP
callers, and two slices of that CANNOT succeed however the backend behaves — a
caller with no credential fails in the config phase before any network call (120
calls, 60 installs, zero successes), and an on-prem deployment has no Ollie to
reach (28 calls, zero successes). Refusing at call time would still leave the
host advertising the tool, so an agent spends a turn discovering it cannot work.
Removing it from ``tools/list`` means the agent never sees it.

These tests exist because the gate is applied at STARTUP, not at import: the
tool registers unconditionally via ``@mcp.tool()`` and is unregistered by
``apply_tool_visibility``. A test that only imports the module would therefore
pass whether or not the gate works at all.
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
    """Re-register whatever the test removed.

    ``mcp`` is module-level and shared, so a test that unregisters a tool would
    otherwise leak that removal into every test that runs after it.
    """
    saved = dict(server.mcp._tool_manager._tools)
    yield
    server.mcp._tool_manager._tools.clear()
    server.mcp._tool_manager._tools.update(saved)


def test_ask_ollie_is_hidden_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DEFAULT. No opt-in means the agent never sees the tool."""
    monkeypatch.setattr(server, "get_settings", lambda: Settings())
    assert "ask_ollie" in _tool_names(), "precondition: registered at import"

    server.apply_tool_visibility(server.mcp)

    assert "ask_ollie" not in _tool_names()


def test_an_explicit_opt_in_keeps_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kill switch is reversible.

    Nothing we ship sets this today — hosted included — but the toggle has to
    actually restore the tool, or it is a deletion wearing a flag's clothes.
    """
    monkeypatch.setattr(server, "get_settings", lambda: Settings(opik_mcp_ask_ollie="enabled"))

    server.apply_tool_visibility(server.mcp)

    assert "ask_ollie" in _tool_names()


def test_no_other_tool_is_disturbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate governs exactly one tool; the rest of the surface is untouched."""
    monkeypatch.setattr(server, "get_settings", lambda: Settings())
    before = _tool_names()

    server.apply_tool_visibility(server.mcp)

    assert before - _tool_names() == {"ask_ollie"}


def test_applying_the_gate_twice_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both startup paths call it, and ``remove_tool`` raises on an unknown name.

    A server must never fail to boot over a tool we did not want anyway.
    """
    monkeypatch.setattr(server, "get_settings", lambda: Settings())

    server.apply_tool_visibility(server.mcp)
    server.apply_tool_visibility(server.mcp)  # must be a no-op, not a crash

    assert "ask_ollie" not in _tool_names()


def test_a_typo_fails_loudly_instead_of_silently_hiding_the_tool() -> None:
    """ "disable" / "off" must not be read as an opt-in OR a silent opt-out."""
    for typo in ("disable", "off", "true", "ENABLED "):
        with pytest.raises(ValidationError):
            Settings(opik_mcp_ask_ollie=typo)  # type: ignore[arg-type]


def test_the_boot_event_reports_whether_the_tool_is_advertised() -> None:
    """Otherwise a chart that failed to apply looks like a chart nobody used.

    "No ask_ollie calls" is ambiguous between "disabled" and "enabled but
    untouched"; this flag separates them, which is what makes the hosted
    rollout verifiable.
    """

    def props(settings: Settings) -> dict[str, str]:
        return boot_props.server_started_props(
            settings, fingerprint_props={}, lifecycle_source="main"
        )

    off = props(Settings())
    on = props(Settings(opik_mcp_ask_ollie="enabled"))

    assert off["ask_ollie_enabled"] == "false"
    assert on["ask_ollie_enabled"] == "true"
