import pytest

from opik_mcp.config import MissingConfigError, Settings, require_ollie_config


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPIK_API_KEY", "COMET_WORKSPACE", "COMET_URL_OVERRIDE"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    s = Settings()
    assert s.opik_api_key is None
    assert s.comet_workspace is None
    assert s.comet_url_override == "https://www.comet.com"
    assert s.opik_mcp_pod_ready_timeout_s == 120
    assert s.opik_mcp_pod_ready_interval_s == 2


def test_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_API_KEY", "k1")
    monkeypatch.setenv("COMET_WORKSPACE", "ws1")
    monkeypatch.setenv("COMET_URL_OVERRIDE", "https://dev.comet.com")
    s = Settings()
    assert s.opik_api_key == "k1"
    assert s.comet_workspace == "ws1"
    assert s.comet_url_override == "https://dev.comet.com"


def test_require_ollie_config_returns_pair() -> None:
    s = Settings(opik_api_key="k", comet_workspace="w")
    assert require_ollie_config(s) == ("k", "w")


def test_require_ollie_config_missing_api_key() -> None:
    s = Settings(opik_api_key=None, comet_workspace="w")
    with pytest.raises(MissingConfigError, match="OPIK_API_KEY"):
        require_ollie_config(s)


def test_require_ollie_config_missing_workspace() -> None:
    s = Settings(opik_api_key="k", comet_workspace=None)
    with pytest.raises(MissingConfigError, match="COMET_WORKSPACE"):
        require_ollie_config(s)


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


# --- opik_mcp_auto_approve validator ---


@pytest.mark.parametrize("raw", ["DISABLED", "Disabled", "disabled"])
def test_auto_approve_case_insensitive(raw: str) -> None:
    """Shell envs vary on capitalization; the validator must normalise.

    Without the lowercase validator, `Literal["enabled", "disabled"]` would
    reject `"DISABLED"` at Settings construction — that's a worse UX than
    quietly normalising.
    """
    # mypy sees the Literal["enabled", "disabled"] field type and rejects
    # "DISABLED" — but the whole point of this test is to prove the runtime
    # validator accepts it. Ignore the static check at the call site.
    s = Settings(opik_api_key="k", comet_workspace="w", opik_mcp_auto_approve=raw)  # type: ignore[arg-type]
    assert s.opik_mcp_auto_approve == "disabled"


def test_auto_approve_default_is_enabled() -> None:
    """Production default is YOLO ON — changing this is a breaking change
    requiring an ADR update, so make it impossible to do silently."""
    assert Settings(opik_api_key="k", comet_workspace="w").opik_mcp_auto_approve == "enabled"


def test_auto_approve_rejects_typo() -> None:
    """A typo like "off"/"disable" must fail loudly at construction — otherwise
    the user thinks they opted out while auto-approval is still on."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(opik_api_key="k", comet_workspace="w", opik_mcp_auto_approve="off")  # type: ignore[arg-type]
