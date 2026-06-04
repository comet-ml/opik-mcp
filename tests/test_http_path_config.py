"""OPIK_MCP_HTTP_PATH mounts the streamable transport at the path the advertised
resource URI uses — needed behind a non-rewriting path-prefix proxy (e.g. an
ALB routing /opik/api/v1/mcp straight to the Service), where the served path
and the advertised `resource` must match.

The build_app wiring (mcp.settings.streamable_http_path = get_settings()...) is
exercised by the session-scoped http_client fixture at the default "/mcp"
(see test_http_auth). A second build_app() can't run in-process — the FastMCP
session manager is a process-level singleton — so here we cover the config
surface directly.
"""

import pytest
from pydantic import ValidationError

from opik_mcp.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()


def test_http_path_defaults_to_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_HTTP_PATH", raising=False)
    assert Settings().opik_mcp_http_path == "/mcp"


def test_http_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_HTTP_PATH", "/opik/api/v1/mcp")
    assert Settings().opik_mcp_http_path == "/opik/api/v1/mcp"


def test_http_path_requires_leading_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_HTTP_PATH", "opik/api/v1/mcp")
    with pytest.raises(ValidationError):
        Settings()
