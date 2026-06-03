import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from opik_mcp import __main__ as main_mod
from opik_mcp.analytics import EVENT_SERVER_STARTED, EVENT_STARTUP_ERROR


class _RecorderClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []
        self.flush_calls: list[float] = []

    def track_event(self, event_type: str, properties: dict[str, str]) -> None:
        self.events.append((event_type, properties))

    def flush(self, deadline_s: float = 2.0) -> None:
        # Recorded so tests can assert we synchronously drained the queue
        # before the process exits — without this, the daemon worker is killed
        # mid-send and the BI event never lands.
        self.flush_calls.append(deadline_s)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    from opik_mcp.config import get_settings

    get_settings.cache_clear()


def _install_recorder(monkeypatch: pytest.MonkeyPatch) -> _RecorderClient:
    recorder = _RecorderClient()
    monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)
    monkeypatch.setattr("opik_mcp.__main__.get_analytics", lambda: recorder)
    # Config-fail path bypasses the singleton on purpose — see the doc on
    # ``_build_fallback_analytics_client``. Tests that drive that path need
    # the same recorder injected at the fallback factory.
    monkeypatch.setattr("opik_mcp.__main__._build_fallback_analytics_client", lambda: recorder)
    return recorder


def test_main_emits_server_started_then_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_recorder(monkeypatch)

    run_calls: list[str] = []

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            run_calls.append(transport)

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    # The test process disables analytics by default (see conftest). Opt back
    # in here so the emitted ``analytics_enabled`` flag is exercised as "true".
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "true")

    main_mod.main()

    assert run_calls == ["stdio"]
    event_types = [e[0] for e in recorder.events]
    assert EVENT_SERVER_STARTED in event_types
    # Happy path must NOT emit startup_error — that would corrupt the BI
    # funnel by double-counting every successful boot as a failed one.
    assert EVENT_STARTUP_ERROR not in event_types
    started = next(p for et, p in recorder.events if et == EVENT_SERVER_STARTED)
    assert started["transport"] == "stdio"
    assert started["analytics_enabled"] == "true"
    assert started["has_workspace"] in {"true", "false"}
    assert started["has_api_key"] in {"true", "false"}
    assert started["has_default_project"] in {"true", "false"}
    # Tier 1 fingerprint: every key MUST be present, every value a string.
    for key in (
        "is_ci",
        "is_container",
        "is_codespaces",
        "is_gitpod",
        "launch_method",
        "parent_process",
        "stdin_is_pipe",
        "stdout_is_pipe",
        "install_id_freshly_generated",
    ):
        assert key in started, f"missing fingerprint key: {key}"
        assert isinstance(started[key], str)
    # Spot-check bucketed values
    assert started["is_ci"] in {"true", "false"}
    assert started["is_container"] in {"true", "false", "unknown"}
    # Mirrors `_LAUNCH_METHOD_PATTERNS` in `analytics/environment.py` exactly —
    # any new bucket added there must appear here too, or a typo introducing a
    # phantom value would slip through CI.
    assert started["launch_method"] in {
        "uvx",
        "pipx",
        "venv",
        "system",
        "unknown",
    }
    assert started["install_id_freshly_generated"] in {"true", "false"}


def test_clean_exit_flushes_with_configured_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clean-exit shutdown emit must drain synchronously with the
    configured deadline — guards against a regression to 0 (which would drop a
    cold in-flight POST) and keeps the deadline bump intentional."""
    recorder = _install_recorder(monkeypatch)

    class _StubMcp:
        def run(self, *, transport: str) -> None:
            return  # clean exit

    monkeypatch.setattr("opik_mcp.server.mcp", _StubMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "true")

    main_mod.main()

    assert recorder.flush_calls == [main_mod._SHUTDOWN_FLUSH_DEADLINE_S]


# --- startup_error: config validation -------------------------------------- #


def test_startup_error_on_invalid_workspace_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad COMET_WORKSPACE_ID crashes Settings() — we must still phone home.

    Without this emit path, BI sees zero signal: no `server_started`, no
    `startup_error`, just a silent process exit. That's the worst-case for
    diagnosing install issues.
    """
    recorder = _install_recorder(monkeypatch)
    monkeypatch.setenv("COMET_WORKSPACE_ID", "not-a-uuid")
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(ValidationError):
        main_mod.main()

    event_types = [e[0] for e in recorder.events]
    # server_started MUST NOT fire — we never reached a usable Settings.
    assert EVENT_SERVER_STARTED not in event_types
    assert EVENT_STARTUP_ERROR in event_types
    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["phase"] == "config"
    assert props["error_kind"] == "invalid_config"
    assert props["exception_type"] == "ValidationError"
    # Synchronous flush is required because the daemon worker thread is
    # otherwise killed by the imminent SystemExit before the POST lands.
    assert recorder.flush_calls, "startup_error must be flushed synchronously before exit"


def test_startup_error_omits_pii_from_invalid_config_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw bad env value must never leak into the event payload."""
    import json

    recorder = _install_recorder(monkeypatch)
    # Use a globally unique canary that would only appear if the exception
    # message (or the env var itself) leaked into the event properties.
    canary = "PII-LEAK-CANARY-not-a-real-uuid-9b2f4e8c"
    monkeypatch.setenv("COMET_WORKSPACE_ID", canary)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(ValidationError):
        main_mod.main()

    payload = json.dumps(recorder.events)
    assert canary not in payload, f"PII leak: {canary!r} surfaced in {payload!r}"


# --- startup_error: unexpected exception during transport.run ------------- #


def test_startup_error_on_transport_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unhandled exception from mcp.run() must be tagged and re-raised."""
    recorder = _install_recorder(monkeypatch)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise OSError("address already in use")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(OSError, match="address already in use"):
        main_mod.main()

    event_types = [e[0] for e in recorder.events]
    assert EVENT_SERVER_STARTED in event_types
    assert EVENT_STARTUP_ERROR in event_types
    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["phase"] == "transport_start"
    assert props["error_kind"] == "transport_crash"
    assert props["exception_type"] == "OSError"
    assert props["transport"] == "stdio"
    assert recorder.flush_calls, "must flush before re-raising"


def test_transport_crash_propagates_full_context_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentry must see the same shape of context the analytics event has.

    Without the tags/transaction/fingerprint plumbing, a transport_start
    crash lands as a bare ``OSError`` issue with no indication of phase,
    error_kind, or which transport was being started — tool-call failures
    would be richly tagged while startup failures would be opaque.
    """
    _install_recorder(monkeypatch)

    captured_calls: list[dict[str, object]] = []

    def _spy(
        exc: BaseException,
        *,
        tags: dict[str, str] | None = None,
        extras: dict[str, object] | None = None,
        transaction: str | None = None,
        fingerprint: list[str] | None = None,
    ) -> None:
        captured_calls.append(
            {
                "exc": exc,
                "tags": dict(tags or {}),
                "transaction": transaction,
                "fingerprint": list(fingerprint) if fingerprint is not None else None,
            }
        )

    # Patch at the source module — covers every caller (here ``__main__``
    # via ``error_tracking.capture_exception(...)``) without needing per-
    # call-site shims.
    monkeypatch.setattr("opik_mcp.error_tracking.capture_exception", _spy)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise OSError("address already in use")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(OSError):
        main_mod.main()

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert isinstance(call["exc"], OSError)
    # Same shape as analytics props — one source of truth on what context
    # describes a startup crash. ``exception_type`` is the MRO-bucketed
    # name so cardinality stays bounded (PermissionError → OSError, …).
    assert call["tags"] == {
        "phase": "transport_start",
        "error_kind": "transport_crash",
        "exception_type": "OSError",
        "transport": "stdio",
    }
    # Transaction puts ``startup`` next to the exception type in Sentry's
    # issue listing, alongside ``read`` / ``write`` / ``ask_ollie`` for
    # tool failures.
    assert call["transaction"] == "startup"
    # Fingerprint splits transport_crash from any future startup-phase
    # buckets (e.g. a "watchdog_crash" added later).
    assert call["fingerprint"] == ["{{ default }}", "startup", "transport_crash"]


def test_startup_error_omits_pii_from_transport_crash_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception message must not appear in the event — only the class name."""
    recorder = _install_recorder(monkeypatch)

    canary = "STARTUP-CRASH-PII-CANARY-secret-path-7f2a"

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise RuntimeError(canary)

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(RuntimeError):
        main_mod.main()

    payload = json.dumps(recorder.events)
    assert canary not in payload, f"PII leak: {canary!r} surfaced in {payload!r}"


# --- exception_type bucketing -------------------------------------------- #


def test_transport_crash_buckets_oserror_subclass_via_mro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PermissionError`` (and other ``OSError`` subclasses) must bucket as
    ``"OSError"`` via MRO walk, not as ``"unknown"``.

    Without MRO walking the most common real-world bind failure — port 80
    on a non-root user → ``PermissionError`` — surfaces as ``"unknown"``,
    making BI funnels for bind-permission issues invisible. Bucketing to
    the parent ``OSError`` keeps cardinality bounded *and* gives BI a
    semantic group it can act on.
    """
    recorder = _install_recorder(monkeypatch)

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(PermissionError):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["exception_type"] == "OSError", (
        f"PermissionError should bucket to its OSError parent via MRO walk, "
        f"got {props['exception_type']!r}"
    )


def test_transport_crash_buckets_unknown_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom exception classes (e.g. from future uvicorn middleware) must
    bucket to ``"unknown"`` rather than surfacing the class name verbatim.

    Without bucketing the `exception_type` cardinality grows with every new
    transport plugin, breaking BI dashboards built on the existing low-
    cardinality contract used by ``bucket_exception`` in analytics/errors.py.
    """
    recorder = _install_recorder(monkeypatch)

    class _CustomBoom(Exception):
        pass

    class _BoomMcp:
        def run(self, *, transport: str) -> None:
            raise _CustomBoom("opaque")

    monkeypatch.setattr("opik_mcp.server.mcp", _BoomMcp())
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(_CustomBoom):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["exception_type"] == "unknown", (
        f"expected bucketed 'unknown' for _CustomBoom, got {props['exception_type']!r}"
    )


# --- integration: prove the config-fail path doesn't depend on the singleton --


@respx.mock
def test_startup_error_emits_via_fallback_when_settings_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof that the config-fail emit path bypasses the broken singleton.

    Without the dedicated fallback client, ``_emit_startup_error()`` would
    resolve ``get_analytics()`` → ``AnalyticsClient(get_settings())`` →
    ``Settings()`` → same ``ValidationError``. ``lru_cache`` does not memoize
    exceptions, so the second construction raises identically; the outer
    ``except Exception`` swallows it and the event is silently dropped in
    production. This test asserts the HTTP POST actually lands by mocking at
    the httpx level (closest to the wire we can get without a real server).
    """
    route = respx.post("https://stats.comet.com/notify/event/").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    monkeypatch.setenv("COMET_WORKSPACE_ID", "not-a-uuid")
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    # The test process disables analytics by default (see conftest); this test
    # is specifically about proving the fallback client POSTs, so opt back in.
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "true")

    with pytest.raises(ValidationError):
        main_mod.main()

    assert route.called, "fallback client must POST startup_error on settings failure"
    body = json.loads(route.calls.last.request.content)
    assert body["event_type"] == EVENT_STARTUP_ERROR
    props = body["event_properties"]
    assert props["phase"] == "config"
    assert props["error_kind"] == "invalid_config"
    assert props["exception_type"] == "ValidationError"


@respx.mock
def test_fallback_client_honors_analytics_disabled_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User opt-out via OPIK_MCP_ANALYTICS_ENABLED=false must hold on the failure path.

    ``Settings.model_construct()`` ignores env vars (it skips all validation
    machinery), so without explicit env handling the fallback client would
    phone home for users who had disabled analytics. That's a privacy /
    compliance regression specifically on the config-fail path that users
    can't see or control.
    """
    route = respx.post("https://stats.comet.com/notify/event/").mock(
        return_value=httpx.Response(200)
    )
    monkeypatch.setenv("OPIK_MCP_ANALYTICS_ENABLED", "false")
    monkeypatch.setenv("COMET_WORKSPACE_ID", "not-a-uuid")
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")

    with pytest.raises(ValidationError):
        main_mod.main()

    assert not route.called, "analytics_enabled=false must suppress emit even on config-fail"


# --- startup_error: OAuth resource URI required on HTTP transport ----------- #


def test_startup_error_when_oauth_enabled_without_resource_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP + OPIK_MCP_AS_URL but no OPIK_MCP_RESOURCE_URI must fail fast.

    RFC 9728 requires `resource` in the protected-resource doc, and the AS
    exact-matches the authorize `resource` param against its own config — so
    serving the doc without it would 401-loop hosts with invalid_target.
    """
    recorder = _install_recorder(monkeypatch)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "http")
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://example.test/opik")
    monkeypatch.delenv("OPIK_MCP_RESOURCE_URI", raising=False)

    with pytest.raises(SystemExit):
        main_mod.main()

    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["phase"] == "config"
    assert props["error_kind"] == "invalid_config"
    assert props["transport"] == "http"
    assert recorder.flush_calls, "startup_error must be flushed synchronously before exit"


def test_no_startup_error_when_resource_uri_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP + both AS URL and resource URI set must pass the guard and boot."""
    recorder = _install_recorder(monkeypatch)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "http")
    monkeypatch.setenv("OPIK_MCP_AS_URL", "https://example.test/opik")
    monkeypatch.setenv("OPIK_MCP_RESOURCE_URI", "https://example.test/opik/api/v1/mcp")

    # Stub out the actual server boot — we only care that the config guard passes.
    monkeypatch.setattr(main_mod, "_preflight_bind_check", lambda host, port: None)
    monkeypatch.setattr("opik_mcp.server.build_app", lambda: object())
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    main_mod.main()

    error_props = [p for et, p in recorder.events if et == EVENT_STARTUP_ERROR]
    assert error_props == [], "guard must not fire when resource URI is set"
    assert EVENT_SERVER_STARTED in [e[0] for e in recorder.events]
