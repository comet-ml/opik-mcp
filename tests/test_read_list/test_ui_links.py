"""UI link helpers — the Opik UI base and the session's workspace name.

These feed both the instructions blob and the links a read attaches, so the
two can never disagree about where the UI lives.
"""

from __future__ import annotations

from opik_mcp.auth_context import inbound_workspace, resolved_workspace_name
from opik_mcp.config import Settings
from opik_mcp.read_list.ui_links import current_workspace, opik_ui_base


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "opik_api_key": "k",
        "comet_workspace": "demo-ws",
        "opik_url": "https://opik.test/api/",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_ui_base_strips_api_suffix_and_trailing_slash() -> None:
    assert opik_ui_base(_settings()) == "https://opik.test"


def test_ui_base_derives_from_comet_url_override() -> None:
    s = _settings(opik_url=None, comet_url_override="https://demo.comet.com/")
    assert opik_ui_base(s) == "https://demo.comet.com/opik"


def test_ui_base_none_when_unconfigured() -> None:
    assert opik_ui_base(_settings(opik_url=None, comet_url_override="")) is None


def test_workspace_falls_back_to_settings_then_default() -> None:
    assert current_workspace(_settings()) == "demo-ws"
    assert current_workspace(_settings(comet_workspace=None)) == "default"


def test_workspace_prefers_inbound_header_then_resolved_name() -> None:
    tok_resolved = resolved_workspace_name.set("oauth-ws")
    try:
        assert current_workspace(_settings()) == "oauth-ws"
        tok_inbound = inbound_workspace.set("header-ws")
        try:
            assert current_workspace(_settings()) == "header-ws"
        finally:
            inbound_workspace.reset(tok_inbound)
    finally:
        resolved_workspace_name.reset(tok_resolved)
