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
    assert body["anonymous_id"] == "ws-1"
    props = body["event_properties"]
    assert props["foo"] == "bar"
    # Common properties stamped by the client:
    assert props["environment"] == "prod"
    assert props["workspace_id"] == "ws-1"
    assert "opik_mcp_version" in props
    assert "install_id" in props
    assert "python_version" in props
    assert "platform" in props
    assert "transport" in props


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
