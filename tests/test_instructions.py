"""``InitializeResult.instructions`` template (ADR 0004 D6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.config import Settings
from opik_mcp.instructions import render_instructions
from opik_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "opik_api_key": "k",
        "comet_workspace": "demo-ws",
        "opik_url": "https://opik.test/",
    }
    base.update(overrides)
    return Settings(**base)


def test_render_substitutes_workspace_and_url() -> None:
    out = render_instructions(_settings())
    assert 'workspace "demo-ws"' in out
    assert "https://opik.test" in out
    assert out.count("https://opik.test") >= 1
    # Trailing slash stripped
    assert "https://opik.test/" not in out


def test_render_uses_comet_url_override_when_opik_url_missing() -> None:
    s = _settings(opik_url=None, comet_url_override="https://demo.comet.com/")
    out = render_instructions(s)
    assert "https://demo.comet.com/opik" in out


def test_render_uses_default_workspace_when_unset() -> None:
    """With no workspace configured the tools operate against "default"
    (Opik SDK convention), so the LLM-facing context must say so rather than
    "(workspace not configured)"."""
    from opik_mcp.config import DEFAULT_WORKSPACE

    s = _settings(comet_workspace=None)
    out = render_instructions(s)
    assert f'workspace "{DEFAULT_WORKSPACE}"' in out


def test_render_omits_user_clause_when_email_unknown() -> None:
    out = render_instructions(_settings())
    assert " as " not in out.split("Tool selection:")[0]


def test_render_includes_user_email_when_provided() -> None:
    out = render_instructions(_settings(), user_email="me@example.com")
    assert "as me@example.com" in out


def test_render_includes_today_date() -> None:
    out = render_instructions(_settings(), today=datetime(2026, 5, 15, tzinfo=UTC))
    assert "2026-05-15" in out


def test_render_mentions_tool_selection_guidance() -> None:
    """The blob's reason for existing is to prime tool routing."""
    out = render_instructions(_settings())
    assert "read" in out
    assert "list" in out
    assert "ask_ollie" in out


def test_render_includes_default_project_name_when_set() -> None:
    s = _settings(opik_default_project_name="chatbot-prod")
    out = render_instructions(s)
    assert "chatbot-prod" in out
    assert "default project" in out.lower()


def test_render_omits_default_project_when_unset() -> None:
    out = render_instructions(_settings())
    assert "default project" not in out.lower()


@pytest.mark.anyio
async def test_server_advertises_instructions_blob() -> None:
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        result = await session.initialize()
    assert result.instructions is not None
    assert "Opik" in result.instructions
    assert "Tool selection" in result.instructions
