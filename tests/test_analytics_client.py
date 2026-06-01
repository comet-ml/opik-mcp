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
    # Explicit None defaults so a stray OPIK_API_KEY / COMET_WORKSPACE_ID in the
    # developer's shell can't change which branch of `_build_event` the test hits.
    base: dict[str, Any] = dict(
        opik_mcp_analytics_enabled=True,
        comet_workspace="ws-1",
        opik_api_key=None,
        comet_workspace_id=None,
    )
    return Settings(**{**base, **overrides})


def _drain(client: AnalyticsClient, deadline_s: float = 2.0) -> None:
    """Wait for the worker thread to finish dispatching everything in the queue."""
    client.flush(deadline_s=deadline_s)


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
        client = AnalyticsClient(_settings(comet_url_override="https://opik.acme.internal"))
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
    """No api_key, no workspace → user_id falls back to install_id, never empty."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(comet_workspace=None, opik_api_key=None))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert body["user_id"]  # never empty / None
    # No workspace was set, so `workspace` must NOT appear in event_properties.
    assert "workspace" not in body["event_properties"]
    # No api_key was set, so `api_key_sha256` must NOT appear either.
    assert "api_key_sha256" not in body["event_properties"]


# --- pseudonymous user identity (api_key hash + workspace_id) ------------ #


@respx.mock
def test_api_key_hash_stamped_in_event_properties_when_key_set() -> None:
    """OPIK_API_KEY set → SHA-256 hash appears in event_properties.api_key_sha256.

    The backend retains the raw-key → user-id mapping; BI joins the digest
    to the auth table to count distinct Comet users. Top-level ``user_id``
    is intentionally unchanged (workspace name) for dashboard continuity.
    """
    from opik_mcp.analytics.identity import api_key_sha256

    raw_key = "sk-some-secret-key"
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_api_key=raw_key))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert body["event_properties"]["api_key_sha256"] == api_key_sha256(raw_key)
    # Top-level user_id stays workspace name — dashboards built against the
    # pre-Phase-1.5 schema must not see a discontinuous type-flip.
    assert body["user_id"] == "ws-1"
    assert body["event_properties"]["workspace"] == "ws-1"


@respx.mock
def test_api_key_hash_absent_when_key_unset() -> None:
    """No OPIK_API_KEY → field omitted (don't stamp empty / sentinel)."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_api_key=None))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert "api_key_sha256" not in body["event_properties"]


@respx.mock
def test_raw_api_key_never_in_payload_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    """PRIVACY CONTRACT: the raw OPIK_API_KEY must NEVER appear in any
    posted payload AND must NEVER appear in any log record / exception text.

    Covers both attack surfaces: a future commit that puts the raw key in an
    exception message (e.g. ``f"failed to encode: {settings.opik_api_key}"``)
    would have its traceback dumped via ``exc_info=True`` in the worker's
    error path — the caplog check catches it before it ships.
    """
    raw_key = "sk-DO-NOT-LEAK-canary-9f8e7d6c"
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    with caplog.at_level(logging.DEBUG, logger="opik_mcp.analytics"):
        client = AnalyticsClient(_settings(opik_api_key=raw_key))
        try:
            client.track_event("opik_mcp_test", {"foo": "bar"})
            client.track_event("opik_mcp_other", {})
            _drain(client)
        finally:
            client.close()

    for call in route.calls:
        body = call.request.content.decode("utf-8", errors="replace")
        assert raw_key not in body, f"raw api_key leaked in payload: {body!r}"
    for record in caplog.records:
        msg = record.getMessage()
        assert raw_key not in msg, f"raw api_key leaked in log message: {msg!r}"
        assert raw_key not in (record.exc_text or ""), (
            f"raw api_key leaked in exception text: {record.exc_text!r}"
        )


@respx.mock
def test_workspace_id_stamped_when_set() -> None:
    """COMET_WORKSPACE_ID set → stamped as event_properties.workspace_id."""
    workspace_uuid = "11111111-2222-3333-4444-555555555555"
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(comet_workspace_id=workspace_uuid))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert body["event_properties"]["workspace_id"] == workspace_uuid


@respx.mock
def test_workspace_id_absent_when_unset() -> None:
    """No COMET_WORKSPACE_ID → field is omitted (don't send empty string)."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings())  # no comet_workspace_id
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    body = json.loads(route.calls.last.request.content)
    assert "workspace_id" not in body["event_properties"]


@respx.mock
def test_api_key_hash_and_workspace_id_both_stamped_when_both_set() -> None:
    """The two identity fields are independent — both stamp simultaneously.

    Guards against a future refactor that gates one on the other (e.g.
    ``if workspace_id and api_key`` instead of two parallel ifs).
    """
    from opik_mcp.analytics.identity import api_key_sha256

    raw_key = "sk-dual-stamp-test"
    workspace_uuid = "11111111-2222-3333-4444-555555555555"
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(opik_api_key=raw_key, comet_workspace_id=workspace_uuid))
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    props = json.loads(route.calls.last.request.content)["event_properties"]
    assert props["api_key_sha256"] == api_key_sha256(raw_key)
    assert props["workspace_id"] == workspace_uuid
    assert props["workspace"] == "ws-1"


def test_comet_workspace_id_rejects_non_uuid_at_startup() -> None:
    """Operator typo (e.g. ``COMET_WORKSPACE_ID=my-workspace``) must fail
    loudly at Settings construction, not silently corrupt every event.
    """
    with pytest.raises(ValueError, match="not a valid UUID"):
        Settings(comet_workspace_id="not-a-uuid")


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
    # Zero backoff so the retry chain doesn't add real sleeps to the suite.
    client = AnalyticsClient(_settings(), retry_backoff_s=(0.0, 0.0, 0.0))
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

    client = AnalyticsClient(
        _settings(),
        http_client=BlockingClient(),  # type: ignore[arg-type]
        max_queue_size=2,
        retry_backoff_s=(0.0,),  # single attempt; the blocking client makes retries irrelevant
    )
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
    """A 5xx that never recovers must exhaust the retry chain (one POST per
    configured attempt) and then log a single WARNING."""
    route = respx.post(URL).mock(return_value=httpx.Response(500, text="internal error"))
    # Three zero-delay attempts: proves the worker retries and then gives up.
    client = AnalyticsClient(_settings(), retry_backoff_s=(0.0, 0.0, 0.0))
    try:
        with caplog.at_level(logging.WARNING, logger="opik_mcp.analytics"):
            client.track_event("opik_mcp_test", {"k": "v"})
            _drain(client)
    finally:
        client.close()

    assert route.call_count == 3, "expected one POST per configured retry attempt"
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    # Exactly one — the warning fires once after exhaustion, NOT per failed
    # attempt (a regression moving it into the except block would log 3).
    assert len(warning_records) == 1, f"expected one exhaustion WARNING, got {len(warning_records)}"


@respx.mock
def test_retry_succeeds_on_second_attempt() -> None:
    """A transient failure on the first POST must be retried, and the event
    lands on the second attempt (which reuses the now-warm pooled connection).

    This is the core server_started fix: the first cold POST eats the DNS+TLS
    handshake and often errors/times out; without a retry it is lost forever
    even though a second attempt would land instantly.
    """
    route = respx.post(URL).mock(
        side_effect=[httpx.ConnectError("cold handshake"), httpx.Response(200)]
    )
    client = AnalyticsClient(_settings(), retry_backoff_s=(0.0, 0.0))
    try:
        client.track_event("opik_mcp_test", {"k": "v"})
        _drain(client)
    finally:
        client.close()

    assert route.call_count == 2, "expected one failed attempt then a successful retry"


@respx.mock
def test_worker_survives_failed_event_and_delivers_next() -> None:
    """An event that exhausts all retries must NOT kill the worker thread —
    the next enqueued event still gets delivered. Guards the `while True` loop
    against an exception escaping past the per-attempt handling."""
    route = respx.post(URL).mock(
        side_effect=[
            httpx.ConnectError("e1"),  # event 1: attempt 1
            httpx.ConnectError("e1"),  # event 1: attempt 2
            httpx.ConnectError("e1"),  # event 1: attempt 3 → exhausted
            httpx.Response(200),  # event 2: lands
        ]
    )
    client = AnalyticsClient(_settings(), retry_backoff_s=(0.0, 0.0, 0.0))
    try:
        client.track_event("first", {})
        client.track_event("second", {})
        _drain(client)
    finally:
        client.close()

    assert route.call_count == 4, "worker must keep processing after an exhausted event"


@respx.mock
def test_empty_retry_backoff_still_attempts_once() -> None:
    """An empty retry_backoff_s must not silently drop the event with zero
    delivery attempts (and a misleading 'after 0 attempt(s)' warning). It
    normalises to a single immediate attempt — `()` differs from `(0.0,)` by
    one character but must not mean 'never send'."""
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    client = AnalyticsClient(_settings(), retry_backoff_s=())
    try:
        client.track_event("opik_mcp_test", {})
        _drain(client)
    finally:
        client.close()

    assert route.call_count == 1, "empty schedule must still attempt delivery once"


def _has_running_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
