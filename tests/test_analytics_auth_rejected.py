"""Tests for AuthRejectionMiddleware — the opik_mcp_auth_rejected event (GAP#3).

The middleware is pure ASGI (not BaseHTTPMiddleware) so it never buffers
streaming SSE responses. It observes the response status and, for 401/421/403
on AUTHENTICATED paths, emits an analytics event whose reason is derived from
the Authorization header SHAPE only (never the token value).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from opik_mcp.config import Settings

# Unique bearer token canary — must never appear raw in the emitted event.
RAW_TOKEN_CANARY = "opik_at_AUTHREJECT-CANARY-TOKEN-3f9a2b1c"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {"opik_mcp_analytics_enabled": False, "_env_file": None}
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _app_returning(status: int) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


def _drive(mw: Any, *, path: str = "/mcp", auth: bytes | None = None) -> None:
    headers: list[tuple[bytes, bytes]] = []
    if auth is not None:
        headers.append((b"authorization", auth))
    scope = {"type": "http", "path": path, "headers": headers}

    async def receive() -> Any:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_msg: Any) -> None:
        return None

    asyncio.run(mw(scope, receive, send))


def _make(
    monkeypatch: pytest.MonkeyPatch, status: int, settings: Settings | None = None
) -> tuple[_Recorder, Any]:
    from opik_mcp import server

    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.server.track_event", lambda et, p: recorder.track_event(et, p))
    mw = server.AuthRejectionMiddleware(_app_returning(status), settings=settings or _settings())
    return recorder, mw


def _only(recorder: _Recorder) -> dict[str, str]:
    rejected = [p for et, p in recorder.events if et == "opik_mcp_auth_rejected"]
    assert rejected, f"expected an auth_rejected event, got {recorder.events!r}"
    return rejected[0]


def test_missing_auth_header_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/mcp", auth=None)
    assert _only(recorder)["rejection_reason"] == "missing_header"


def test_non_bearer_scheme_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/mcp", auth=b"Basic dXNlcjpwYXNz")
    assert _only(recorder)["rejection_reason"] == "not_bearer"


def test_empty_bearer_token_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/mcp", auth=b"Bearer    ")
    assert _only(recorder)["rejection_reason"] == "empty_token"


def test_421_is_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 421)
    _drive(mw, path="/mcp", auth=b"Bearer opik_at_x")
    assert _only(recorder)["rejection_reason"] == "host_rejected"


def test_403_is_origin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 403)
    _drive(mw, path="/mcp", auth=b"Bearer opik_at_x")
    assert _only(recorder)["rejection_reason"] == "origin_rejected"


def test_success_emits_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 200)
    _drive(mw, path="/mcp", auth=b"Bearer opik_at_x")
    assert not [p for et, p in recorder.events if et == "opik_mcp_auth_rejected"]


def test_unauth_path_rejection_is_not_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 401 proxied back from the AS during the OAuth dance (/authorize is an
    # _UNAUTH_PATHS proxy path) is NOT opik-mcp's resource-server rejection —
    # attributing it would pollute the auth-rejection chart.
    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/authorize", auth=None)
    assert not [p for et, p in recorder.events if et == "opik_mcp_auth_rejected"]


def test_health_path_rejection_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 403)
    _drive(mw, path="/health", auth=None)
    assert not [p for et, p in recorder.events if et == "opik_mcp_auth_rejected"]


def test_raw_token_never_in_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/mcp", auth=f"Bearer {RAW_TOKEN_CANARY}".encode())
    props = _only(recorder)
    assert RAW_TOKEN_CANARY not in json.dumps(props)
    # A non-empty non-... wait: a valid-looking bearer that still 401s is an
    # unexpected shape; reason falls back to missing_header (never leaks token).
    assert props["rejection_reason"] in {
        "missing_header",
        "not_bearer",
        "empty_token",
        "token_rejected",
        "host_rejected",
        "origin_rejected",
    }


def test_path_bucket_and_allowlisted_props(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import get_args

    from opik_mcp.analytics.events import AuthRejectionReason, PathBucket

    recorder, mw = _make(monkeypatch, 401)
    _drive(mw, path="/mcp", auth=None)
    props = _only(recorder)
    assert props["path_bucket"] in get_args(PathBucket)
    assert props["path_bucket"] == "mcp"
    assert props["rejection_reason"] in get_args(AuthRejectionReason)
    assert props["oauth_configured"] in {"true", "false"}
    assert props["resource_metadata_url_present"] in {"true", "false"}


def test_resource_metadata_url_present_reflects_absolute_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(opik_mcp_resource_uri="https://www.comet.com/api/v1/mcp")
    recorder, mw = _make(monkeypatch, 401, settings=settings)
    _drive(mw, path="/mcp", auth=None)
    assert _only(recorder)["resource_metadata_url_present"] == "true"


def test_resource_metadata_url_absent_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, mw = _make(monkeypatch, 401, settings=_settings(opik_mcp_resource_uri=None))
    _drive(mw, path="/mcp", auth=None)
    assert _only(recorder)["resource_metadata_url_present"] == "false"


def test_auth_mode_reflects_rejected_oauth_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    # A valid OAuth bearer rejected by the Host guard (421) must still report
    # auth_mode="oauth" — derived from the header, NOT the already-reset
    # ContextVar (which would yield the settings fallback).
    recorder, mw = _make(monkeypatch, 421)
    _drive(mw, path="/mcp", auth=b"Bearer opik_at_valid-token")
    props = _only(recorder)
    assert props["rejection_reason"] == "host_rejected"
    assert props["auth_mode"] == "oauth"


def test_oauth_only_deploy_missing_header_reports_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    # OAuth-only deploy (AS configured, no static key): a no-credential rejection
    # reports auth_mode="oauth" (settings-derived), consistent with boot/per-call.
    settings = _settings(opik_api_key=None, opik_mcp_as_url="https://as.example.com")
    recorder, mw = _make(monkeypatch, 401, settings=settings)
    _drive(mw, path="/mcp", auth=None)
    props = _only(recorder)
    assert props["rejection_reason"] == "missing_header"
    assert props["auth_mode"] == "oauth"


def test_app_exception_propagates_without_emitting(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the inner app raises before sending a response, the exception must
    # propagate and no auth_rejected event is emitted.
    from opik_mcp import server

    recorder = _Recorder()
    monkeypatch.setattr("opik_mcp.server.track_event", lambda et, p: recorder.track_event(et, p))

    async def _boom(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("inner app blew up")

    mw = server.AuthRejectionMiddleware(_boom, settings=_settings())
    with pytest.raises(RuntimeError):
        _drive(mw, path="/mcp", auth=None)
    assert not recorder.events


def test_path_bucket_honours_custom_http_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The configured MCP mount must be threaded through to bucket_path.
    recorder, mw = _make(monkeypatch, 401, settings=_settings(opik_mcp_http_path="/api/v1/mcp"))
    _drive(mw, path="/api/v1/mcp", auth=None)
    assert _only(recorder)["path_bucket"] == "mcp"


def test_lifespan_scope_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # Non-http scopes (lifespan/websocket) must pass straight through so the
    # composed lifespan still runs when AuthRejectionMiddleware is outermost.
    from opik_mcp import server

    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    mw = server.AuthRejectionMiddleware(inner, settings=_settings())

    async def receive() -> Any:
        return {"type": "lifespan.startup"}

    async def send(_msg: Any) -> None:
        return None

    asyncio.run(mw({"type": "lifespan"}, receive, send))
    assert seen == ["lifespan"]
