"""Unit tests for analytics/boot_props.py — settings-derived boot properties.

Pure functions over a ``Settings`` instance: deterministic because init kwargs
win over env in pydantic-settings, and ``_env_file=None`` disables dotenv. The
single source of truth for the new server_started / startup_error / auth_rejected
properties, called identically from __main__.main() and the build_app() lifespan.
"""

from __future__ import annotations

from typing import get_args

import pytest

from opik_mcp.analytics import boot_props
from opik_mcp.analytics.events import AuthMode, InstallationType, ResourceUriScheme
from opik_mcp.config import Settings


def _settings(**kwargs: object) -> Settings:
    # _env_file=None: ignore any developer .env so these stay deterministic.
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_default_allowed_hosts_schema_parity() -> None:
    # Read the schema default (NOT Settings() — that would pick up env overrides
    # and make the constant track a live instance instead of the declared default).
    assert (
        Settings.model_fields["opik_mcp_allowed_hosts"].default
        == boot_props._DEFAULT_ALLOWED_HOSTS
    )


def test_installation_type_self_hosted_uses_hyphen() -> None:
    result = boot_props.installation_type(_settings(opik_url="https://opik.acme.internal"))
    # Canonical value is "self-hosted" (hyphen) to match error_tracking; an
    # underscore here would silently break BI filters built against the Sentry tag.
    assert result == "self-hosted"
    assert "_" not in result


def test_installation_type_cloud() -> None:
    s = _settings(opik_url=None, comet_url_override="https://www.comet.com")
    assert boot_props.installation_type(s) == "cloud"


def test_installation_type_local() -> None:
    assert boot_props.installation_type(_settings(opik_url="http://localhost:5173")) == "local"


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("https://www.comet.com/api/v1/mcp", "https"),
        ("http://localhost:8080/mcp", "http"),
        (None, "none"),
        ("", "none"),
        ("ftp://example.com", "none"),
        # Scheme-less value: TLS is assumed for a public ingress, so it reports https.
        ("www.comet.com/api/v1/mcp", "https"),
    ],
)
def test_resource_uri_scheme(uri: str | None, expected: str) -> None:
    assert boot_props.resource_uri_scheme(_settings(opik_mcp_resource_uri=uri)) == expected


def test_auth_mode_at_boot_api_key_wins_over_oauth() -> None:
    s = _settings(opik_api_key="sk-x", opik_mcp_as_url="https://as.example.com")
    assert boot_props.auth_mode_at_boot(s) == "api_key"


def test_auth_mode_at_boot_oauth_when_only_as_url() -> None:
    s = _settings(opik_api_key=None, opik_mcp_as_url="https://as.example.com")
    assert boot_props.auth_mode_at_boot(s) == "oauth"


def test_auth_mode_at_boot_none_when_neither() -> None:
    s = _settings(opik_api_key=None, opik_mcp_as_url=None)
    assert boot_props.auth_mode_at_boot(s) == "none"


def test_allowed_hosts_is_default_true_for_shipped_default() -> None:
    assert boot_props.allowed_hosts_is_default(_settings()) == "true"


def test_allowed_hosts_is_default_false_for_custom() -> None:
    s = _settings(opik_mcp_allowed_hosts="www.comet.com,www.comet.com:*")
    assert boot_props.allowed_hosts_is_default(s) == "false"


def test_allowed_hosts_is_default_tolerates_spaces() -> None:
    # Whitespace around entries is cosmetic; an operator who pasted the default
    # with spaces still counts as "default" (not a deliberate hardening).
    s = _settings(opik_mcp_allowed_hosts="127.0.0.1:* , localhost:* , [::1]:*")
    assert boot_props.allowed_hosts_is_default(s) == "true"


def test_oauth_configured_bool_strings() -> None:
    assert boot_props.oauth_configured(_settings(opik_mcp_as_url="https://as")) == "true"
    assert boot_props.oauth_configured(_settings(opik_mcp_as_url=None)) == "false"


def test_dns_rebinding_protection_bool_string() -> None:
    on = _settings(opik_mcp_dns_rebinding_protection=True)
    off = _settings(opik_mcp_dns_rebinding_protection=False)
    assert boot_props.dns_rebinding_protection(on) == "true"
    assert boot_props.dns_rebinding_protection(off) == "false"


def test_oauth_configured_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPIK_MCP_AS_URL", raising=False)
    assert boot_props.oauth_configured_from_env() == "false"
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://as.example.com")
    assert boot_props.oauth_configured_from_env() == "true"


def test_collect_boot_props_keys_and_literal_membership() -> None:
    s = _settings(
        opik_api_key="sk",
        opik_mcp_as_url="https://as",
        opik_mcp_resource_uri="https://www.comet.com/api/v1/mcp",
    )
    props = boot_props.collect_boot_props(s)
    # installation_type is intentionally NOT here — _build_event's common block
    # is its single source of truth (stamped on every event).
    assert set(props) == {
        "oauth_configured",
        "resource_uri_scheme",
        "dns_rebinding_protection",
        "allowed_hosts_is_default",
        "auth_mode",
    }
    assert props["auth_mode"] in get_args(AuthMode)
    assert props["resource_uri_scheme"] in get_args(ResourceUriScheme)
    for key in ("oauth_configured", "dns_rebinding_protection", "allowed_hosts_is_default"):
        assert props[key] in {"true", "false"}


def test_installation_type_self_hosted_hyphen() -> None:
    # The hyphen-vs-underscore contract is the highest-stakes invariant here.
    result = boot_props.installation_type(_settings(opik_url="https://opik.acme.internal"))
    assert result == "self-hosted"
    assert result in get_args(InstallationType)


def test_lifecycle_sentinel_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pins the contract the build_app() lifespan (Phase 4) depends on.
    monkeypatch.delenv(boot_props.LIFECYCLE_SENTINEL, raising=False)
    assert boot_props.lifecycle_owned_by_main() is False
    boot_props.mark_lifecycle_owned_by_main()
    assert boot_props.lifecycle_owned_by_main() is True
