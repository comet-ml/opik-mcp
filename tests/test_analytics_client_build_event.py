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
    inbound_mcp_session_id,
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
def _inbound(
    *,
    auth: str | None = None,
    workspace: str | None = None,
    mcp_session_id: str | None = None,
) -> Iterator[None]:
    a = inbound_authorization.set(auth)
    w = inbound_workspace.set(workspace)
    s = inbound_mcp_session_id.set(mcp_session_id)
    try:
        yield
    finally:
        inbound_authorization.reset(a)
        inbound_workspace.reset(w)
        inbound_mcp_session_id.reset(s)


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
    # Plaintext by design — a workspace name is a tenant label, not a person.
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


# --- caller identity (the amended privacy contract) ---------------------- #
#
# events.py names this module as what enforces the identity rule. These tests
# call _build_event directly, which is the only way to catch a leak *inside* it:
# the recorder-based privacy suite intercepts at track_event and never sees what
# _build_event builds.


def test_login_is_emitted_plaintext_as_the_top_level_user_id(make_client: Any) -> None:
    """The sanctioned exception to "identity only as a digest".

    Hashing it would make it unjoinable — the warehouse's own user key is this
    same plaintext login — so the contract was amended rather than worked around.
    """
    from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX
    from opik_mcp.credential_identity import ResolvedIdentity, remember_identity

    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}build-event-token"
    remember_identity(
        token,
        ResolvedIdentity(user_name="awkoy", workspace_name="awkoy-v2", workspace_id="ws-1"),
    )
    client = make_client()
    with _inbound(auth=f"Bearer {token}"):
        event = client._build_event("opik_mcp_tool_called", {})

    assert event["user_id"] == "awkoy"
    assert event["event_properties"]["user_id_kind"] == "comet_user"


def test_the_login_never_leaks_into_any_other_field(make_client: Any) -> None:
    """Widening identity must not widen anything else: the login belongs in
    exactly one place, and nowhere in event_properties except its discriminator."""
    from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX
    from opik_mcp.credential_identity import ResolvedIdentity, remember_identity

    canary_login = "LOGIN-CANARY-MUST-APPEAR-ONLY-AS-USER-ID-5e1f7a"
    token = f"{OAUTH_ACCESS_TOKEN_PREFIX}leak-check-token"
    remember_identity(
        token,
        ResolvedIdentity(user_name=canary_login, workspace_name="ws", workspace_id=None),
    )
    client = make_client()
    with _inbound(auth=f"Bearer {token}"):
        event = client._build_event("opik_mcp_tool_called", {})

    assert event["user_id"] == canary_login
    assert canary_login not in json.dumps(event["event_properties"])


def test_the_raw_bearer_is_still_never_emitted_now_identity_rides_along(
    make_client: Any,
) -> None:
    """The pre-existing guarantee must survive the identity change."""
    from opik_mcp.credential_identity import ResolvedIdentity, remember_identity

    remember_identity(
        RAW_OAUTH_TOKEN,
        ResolvedIdentity(user_name="awkoy", workspace_name="ws", workspace_id=None),
    )
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}"):
        event = client._build_event("opik_mcp_tool_called", {})

    assert RAW_OAUTH_TOKEN not in json.dumps(event)


def test_unresolved_caller_reports_the_install_id_not_an_empty_field(
    make_client: Any,
) -> None:
    """The warehouse must see a real value or a null — never an empty string."""
    client = make_client()
    event = client._build_event("opik_mcp_tool_called", {})
    assert event["user_id"]
    assert event["event_properties"]["user_id_kind"] == "install_id"


# --- mcp_session_sha256: the stable unit the hosted funnel needs ----------- #
#
# Keyed on token_sha256, every hosted ratio was inversely correlated with usage:
# the OAuth access token lives one hour, a handshake recurs on every mint, a tool
# call does not. So an 8-hour session minted ~8 "authorized + connected" pairs and
# usually one "invoked" — the harder someone worked, the worse they scored. The
# session id survives token refreshes, so it counts that session ONCE.

RAW_MCP_SESSION_ID = "3f9a1c77-session-canary-0b42"


def test_mcp_session_sha256_hashed_not_raw(make_client: Any) -> None:
    """PRIVACY: the raw session id must never leave the process.

    Hashed rather than emitted plainly because possession of a session id plus a
    token addresses a live session, which makes it bearer-equivalent.
    """
    client = make_client()
    with _inbound(
        auth=f"Bearer {RAW_OAUTH_TOKEN}",
        mcp_session_id=RAW_MCP_SESSION_ID,
    ):
        event = client._build_event("opik_mcp_tool_called", {"tool_name": "read"})
    props = event["event_properties"]
    assert RAW_MCP_SESSION_ID not in json.dumps(event)
    assert (
        props["mcp_session_sha256"]
        == hashlib.sha256(RAW_MCP_SESSION_ID.encode("utf-8")).hexdigest()
    )


def test_mcp_session_digest_is_stable_across_token_rotation(make_client: Any) -> None:
    """THE WHOLE POINT: one session, two tokens, one session identity.

    This is what makes a hosted funnel possible. Under token-keyed counting these
    two events look like two separate users; under session-keyed counting they are
    correctly one.
    """
    client = make_client()
    with _inbound(auth="Bearer opik_mcp_at_first", mcp_session_id=RAW_MCP_SESSION_ID):
        first = client._build_event("opik_mcp_tools_listed", {})
    with _inbound(
        auth="Bearer opik_mcp_at_second_after_refresh", mcp_session_id=RAW_MCP_SESSION_ID
    ):
        second = client._build_event("opik_mcp_tool_called", {"tool_name": "read"})

    # Different tokens...
    assert first["event_properties"]["token_sha256"] != second["event_properties"]["token_sha256"]
    # ...same session.
    assert (
        first["event_properties"]["mcp_session_sha256"]
        == second["event_properties"]["mcp_session_sha256"]
    )


def test_no_session_header_omits_the_field(make_client: Any) -> None:
    """stdio, and the initialize request itself, carry no session id.

    Absent rather than a sentinel: a placeholder would be counted as a real
    session, which is the same mistake the nil install_id makes.
    """
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}"):
        event = client._build_event("opik_mcp_tool_called", {"tool_name": "read"})
    assert "mcp_session_sha256" not in event["event_properties"]


def test_blank_session_header_omits_the_field(make_client: Any) -> None:
    """A whitespace-only header is not a session."""
    client = make_client()
    with _inbound(auth=f"Bearer {RAW_OAUTH_TOKEN}", mcp_session_id="   "):
        event = client._build_event("opik_mcp_tool_called", {"tool_name": "read"})
    assert "mcp_session_sha256" not in event["event_properties"]
