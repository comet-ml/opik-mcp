import httpx
import pytest
import respx

from opik_mcp.ollie_client import (
    OllieAuthError,
    OllieClient,
    OllieStreamError,
    PodNotReadyError,
    SSEEvent,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_wait_ready_immediate_success() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        client = OllieClient(sleeper=fake_sleep)
        await client.wait_ready("https://pod.test", "ppa")

    assert sleeps == []


@pytest.mark.anyio
async def test_wait_ready_200_but_not_ok_keeps_polling() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    responses = iter(
        [
            httpx.Response(200, json={"status": "warming"}),
            httpx.Response(200, json={"status": "ok"}),
        ]
    )
    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(side_effect=lambda req: next(responses))
        client = OllieClient(ready_timeout_s=10.0, ready_interval_s=2.0, sleeper=fake_sleep)
        await client.wait_ready("https://pod.test", "ppa")
    assert sleeps == [2.0]


@pytest.mark.anyio
async def test_wait_ready_warms_up_then_succeeds() -> None:
    responses = iter(
        [httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"status": "ok"})]
    )
    ticks: list[float] = []
    sleeps: list[float] = []

    async def on_tick(elapsed: float) -> None:
        ticks.append(elapsed)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(side_effect=lambda req: next(responses))
        client = OllieClient(ready_timeout_s=120.0, ready_interval_s=2.0, sleeper=fake_sleep)
        await client.wait_ready("https://pod.test", "ppa", on_tick=on_tick)

    assert ticks == [0.0, 2.0]
    assert sleeps == [2.0, 2.0]


@pytest.mark.anyio
async def test_wait_ready_timeout_raises() -> None:
    async def fake_sleep(_: float) -> None:
        return None

    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(return_value=httpx.Response(503))
        client = OllieClient(ready_timeout_s=4.0, ready_interval_s=2.0, sleeper=fake_sleep)
        with pytest.raises(PodNotReadyError):
            await client.wait_ready("https://pod.test", "ppa")


@pytest.mark.anyio
async def test_wait_ready_treats_connect_timeout_as_warming() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Inject ConnectTimeout (the cold-start case) then a successful ready.
    call_count = {"n": 0}

    def side_effect(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectTimeout("dns/tcp not up", request=req)
        return httpx.Response(200, json={"status": "ok"})

    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(side_effect=side_effect)
        client = OllieClient(ready_timeout_s=10.0, ready_interval_s=2.0, sleeper=fake_sleep)
        await client.wait_ready("https://pod.test", "ppa")

    assert sleeps == [2.0]
    assert call_count["n"] == 2


@pytest.mark.anyio
async def test_wait_ready_auth_rejection_raises() -> None:
    async def fake_sleep(_: float) -> None:
        return None

    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/health/ready").mock(return_value=httpx.Response(401))
        client = OllieClient(sleeper=fake_sleep)
        with pytest.raises(OllieAuthError):
            await client.wait_ready("https://pod.test", "ppa")


@pytest.mark.anyio
async def test_create_session_returns_session_id_and_sends_headers() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        route = mock.post("/sessions").mock(
            return_value=httpx.Response(200, json={"session_id": "sess-1"})
        )
        client = OllieClient()
        sid = await client.create_session("https://pod.test", "ppa", "ws", {"message": "hi"})

    assert sid == "sess-1"
    sent = route.calls.last.request
    assert sent.headers["cookie"] == "PPAUTH=ppa; sessionToken=ppa"
    assert sent.headers["comet-workspace"] == "ws"
    assert sent.read() == b'{"message":"hi"}'


@pytest.mark.anyio
async def test_create_session_raises_when_session_id_missing() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.post("/sessions").mock(return_value=httpx.Response(200, json={}))
        client = OllieClient()
        with pytest.raises(OllieStreamError, match="session_id"):
            await client.create_session("https://pod.test", "ppa", "ws", {"message": "q"})


@pytest.mark.anyio
async def test_create_session_401_raises_auth_error() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.post("/sessions").mock(return_value=httpx.Response(401))
        client = OllieClient()
        with pytest.raises(OllieAuthError):
            await client.create_session("https://pod.test", "ppa", "ws", {"message": "q"})


@pytest.mark.anyio
async def test_stream_events_parses_envelope_with_payload() -> None:
    sse_body = (
        b'event: thinking_delta\ndata: {"parent_id": null, "payload": {"delta": "hi"}}\n\n'
        b'event: message_delta\ndata: {"parent_id": null, "payload": {"delta": " there"}}\n\n'
        b'event: message_end\ndata: {"parent_id": null, "payload": {}}\n\n'
    )
    with respx.mock(base_url="https://pod.test") as mock:
        route = mock.get("/sessions/sess-1/stream").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body,
            )
        )
        client = OllieClient()
        events: list[SSEEvent] = [
            evt async for evt in client.stream_events("https://pod.test", "ppa", "ws", "sess-1")
        ]

    assert [e.event for e in events] == ["thinking_delta", "message_delta", "message_end"]
    assert events[0].data == {"parent_id": None, "payload": {"delta": "hi"}}
    sent = route.calls.last.request
    assert sent.headers["cookie"] == "PPAUTH=ppa; sessionToken=ppa"
    assert sent.headers["comet-workspace"] == "ws"


@pytest.mark.anyio
async def test_stream_events_sends_last_event_id() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        route = mock.get("/sessions/sess-1/stream").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"",
            )
        )
        client = OllieClient()
        async for _ in client.stream_events(
            "https://pod.test", "ppa", "ws", "sess-1", last_event_id=42
        ):
            pass

    sent = route.calls.last.request
    assert sent.headers["last-event-id"] == "42"


@pytest.mark.anyio
async def test_stream_events_401_raises() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/sessions/sess-1/stream").mock(return_value=httpx.Response(401))
        client = OllieClient()
        with pytest.raises(OllieAuthError):
            async for _ in client.stream_events("https://pod.test", "ppa", "ws", "sess-1"):
                pass


@pytest.mark.anyio
async def test_stream_events_malformed_json_data_falls_back_to_raw() -> None:
    """A pod that ships a garbled `data:` line (transient encoding bug, proxy
    rewrite, etc.) must NOT crash the stream — the consumer falls back to
    {"raw": <original>} so the caller sees the event arrived without trying
    to parse it. Without this branch covered, a single bad event would surface
    as a json.JSONDecodeError to ask_ollie and tear down the whole turn."""
    sse_body = (
        b"event: weird\ndata: this-is-not-json\n\n"
        b'event: message_end\ndata: {"parent_id": null, "payload": {}}\n\n'
    )
    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/sessions/sess-1/stream").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body,
            )
        )
        client = OllieClient()
        events: list[SSEEvent] = [
            evt async for evt in client.stream_events("https://pod.test", "ppa", "ws", "sess-1")
        ]

    assert [e.event for e in events] == ["weird", "message_end"]
    # The bad event surfaces as the raw string under "raw" — the downstream
    # consumer can decide to skip it (ask_ollie does, via the isinstance guard).
    assert events[0].data == {"raw": "this-is-not-json"}


@pytest.mark.anyio
async def test_stream_events_404_raises_stream_error_not_http_error() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.get("/sessions/sess-1/stream").mock(return_value=httpx.Response(404))
        client = OllieClient()
        with pytest.raises(OllieStreamError, match="not found"):
            async for _ in client.stream_events("https://pod.test", "ppa", "ws", "sess-1"):
                pass


@pytest.mark.anyio
async def test_confirm_session_posts_tool_use_id_and_decision() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        route = mock.post("/sessions/sess-1/confirm").mock(return_value=httpx.Response(200))
        client = OllieClient()
        await client.confirm_session(
            "https://pod.test", "ppa", "ws", "sess-1", tool_use_id="tu-1", decision="no"
        )

    sent = route.calls.last.request
    assert sent.headers["cookie"] == "PPAUTH=ppa; sessionToken=ppa"
    assert sent.headers["comet-workspace"] == "ws"
    assert sent.read() == b'{"tool_use_id":"tu-1","decision":"no"}'


@pytest.mark.anyio
async def test_confirm_session_raises_on_auth_failure() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.post("/sessions/sess-1/confirm").mock(return_value=httpx.Response(401))
        client = OllieClient()
        with pytest.raises(OllieAuthError):
            await client.confirm_session(
                "https://pod.test", "ppa", "ws", "sess-1", tool_use_id="tu-1", decision="no"
            )


@pytest.mark.anyio
async def test_confirm_session_raises_on_5xx() -> None:
    with respx.mock(base_url="https://pod.test") as mock:
        mock.post("/sessions/sess-1/confirm").mock(return_value=httpx.Response(503))
        client = OllieClient()
        with pytest.raises(httpx.HTTPStatusError):
            await client.confirm_session(
                "https://pod.test", "ppa", "ws", "sess-1", tool_use_id="tu-1", decision="no"
            )
