import pytest

from opik_mcp.config import Settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPIK_API_KEY", "OPIK_WORKSPACE", "COMET_WORKSPACE", "COMET_URL_OVERRIDE"):
        monkeypatch.delenv(var, raising=False)


def test_opik_workspace_env_populates_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPIK_WORKSPACE is the primary (OPIK_-prefixed) env var for the workspace,
    matching the Opik SDK and the rest of opik-mcp's OPIK_ convention."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPIK_WORKSPACE", "ws-opik")
    s = Settings()
    assert s.comet_workspace == "ws-opik"


def test_opik_workspace_takes_precedence_over_comet_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both are set, the OPIK_-prefixed var wins; COMET_WORKSPACE is the
    deprecated fallback."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPIK_WORKSPACE", "ws-opik")
    monkeypatch.setenv("COMET_WORKSPACE", "ws-comet")
    s = Settings()
    assert s.comet_workspace == "ws-opik"


def test_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    s = Settings()
    assert s.opik_api_key is None
    assert s.comet_workspace is None
    assert s.comet_url_override == "https://www.comet.com"


def test_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_API_KEY", "k1")
    monkeypatch.setenv("COMET_WORKSPACE", "ws1")
    monkeypatch.setenv("COMET_URL_OVERRIDE", "https://dev.comet.com")
    s = Settings()
    assert s.opik_api_key == "k1"
    assert s.comet_workspace == "ws1"
    assert s.comet_url_override == "https://dev.comet.com"


def test_default_project_name_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_DEFAULT_PROJECT_NAME", "chatbot-prod")
    s = Settings()
    assert s.opik_default_project_name == "chatbot-prod"


def test_default_project_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_DEFAULT_PROJECT_NAME", raising=False)
    s = Settings()
    assert s.opik_default_project_name is None


def test_analytics_enabled_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_ENABLED", raising=False)
    assert Settings().opik_mcp_analytics_enabled is True


def test_analytics_disable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "false")
    assert Settings().opik_mcp_analytics_enabled is False


def test_analytics_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_URL", raising=False)
    assert Settings().opik_mcp_analytics_url == "https://stats.comet.com/notify/event/"


def test_analytics_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_ANALYTICS_ENVIRONMENT", raising=False)
    assert Settings().opik_mcp_analytics_environment == "prod"


def test_analytics_timeouts_have_sensible_defaults() -> None:
    s = Settings()
    assert s.opik_mcp_analytics_connect_timeout_s == 5.0
    assert s.opik_mcp_analytics_total_timeout_s == 10.0
