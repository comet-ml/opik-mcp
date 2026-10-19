"""Tests for opik_mcp.error_tracking — Sentry setup, before_send cap, capture context."""

from __future__ import annotations

import contextvars
import json
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Literal

import anyio
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from opik_mcp import error_tracking
from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.analytics.identity import get_install_id
from opik_mcp.config import Settings, installation_type
from opik_mcp.identity.context import (
    OAUTH_ACCESS_TOKEN_PREFIX,
    inbound_authorization,
    inbound_workspace,
)
from opik_mcp.identity.store import ResolvedIdentity, credential_digest, remember_identity
from tests.factories import make_settings

# --- before_send filter --------------------------------------------------- #


@pytest.fixture(autouse=True)
def _disable_pytest_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the ``_in_pytest()`` check inside ``before_send``.

    Production cap behaviour is what we're testing — the pytest guard is a
    separate defence-in-depth concern verified in
    ``test_setup_sentry_skips_under_pytest``.
    """
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)


@pytest.fixture(autouse=True)
def _install_id_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The process user is the install id; keep its file out of the real HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))


def _call_before_send(fn: Callable[[Event, Hint], Event | None], event: Event) -> Event | None:
    """Invoke a before_send callback with an empty Hint.

    The user-side / status-code filter is gone — capture sites in this
    codebase decide what to send — so ``hint`` is now unused. Tests pass an
    empty mapping to match Sentry's actual call shape.
    """
    return fn(event, {})


def test_before_send_caps_events_at_30() -> None:
    """Per-process cap prevents a retry loop from flooding the project."""
    before_send = error_tracking._build_before_send()

    kept = 0
    for _ in range(50):
        if _call_before_send(before_send, {"level": "error"}) is not None:
            kept += 1
    assert kept == 30


@pytest.mark.parametrize("level", ["fatal", "error", "warning", "info", "debug"])
def test_before_send_caps_regardless_of_level(
    level: Literal["fatal", "error", "warning", "info", "debug"],
) -> None:
    """Cap applies to every Sentry level, not just ``error``.

    ``fatal`` is the case that mattered most before the fix — it's the
    severest level Sentry has and a fatal-loop flood is even worse to
    swallow than an error-loop flood. The check used to special-case
    ``error == level``; verify that's gone and the cap is now uniform.
    """
    before_send = error_tracking._build_before_send()

    kept = 0
    for _ in range(50):
        if _call_before_send(before_send, {"level": level}) is not None:
            kept += 1
    assert kept == 30


def test_before_send_cap_counter_is_shared_across_levels() -> None:
    """30 ``error`` events saturate the cap; a subsequent ``fatal`` /
    ``warning`` must also be dropped. Single counter — not per-level.
    """
    before_send = error_tracking._build_before_send()
    for _ in range(30):
        _call_before_send(before_send, {"level": "error"})

    # Cap exhausted by errors — other levels also blocked.
    assert _call_before_send(before_send, {"level": "fatal"}) is None
    assert _call_before_send(before_send, {"level": "warning"}) is None
    assert _call_before_send(before_send, {}) is None


def test_before_send_caps_events_without_level() -> None:
    """Events with no ``level`` field (rare; sentry-sdk usually sets it on
    capture) still count toward the cap.
    """
    before_send = error_tracking._build_before_send()

    kept = 0
    for _ in range(50):
        if _call_before_send(before_send, {}) is not None:
            kept += 1
    assert kept == 30


def test_before_send_drops_everything_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pytest defence-in-depth must override the cap."""
    before_send = error_tracking._build_before_send()
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: True)

    assert _call_before_send(before_send, {"level": "error"}) is None


# --- setup_sentry --------------------------------------------------------- #


def _settings(**overrides: object) -> Settings:
    # ``opik_mcp_sentry_dsn`` is a ClassVar on Settings — hardcoded, NOT a
    # constructor arg. Tests inherit the production DSN automatically; they
    # never need (and can't) inject a fake one.
    base: dict[str, object] = {"opik_mcp_sentry_enabled": True}
    base.update(overrides)
    return make_settings(**base)


def test_settings_dsn_is_not_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DSN must be a ClassVar, NOT a pydantic field.

    If someone "fixes" a future merge conflict by demoting it back to a
    regular field, ``OPIK_MCP_SENTRY_DSN`` becomes env-readable again and a
    user could quietly redirect crash reports to a foreign Sentry project.
    Pin the contract at the Settings layer so the regression is caught here
    instead of in production.
    """
    monkeypatch.setenv("OPIK_MCP_SENTRY_DSN", "https://attacker@evil.example.com/123")
    assert (
        Settings().opik_mcp_sentry_dsn
        == "https://0b191296a0c2e1369da34e7d8fa85322@o168229.ingest.us.sentry.io/4511450607910912"
    )


def test_setup_sentry_returns_false_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    init_calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: init_calls.append(kw))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    assert error_tracking.setup_sentry(_settings(opik_mcp_sentry_enabled=False)) is False
    assert init_calls == []


def test_setup_sentry_skips_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never phone home; the in-pytest guard is the gate."""
    init_calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: init_calls.append(kw))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: True)

    assert error_tracking.setup_sentry(_settings()) is False
    assert init_calls == []


def test_setup_sentry_initializes_and_binds_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    init_calls: list[dict[str, object]] = []
    tag_calls: list[tuple[str, str]] = []
    user_calls: list[dict[str, object]] = []

    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: init_calls.append(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: tag_calls.append((k, v)))
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: user_calls.append(u))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    settings = _settings(
        opik_mcp_transport="stdio",
        comet_workspace="wkspc",
        opik_api_key="sk-test",
    )
    assert error_tracking.setup_sentry(settings) is True

    assert len(init_calls) == 1
    kwargs = init_calls[0]
    # The hardcoded ClassVar DSN reaches sentry_sdk.init unchanged.
    assert kwargs["dsn"] == Settings.opik_mcp_sentry_dsn
    # No baked-in integrations — own-the-capture-sites contract.
    assert kwargs["default_integrations"] is False
    assert kwargs["integrations"] == []
    # No performance tracing — Sentry is purely for error events here.
    assert kwargs["traces_sample_rate"] == 0.0
    assert kwargs["send_default_pii"] is False
    assert callable(kwargs["before_send"])

    tags = dict(tag_calls)
    assert tags["transport"] == "stdio"
    assert tags["has_workspace_id"] == "false"
    assert tags["has_api_key"] == "true"
    assert "release" in tags
    assert "os_type" in tags
    assert "python_version" in tags

    # The process default is BI's fallback, never the workspace; the caller's
    # login is set per event by capture_exception.
    assert user_calls == [{"id": get_install_id()}]
    assert "workspace" not in tags


@pytest.mark.parametrize(
    "comet_url, opik_url, expected",
    [
        # Default cloud configuration.
        ("https://www.comet.com", None, "cloud"),
        # Without subdomain.
        ("https://comet.com", None, "cloud"),
        # Common dev / docker-compose targets.
        ("http://localhost:5173", None, "local"),
        ("http://127.0.0.1:5173", None, "local"),
        ("http://0.0.0.0:5173", None, "local"),
        # On-prem deploy at a custom domain.
        ("https://opik.acme.corp", None, "self-hosted"),
        # Staging is intentionally NOT cloud — mirrors opik SDK's strict
        # equality so prod-cloud dashboards stay clean.
        ("https://staging.comet.com", None, "self-hosted"),
        # opik_url override wins over comet_url_override.
        ("https://www.comet.com", "http://localhost:5173", "local"),
    ],
)
def test_installation_type_taxonomy(comet_url: str, opik_url: str | None, expected: str) -> None:
    # The classifier lives in config (leaf module) so error_tracking + boot_props
    # share it without an import cycle; this pins the taxonomy at its source.
    settings = _settings(comet_url_override=comet_url, opik_url=opik_url)
    assert installation_type(settings) == expected


def test_setup_sentry_stamps_installation_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """``installation_type`` mirrors opik SDK so cross-product queries work."""
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: tag_calls.append((k, v)))
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: None)
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    error_tracking.setup_sentry(_settings(comet_workspace="acme-team"))

    # Default config points at cloud Comet.
    assert dict(tag_calls)["installation_type"] == "cloud"


def test_setup_sentry_user_is_never_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace user merged every open-source install into one "default" user."""
    user_calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: None)
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: user_calls.append(u))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    uuid = "11111111-1111-1111-1111-111111111111"
    error_tracking.setup_sentry(_settings(comet_workspace="default", comet_workspace_id=uuid))

    assert user_calls == [{"id": get_install_id()}]


# --- capture_exception ---------------------------------------------------- #


class _StubScope:
    """Records every scope mutation a capture call makes.

    Tests use this in place of sentry-sdk's real scope so they can assert
    on tags, extras, transaction, and fingerprint without spinning up a
    real Sentry client.
    """

    def __init__(self) -> None:
        self.tags: dict[str, object] = {}
        self.extras: dict[str, object] = {}
        self.transaction: str | None = None
        self.fingerprint: list[str] | None = None
        self.user: dict[str, str] | None = None

    def set_tag(self, key: str, value: object) -> None:
        self.tags[key] = value

    def set_user(self, value: dict[str, str]) -> None:
        self.user = value

    def set_extra(self, key: str, value: object) -> None:
        self.extras[key] = value

    def set_transaction_name(self, name: str, source: object = None) -> None:
        self.transaction = name


class _StubScopeCtx:
    def __init__(self, scope: _StubScope) -> None:
        self.scope = scope

    def __enter__(self) -> _StubScope:
        return self.scope

    def __exit__(self, *_a: object) -> None:
        return None


def _install_scope_stub(monkeypatch: pytest.MonkeyPatch) -> _StubScope:
    scope = _StubScope()
    monkeypatch.setattr(sentry_sdk, "new_scope", lambda: _StubScopeCtx(scope))
    return scope


def test_capture_exception_forwards_to_sentry_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_scope_stub(monkeypatch)
    captured: list[BaseException] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: captured.append(exc))

    exc = RuntimeError("boom")
    error_tracking.capture_exception(exc)
    assert captured == [exc]


def test_capture_exception_pushes_tags_and_extras_onto_event_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-event scope keeps concurrent HTTP-transport calls from polluting
    each other's tag set — that's the whole reason for ``new_scope`` over
    ``set_tag`` on the global scope.
    """
    scope = _install_scope_stub(monkeypatch)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: None)

    error_tracking.capture_exception(
        RuntimeError("boom"),
        tags={"tool_name": "read", "error_kind": "opik_http_5xx"},
        extras={"duration_ms": 42, "raw_kwargs_count": 3},
    )

    assert scope.tags.items() >= {"tool_name": "read", "error_kind": "opik_http_5xx"}.items()
    assert scope.extras == {"duration_ms": 42, "raw_kwargs_count": 3}


def test_capture_exception_sets_transaction_for_issue_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transaction shows next to the exception type in Sentry's issue list —
    the user-visible "which tool failed" signal we want on every event.
    """
    scope = _install_scope_stub(monkeypatch)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: None)

    error_tracking.capture_exception(RuntimeError("boom"), transaction="read")

    assert scope.transaction == "read"


def test_capture_exception_sets_fingerprint_for_grouping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default fingerprint plus an extra key splits issues that would
    otherwise merge — e.g. a shared helper raising the same exception type
    from two different tools.
    """
    scope = _install_scope_stub(monkeypatch)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: None)

    error_tracking.capture_exception(RuntimeError("boom"), fingerprint=["{{ default }}", "read"])

    assert scope.fingerprint == ["{{ default }}", "read"]


def test_capture_exception_handles_empty_tags_and_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``__main__`` startup path calls without context — must not error."""
    scope = _install_scope_stub(monkeypatch)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: None)

    error_tracking.capture_exception(RuntimeError("boom"))

    # Only the caller's identity, which every capture carries.
    assert set(scope.tags) <= {"user_id_kind", "install_id_kind", "workspace_kind", "workspace"}
    assert scope.user == {"id": get_install_id()}
    assert scope.extras == {}


def test_capture_exception_swallows_sentry_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentry-side failures must never break the caller's error path."""

    def _raise(*_a: object, **_kw: object) -> None:
        raise RuntimeError("sentry transport down")

    monkeypatch.setattr(sentry_sdk, "new_scope", _raise)

    # Must not raise.
    error_tracking.capture_exception(RuntimeError("the real bug"))


# --- the event user: the same rule as BI's user_id ------------------------- #

_OAUTH_TOKEN_A = f"{OAUTH_ACCESS_TOKEN_PREFIX}secret-token-a"
_OAUTH_TOKEN_B = f"{OAUTH_ACCESS_TOKEN_PREFIX}secret-token-b"
_API_KEY = "secret-api-key-c"
_SECRETS = (_OAUTH_TOKEN_A, _OAUTH_TOKEN_B, _API_KEY)


class _MemoryTransport(Transport):
    """Keeps every event in memory. Nothing leaves the process."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def sentry_events() -> Generator[list[Event]]:
    """A real Sentry client whose events stay in memory."""
    transport = _MemoryTransport()
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=transport,
        default_integrations=False,
        integrations=[],
        send_default_pii=False,
    )
    try:
        yield transport.events
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)
        sentry_sdk.get_isolation_scope().clear()


def _serve(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    """Bind the process scope for ``overrides`` and capture with the same settings."""
    settings = _settings(**overrides)
    monkeypatch.setattr(error_tracking, "get_settings", lambda: settings)
    error_tracking._bind_scope(settings)
    return settings


def _capture_as(auth: str | None, workspace: str | None, message: str) -> None:
    """Capture inside one request's context, as a tool call does."""
    inbound_authorization.set(auth)
    inbound_workspace.set(workspace)
    error_tracking.capture_exception(RuntimeError(message), tags={"tool_name": "read"})


async def _hosted_call(auth: str, workspace: str | None, message: str) -> None:
    inbound_authorization.set(auth)
    inbound_workspace.set(workspace)
    # Let the other requests bind their own identity before this one captures.
    await anyio.sleep(0.01)
    error_tracking.capture_exception(RuntimeError(message), tags={"tool_name": "read"})


def _event(events: list[Event], message: str) -> Event:
    (event,) = [e for e in events if e["exception"]["values"][0]["value"] == message]
    return event


def _assert_no_secret(events: list[Event]) -> None:
    sent = json.dumps(events)
    for secret in _SECRETS:
        assert secret not in sent
        assert credential_digest(secret) not in sent


def _identity(login: str, workspace: str) -> ResolvedIdentity:
    return ResolvedIdentity(user_name=login, workspace_name=workspace, workspace_id=None)


def test_stdio_call_with_a_resolved_login_reports_the_login(
    monkeypatch: pytest.MonkeyPatch, sentry_events: list[Event]
) -> None:
    monkeypatch.setattr(
        "opik_mcp.identity.caller.resolve_api_key_identity",
        lambda _settings: _identity("alice", "ws-a"),
    )
    _serve(monkeypatch, opik_mcp_transport="stdio", opik_api_key=_API_KEY, comet_workspace="ws-a")

    contextvars.copy_context().run(_capture_as, None, None, "boom")

    event = _event(sentry_events, "boom")
    assert event["user"] == {"id": "alice"}
    assert event["tags"]["user_id_kind"] == "comet_user"
    assert event["tags"]["workspace"] == "ws-a"
    assert event["tags"]["has_api_key"] == "true"
    _assert_no_secret(sentry_events)


@pytest.mark.parametrize(("workspace", "expected_tag"), [("default", "default"), (None, None)])
def test_open_source_call_reports_the_install_id_not_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[Event],
    workspace: str | None,
    expected_tag: str | None,
) -> None:
    """Every open-source install used to be one Sentry user called "default"."""
    _serve(
        monkeypatch,
        opik_mcp_transport="stdio",
        opik_url="http://localhost:5173/api",
        opik_api_key=None,
        comet_workspace=workspace,
        comet_workspace_id=None,
    )

    contextvars.copy_context().run(_capture_as, None, None, "boom")

    event = _event(sentry_events, "boom")
    assert event["user"] == {"id": get_install_id()}
    assert event["tags"]["user_id_kind"] == "install_id"
    assert event["tags"]["install_id_kind"] == "file"
    assert event["tags"].get("workspace") == expected_tag


@pytest.mark.anyio
async def test_concurrent_hosted_calls_report_their_own_user(
    monkeypatch: pytest.MonkeyPatch, sentry_events: list[Event]
) -> None:
    """OPIK-MCP-9E: every hosted event carried the server's nil id and no workspace."""
    _serve(monkeypatch, opik_mcp_transport="streamable-http", opik_api_key=None)
    remember_identity(_OAUTH_TOKEN_A, _identity("alice", "ws-a"))
    remember_identity(_OAUTH_TOKEN_B, _identity("bob", "ws-b"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(_hosted_call, f"Bearer {_OAUTH_TOKEN_A}", None, "call a")
        tg.start_soon(_hosted_call, f"Bearer {_OAUTH_TOKEN_B}", None, "call b")
        tg.start_soon(_hosted_call, f"Bearer {_API_KEY}", "ws-c", "call c")

    for message, login, workspace in (("call a", "alice", "ws-a"), ("call b", "bob", "ws-b")):
        event = _event(sentry_events, message)
        assert event["user"] == {"id": login}
        assert event["tags"]["user_id_kind"] == "comet_user"
        assert event["tags"]["workspace"] == workspace
        assert event["tags"]["has_api_key"] == "false"
        assert event["tags"]["tool_name"] == "read"

    # An inbound API key has no lookup: the install id, as in BI, and its own workspace.
    api_key_event = _event(sentry_events, "call c")
    assert api_key_event["user"] == {"id": get_install_id()}
    assert api_key_event["tags"]["user_id_kind"] == "install_id"
    assert api_key_event["tags"]["workspace"] == "ws-c"
    assert api_key_event["tags"]["has_api_key"] == "true"
    _assert_no_secret(sentry_events)


@pytest.mark.parametrize("auth", [None, f"Bearer {_OAUTH_TOKEN_A}", f"Bearer {_API_KEY}"])
def test_sentry_user_is_the_bi_user(
    monkeypatch: pytest.MonkeyPatch, sentry_events: list[Event], auth: str | None
) -> None:
    settings = _serve(monkeypatch, opik_api_key=None, opik_mcp_analytics_enabled=False)
    remember_identity(_OAUTH_TOKEN_A, _identity("alice", "ws-a"))

    def _both() -> tuple[object, object, object]:
        _capture_as(auth, None, "boom")
        bi = AnalyticsClient(settings)._build_event("opik_mcp_tool_called", {})
        props = bi["event_properties"]
        return bi["user_id"], props["user_id_kind"], props.get("workspace")

    user_id, user_id_kind, workspace = contextvars.copy_context().run(_both)

    event = _event(sentry_events, "boom")
    assert event["user"] == {"id": user_id}
    assert event["tags"]["user_id_kind"] == user_id_kind
    assert event["tags"].get("workspace") == workspace
