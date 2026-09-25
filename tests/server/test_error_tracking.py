"""Tests for opik_mcp.error_tracking — Sentry setup, before_send cap, capture context."""

from __future__ import annotations

from typing import Any

import pytest
import sentry_sdk

from opik_mcp import error_tracking
from opik_mcp.config import Settings, installation_type

# --- before_send filter --------------------------------------------------- #


@pytest.fixture(autouse=True)
def _disable_pytest_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the ``_in_pytest()`` check inside ``before_send``.

    Production cap behaviour is what we're testing — the pytest guard is a
    separate defence-in-depth concern verified in
    ``test_setup_sentry_skips_under_pytest``.
    """
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)


def _call_before_send(fn: Any, event: dict[str, Any]) -> Any:
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
def test_before_send_caps_regardless_of_level(level: str) -> None:
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


def _settings(**overrides: Any) -> Settings:
    # ``opik_mcp_sentry_dsn`` is a ClassVar on Settings — hardcoded, NOT a
    # constructor arg. Tests inherit the production DSN automatically; they
    # never need (and can't) inject a fake one.
    base: dict[str, Any] = {"opik_mcp_sentry_enabled": True}
    base.update(overrides)
    return Settings(**base)


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
    init_calls: list[Any] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: init_calls.append(kw))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    assert error_tracking.setup_sentry(_settings(opik_mcp_sentry_enabled=False)) is False
    assert init_calls == []


def test_setup_sentry_skips_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never phone home; the in-pytest guard is the gate."""
    init_calls: list[Any] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: init_calls.append(kw))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: True)

    assert error_tracking.setup_sentry(_settings()) is False
    assert init_calls == []


def test_setup_sentry_initializes_and_binds_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    init_calls: list[dict[str, Any]] = []
    tag_calls: list[tuple[str, str]] = []
    user_calls: list[dict[str, Any]] = []

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

    # User identity falls back from workspace_id → workspace → install_id.
    assert user_calls == [{"id": "wkspc"}]


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


def test_setup_sentry_stamps_installation_type_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two tags every dashboard query needs: which Opik deployment, and
    which workspace inside it. ``installation_type`` mirrors opik SDK so
    cross-product queries work; ``workspace`` gives readable filtering.
    """
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: tag_calls.append((k, v)))
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: None)
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    settings = _settings(comet_workspace="acme-team")
    error_tracking.setup_sentry(settings)

    tags = dict(tag_calls)
    # Default config points at cloud Comet.
    assert tags["installation_type"] == "cloud"
    assert tags["workspace"] == "acme-team"


def test_setup_sentry_omits_workspace_tag_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No workspace name → no ``workspace`` tag. Avoids stamping a useless
    empty string on every event; ``has_workspace_id`` still carries the
    boolean signal.
    """
    tag_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: tag_calls.append((k, v)))
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: None)
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    error_tracking.setup_sentry(_settings())

    assert "workspace" not in dict(tag_calls)


def test_setup_sentry_user_id_prefers_workspace_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda k, v: None)
    monkeypatch.setattr(sentry_sdk, "set_user", lambda u: user_calls.append(u))
    monkeypatch.setattr(error_tracking, "_in_pytest", lambda: False)

    uuid = "11111111-1111-1111-1111-111111111111"
    settings = _settings(comet_workspace="wkspc", comet_workspace_id=uuid)
    error_tracking.setup_sentry(settings)

    assert user_calls == [{"id": uuid}]


# --- capture_exception ---------------------------------------------------- #


class _StubScope:
    """Records every scope mutation a capture call makes.

    Tests use this in place of sentry-sdk's real scope so they can assert
    on tags, extras, transaction, and fingerprint without spinning up a
    real Sentry client.
    """

    def __init__(self) -> None:
        self.tags: dict[str, Any] = {}
        self.extras: dict[str, Any] = {}
        self.transaction: str | None = None
        self.fingerprint: list[str] | None = None

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def set_transaction_name(self, name: str, source: Any = None) -> None:
        self.transaction = name


class _StubScopeCtx:
    def __init__(self, scope: _StubScope) -> None:
        self.scope = scope

    def __enter__(self) -> _StubScope:
        return self.scope

    def __exit__(self, *_a: Any) -> None:
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

    assert scope.tags == {"tool_name": "read", "error_kind": "opik_http_5xx"}
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

    assert scope.tags == {}
    assert scope.extras == {}


def test_capture_exception_swallows_sentry_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentry-side failures must never break the caller's error path."""

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("sentry transport down")

    monkeypatch.setattr(sentry_sdk, "new_scope", _raise)

    # Must not raise.
    error_tracking.capture_exception(RuntimeError("the real bug"))
