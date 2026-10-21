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


# --- transport security (DNS-rebinding / Host-Origin allowlist) ------------- #


def test_transport_security_defaults_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPIK_MCP_DNS_REBINDING_PROTECTION",
        "OPIK_MCP_ALLOWED_HOSTS",
        "OPIK_MCP_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.opik_mcp_dns_rebinding_protection is True
    assert s.allowed_hosts_list == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    assert s.allowed_origins_list == ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def test_allowed_hosts_parses_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_ALLOWED_HOSTS", "dev.comet.com, dev.comet.com:*")
    assert Settings().allowed_hosts_list == ["dev.comet.com", "dev.comet.com:*"]


def test_allowed_origins_parses_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_ALLOWED_ORIGINS", "https://claude.ai")
    assert Settings().allowed_origins_list == ["https://claude.ai"]


def test_dns_rebinding_protection_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_DNS_REBINDING_PROTECTION", "false")
    assert Settings().opik_mcp_dns_rebinding_protection is False
