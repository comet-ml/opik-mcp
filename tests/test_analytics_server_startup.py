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
    for key in ("is_ci", "is_container", "is_codespaces", "is_gitpod",
                "launch_method", "parent_process", "stdin_is_pipe",
                "stdout_is_pipe", "install_id_freshly_generated"):
        assert key in started, f"missing fingerprint key: {key}"
        assert isinstance(started[key], str)
    # Spot-check bucketed values
    assert started["is_ci"] in {"true", "false"}
    assert started["is_container"] in {"true", "false", "unknown"}
    assert started["launch_method"] in {
        "uvx", "pipx", "venv", "system", "pip", "npx", "unknown",
    }
    assert started["install_id_freshly_generated"] in {"true", "false"}


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


# --- startup_error: insecure default token + non-loopback ----------------- #


def test_startup_error_on_insecure_token_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP mode with the default dev token + a public bind must emit before exit."""
    recorder = _install_recorder(monkeypatch)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("OPIK_MCP_HOST", "0.0.0.0")
    # OPIK_MCP_DEV_TOKEN stays at the insecure default by not setting it.
    monkeypatch.delenv("OPIK_MCP_DEV_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1

    event_types = [e[0] for e in recorder.events]
    # server_started fires earlier in the flow (boot was attempted) — leave
    # the existing behaviour intact; startup_error correlates the failure.
    assert EVENT_SERVER_STARTED in event_types
    assert EVENT_STARTUP_ERROR in event_types
    props = next(p for et, p in recorder.events if et == EVENT_STARTUP_ERROR)
    assert props["phase"] == "http_bind_check"
    assert props["error_kind"] == "insecure_token_on_public_iface"
    assert props["transport"] == "streamable-http"
    assert recorder.flush_calls, "must flush before sys.exit"


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
    cardinality contract used by ``_ERROR_KIND_TABLE`` in wrappers.py.
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
