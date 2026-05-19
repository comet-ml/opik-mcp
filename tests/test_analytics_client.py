import json
import logging
import threading
from typing import Any

import httpx
import pytest
import respx

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.config import Settings

URL = "https://stats.comet.com/notify/event/"


def _settings(**overrides: Any) -> Settings:
    base = dict(opik_mcp_analytics_enabled=True, comet_workspace="ws-1")
    return Settings(**{**base, **overrides})


def _drain(client: AnalyticsClient, deadline_s: float = 2.0) -> None:
    """Wait for the worker thread to finish dispatching everything in the queue."""
    client._flush(deadline_s=deadline_s)


@respx.mock
def test_track_event_posts_wire_shape() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"success": True}))
    client = AnalyticsClient(_settings())
    try:
        client.track_event("opik_mcp_test", {"foo": "bar"})
        _drain(client)
    finally:
        client.close()

    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["event_type"] == "opik_mcp_test"
    # comet-stats indexes events by top-level `user_id` (ollie-assist contract).
    assert body["user_id"] == "ws-1"
    # Legacy `anonymous_id` key must not be sent — receiver wouldn't index it.
    assert "anonymous_id" not in body
    props = body["event_properties"]
    assert props["foo"] == "bar"
    # Common properties stamped by the client:
    assert props["environment"] == "prod"
    # `workspace` (not `workspace_id`) so it joins with ollie-assist events.
    assert props["workspace"] == "ws-1"
    assert "workspace_id" not in props
    assert "opik_mcp_version" in props
    assert "install_id" in props
    assert "python_version" in props
    assert "platform" in props
    assert "transport" in props
    # ISO-8601 UTC timestamp stamped client-side.
    assert "timestamp" in props
    assert props["timestamp"].endswith("+00:00")
    # `source` defaults to `comet.com` (cloud-Comet deploy convention).
    assert props["source"] == "comet.com"


@respx.mock
def test_track_event_omits_source_when_blanked() -> None:
    """Setting opik_mcp_analytics_source='' (e.g. on-prem) drops the field."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_mcp_analytics_source=""))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert "source" not in body["event_properties"]


def test_warns_when_onprem_url_with_cloud_source(caplog: pytest.LogCaptureFixture) -> None:
    """On-prem-looking COMET_URL_OVERRIDE + default source must log a WARNING.

    Safety net: an on-prem operator who sets COMET_URL_OVERRIDE but forgets
    OPIK_MCP_ANALYTICS_SOURCE='' would otherwise mis-label every event as
    cloud-Comet. The warning fires once at client startup.
    """
    with caplog.at_level(logging.WARNING, logger="opik_mcp.analytics"):
        client = AnalyticsClient(
            _settings(comet_url_override="https://opik.acme.internal")
        )
        try:
            pass
        finally:
            client.close()

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("looks on-prem" in m for m in warning_msgs), (
        f"expected on-prem misconfig warning, got: {warning_msgs!r}"
    )


def test_no_warning_for_cloud_url(caplog: pytest.LogCaptureFixture) -> None:
    """Default cloud URL must NOT trigger the on-prem warning."""
    with caplog.at_level(logging.WARNING, logger="opik_mcp.analytics"):
        client = AnalyticsClient(_settings())  # default comet_url_override = www.comet.com
        try:
            pass
        finally:
            client.close()

    on_prem_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "looks on-prem" in r.getMessage()
    ]
    assert not on_prem_warnings, f"unexpected on-prem warning: {on_prem_warnings}"


def test_no_warning_when_source_blanked_for_onprem(caplog: pytest.LogCaptureFixture) -> None:
    """On-prem URL with source explicitly blanked = correctly configured, no warning."""
    with caplog.at_level(logging.WARNING, logger="opik_mcp.analytics"):
        client = AnalyticsClient(
            _settings(
                comet_url_override="https://opik.acme.internal",
                opik_mcp_analytics_source="",
            )
        )
        try:
            pass
        finally:
            client.close()

    on_prem_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "looks on-prem" in r.getMessage()
    ]
    assert not on_prem_warnings, f"unexpected on-prem warning: {on_prem_warnings}"


@respx.mock
def test_track_event_emits_custom_source() -> None:
    """A custom source value (e.g. on-prem domain) propagates verbatim."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_mcp_analytics_source="acme-internal.example"))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert body["event_properties"]["source"] == "acme-internal.example"


@respx.mock
def test_track_event_falls_back_to_install_id_without_workspace() -> None:
    """No workspace → user_id falls back to the persisted install_id, never empty."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(comet_workspace=None))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert body["user_id"]  # never empty / None
    # No workspace was set, so `workspace` must NOT appear in event_properties.
    assert "workspace" not in body["event_properties"]


@respx.mock
def test_disabled_skips_post() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_mcp_analytics_enabled=False))
    try:
        client.track_event("opik_mcp_test", {})
        # A disabled client never starts a worker.
        assert client._worker is None
    finally:
        client.close()
    assert not route.called


@respx.mock
def test_worker_swallows_exceptions() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    client = AnalyticsClient(_settings())
    try:
        # Must not raise.
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()


def test_queue_full_drops_silently(caplog: pytest.LogCaptureFixture) -> None:
    """Filling the queue beyond capacity must never raise from track_event,
    and must log a DEBUG message for every dropped event."""
    # A threading.Event that the stub http_client will block on until we release it.
    release = threading.Event()

    class BlockingClient:
        def post(self, *args: Any, **kwargs: Any) -> None:
            release.wait()

        def close(self) -> None:
            pass

    client = AnalyticsClient(_settings(), http_client=BlockingClient(), max_queue_size=2)  # type: ignore[arg-type]
    try:
        with caplog.at_level(logging.DEBUG, logger="opik_mcp.analytics"):
            # Fire enough events to overflow the tiny queue — none must raise.
            for i in range(50):
                client.track_event("opik_mcp_test", {"i": str(i)})
    finally:
        release.set()  # unblock the worker so close() can join
        client.close()

    # At least one event must have been dropped and logged.
    drop_messages = [r for r in caplog.records if "analytics queue full; dropping" in r.message]
    assert drop_messages, "Expected at least one 'analytics queue full; dropping' DEBUG log"


def test_track_event_after_close_does_not_raise() -> None:
    """track_event called after close() must be silent and not enqueue."""

    class NullClient:
        def post(self, *args: Any, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            pass

    client = AnalyticsClient(_settings(), http_client=NullClient())  # type: ignore[arg-type]
    client.close()
    # Should be a no-op, not raise, and not enqueue anything.
    client.track_event("opik_mcp_test", {})
    assert client._queue.empty()


@respx.mock
def test_track_event_safe_without_running_event_loop() -> None:
    """Caller in pure sync context (no asyncio loop) must work."""
    assert not _has_running_loop()
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings())
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()
    assert route.called


@respx.mock
def test_http_500_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Worker must log a WARNING when the analytics endpoint returns 5xx."""
    respx.post(URL).mock(return_value=httpx.Response(500, text="internal error"))
    client = AnalyticsClient(_settings())
    try:
        with caplog.at_level(logging.WARNING, logger="opik_mcp.analytics"):
            client.track_event("opik_mcp_test", {"k": "v"})
            _drain(client)
    finally:
        client.close()

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected at least one WARNING log for HTTP 500 response"


def _has_running_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
