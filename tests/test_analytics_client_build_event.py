"""Direct unit tests for AnalyticsClient._build_event() per-request enrichment.

These call ``_build_event`` directly on a real (analytics-disabled, no worker)
client. This is the ONLY layer that can catch a raw-value leak INSIDE
``_build_event`` — the recorder-based tests in test_analytics_privacy.py
intercept at ``track_event`` and never see what ``_build_event`` builds.

``_build_event`` runs synchronously in the calling task (track_event builds then
enqueues), so the auth_context ContextVars are live at build time — that is what
lets per-request OAuth identity reach BI in hosted mode.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterator
from typing import Any, get_args

import pytest

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.auth_context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    inbound_authorization,
    inbound_workspace,
)
from opik_mcp.config import Settings

# Canaries: unique, greppable values that must never appear raw in an event.
RAW_OAUTH_TOKEN = f"{OAUTH_ACCESS_TOKEN_PREFIX}BEARER-CANARY-TOKEN-UNIQUE-7a3b2c1d"
RAW_WORKSPACE = "WORKSPACE-CANARY-NAME-MUST-NOT-LEAK-9f4e5a6b"


@pytest.fixture
def make_client() -> Iterator[Any]:
    created: list[AnalyticsClient] = []

    def _make(**kwargs: object) -> AnalyticsClient:
        # Pin auth-relevant fields to deterministic defaults (init kwargs win over
        # the developer's OPIK_API_KEY/OPIK_MCP_AS_URL env); a test overrides them
        # explicitly when it needs a key/AS set. analytics_enabled=False => no
        # worker thread; _build_event is pure.
        base: dict[str, object] = {
            "opik_mcp_analytics_enabled": False,
            "opik_api_key": None,
            "opik_mcp_as_url": None,
            "_env_file": None,
        }
        base.update(kwargs)
        client = AnalyticsClient(Settings(**base))  # type: ignore[arg-type]
        created.append(client)
        return client

    yield _make
    for client in created:
        client.close()


@contextlib.contextmanager
def _inbound(*, auth: str | None = None, workspace: str | None = None) -> Iterator[None]:
    a = inbound_authorization.set(auth)
    w = inbound_workspace.set(workspace)
    try:
        yield
    finally:
        inbound_authorization.reset(a)
        inbound_workspace.reset(w)


def test_oauth_token_sha256_hashed_not_raw(make_client: Any) -> None:
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}"):
        event = client._build_event("opik_mcp_tool_called", {"tool_name": "read"})
    props = event["event_properties"]
    assert RAW_OAUTH_TOKEN not in json.dumps(event)  # raw token never leaves the process
    assert props["auth_mode"] == "oauth"
    assert props["token_sha256"] == hashlib.sha256(RAW_OAUTH_TOKEN.encode("utf-8")).hexdigest()


def test_non_oauth_bearer_is_api_key_mode_without_token_hash(make_client: Any) -> None:
    client = make_client()
    with _inbound(auth="Bearer some-non-oauth-static-key"):
        props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["auth_mode"] == "api_key"
    assert "token_sha256" not in props  # only OAUTH_ACCESS_TOKEN_PREFIX-prefixed bearers are hashed


def test_request_workspace_plaintext_present(make_client: Any) -> None:
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}", workspace=RAW_WORKSPACE):
        props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    # Plaintext by design — matches the existing settings `workspace` posture
    # (workspace names are used as user_id in resolve_anonymous_id).
    assert props["request_workspace"] == RAW_WORKSPACE


def test_three_tier_merge_properties_wins_over_per_request(make_client: Any) -> None:
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}"):
        # A caller that supplies auth_mode in `properties` (e.g. server_started
        # spreading collect_boot_props in Phase 3) must win over the
        # contextvar-derived value, while token_sha256 (only in
        # _per_request_props) still rides along.
        props = client._build_event("opik_mcp_server_started", {"auth_mode": "api_key"})[
            "event_properties"
        ]
    assert props["auth_mode"] == "api_key"
    assert props["token_sha256"] == hashlib.sha256(RAW_OAUTH_TOKEN.encode("utf-8")).hexdigest()


def test_common_block_still_wins_over_per_request(make_client: Any) -> None:
    # The common block is authoritative for its keys; a stray per-request key
    # must never shadow it. transport is a common key.
    client = make_client(opik_mcp_transport="http")
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}"):
        props = client._build_event("opik_mcp_tool_called", {"transport": "stdio"})[
            "event_properties"
        ]
    assert props["transport"] == "http"  # common wins over caller properties


def test_no_per_request_fields_outside_request_context(make_client: Any) -> None:
    client = make_client()
    props = client._build_event("opik_mcp_server_started", {})["event_properties"]
    assert "token_sha256" not in props
    assert "request_workspace" not in props
    assert props["auth_mode"] == "none"  # settings-derived fallback, no env key


def test_stdio_auth_mode_api_key_when_env_key_set(make_client: Any) -> None:
    client = make_client(opik_api_key="sk-test")
    props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["auth_mode"] == "api_key"
    assert "token_sha256" not in props  # env key is hashed as api_key_sha256, not token


def test_empty_bearer_is_api_key_without_token_hash(make_client: Any) -> None:
    # An inbound bearer with an empty token is a (malformed) forwarded
    # credential -> api_key, never hashed. (In production BearerAuthMiddleware
    # 401s this before a tool runs; this just pins the enrichment is safe.)
    client = make_client()
    with _inbound(auth="Bearer    "):  # whitespace-only token
        props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["auth_mode"] == "api_key"
    assert "token_sha256" not in props


def test_oauth_token_extraction_matches_resolve_opik_config(make_client: Any) -> None:
    # Odd whitespace between scheme and token must still hash the same token
    # resolve_opik_config identifies (partition(" ") + lstrip), so token_sha256
    # stays a consistent BI join key.
    client = make_client()
    with _inbound(auth=f"Bearer  {RAW_OAUTH_TOKEN}"):  # two spaces
        props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["auth_mode"] == "oauth"
    assert props["token_sha256"] == hashlib.sha256(RAW_OAUTH_TOKEN.encode("utf-8")).hexdigest()


def test_installation_type_in_common_block(make_client: Any) -> None:
    from opik_mcp.analytics.events import InstallationType

    client = make_client()
    props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["installation_type"] in get_args(InstallationType)


def test_oauth_only_deploy_reports_oauth_when_no_inbound_credential(make_client: Any) -> None:
    # OAuth-only deploy (AS configured, no static key): a per-call event with no
    # inbound bearer must report auth_mode="oauth" (settings-derived), matching
    # auth_mode_at_boot — NOT the old 2-way "none" fallback.
    client = make_client(opik_api_key=None, opik_mcp_as_url="https://as.example.com")
    props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["auth_mode"] == "oauth"


def test_transport_lowercased_in_common_block(make_client: Any) -> None:
    # A mixed-case OPIK_MCP_TRANSPORT must still emit a canonical lowercase value.
    client = make_client(opik_mcp_transport="HTTP")
    props = client._build_event("opik_mcp_tool_called", {})["event_properties"]
    assert props["transport"] == "http"
