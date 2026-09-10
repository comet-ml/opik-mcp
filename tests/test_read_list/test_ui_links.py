"""UI link helpers — the Opik UI base and the session's workspace name.

These feed both the instructions blob and the links a read attaches, so the
two can never disagree about where the UI lives.
"""

from __future__ import annotations

import base64

from opik_mcp.auth_context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    inbound_authorization,
    inbound_workspace,
    resolved_workspace_name,
)
from opik_mcp.config import Settings
from opik_mcp.read_list.ui_links import (
    current_workspace,
    link_workspace,
    opik_ui_base,
    trace_link_template,
)


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


def test_link_workspace_is_none_for_oauth_bearer_with_unknown_workspace() -> None:
    """Under an OAuth bearer the workspace lives server-side. If neither the
    header nor introspection named it, a link built from the static settings
    fallback would point at the wrong workspace — so no link at all."""
    tok = inbound_authorization.set(f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc")
    try:
        assert link_workspace(_settings()) is None
        # ...but the instructions blob keeps its best-effort name.
        assert current_workspace(_settings()) == "demo-ws"
        tok_res = resolved_workspace_name.set("oauth-ws")
        try:
            assert link_workspace(_settings()) == "oauth-ws"
        finally:
            resolved_workspace_name.reset(tok_res)
    finally:
        inbound_authorization.reset(tok)


def test_link_workspace_trusts_inbound_header_under_oauth() -> None:
    """An explicit Comet-Workspace header is cross-checked against the token
    server-side, so it is a safe name to link with even under OAuth."""
    tok_auth = inbound_authorization.set(f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc")
    tok_ws = inbound_workspace.set("header-ws")
    try:
        assert link_workspace(_settings()) == "header-ws"
    finally:
        inbound_workspace.reset(tok_ws)
        inbound_authorization.reset(tok_auth)


def test_link_workspace_uses_settings_for_api_key_sessions() -> None:
    tok = inbound_authorization.set("plain-api-key")
    try:
        assert link_workspace(_settings()) == "demo-ws"
    finally:
        inbound_authorization.reset(tok)
    assert link_workspace(_settings()) == "demo-ws"


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


def test_trace_link_template_is_the_backend_redirect_with_one_slot() -> None:
    """The redirect resolves the project and the workspace from the trace id
    itself, so the agent fills a single slot and needs neither."""
    template = trace_link_template(_settings())
    path = base64.urlsafe_b64encode(b"https://opik.test/api").decode().rstrip("=")
    assert template == (
        f"https://opik.test/api/v1/session/redirect/projects/?trace_id={{trace_id}}&path={path}"
    )


def test_trace_link_template_survives_an_unknown_workspace() -> None:
    """The reason for preferring the redirect over a direct project URL: under
    an OAuth bearer whose workspace was never named, ``project_page_url``
    cannot build a link, but this one still can."""
    token = inbound_authorization.set(f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc")
    try:
        assert link_workspace(_settings()) is None
        assert trace_link_template(_settings()) is not None
    finally:
        inbound_authorization.reset(token)


def test_trace_link_template_none_when_opik_is_unconfigured() -> None:
    assert trace_link_template(_settings(opik_url=None, comet_url_override="")) is None


def test_trace_link_template_none_without_an_api_segment() -> None:
    """opik-backend derives the UI base by cutting the decoded path at ``/api``
    and throws when there is none, so a base without it cannot be linked."""
    assert trace_link_template(_settings(opik_url="https://opik.test")) is None
