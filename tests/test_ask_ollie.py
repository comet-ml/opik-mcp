import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import pytest

from opik_mcp.ask_ollie import run_ask_ollie
from opik_mcp.comet_client import PodDiscovery
from opik_mcp.config import MissingConfigError, Settings
from opik_mcp.ollie_client import OllieStreamError, OnTick, SSEEvent


def _ev(event: str, payload: dict[str, Any]) -> SSEEvent:
    return SSEEvent(event=event, data={"parent_id": None, "payload": payload})


class FakeCometClient:
    def __init__(self, discovery: PodDiscovery) -> None:
        self._discovery = discovery
        self.workspaces: list[str] = []

    async def discover_pod(self, workspace: str) -> PodDiscovery:
        self.workspaces.append(workspace)
        return self._discovery


class FakeOllieClient:
    def __init__(self, events: list[SSEEvent], *, session_id: str = "sess-1") -> None:
        self._events = events
        self._session_id = session_id
        self.wait_ready_calls = 0
        self.create_body: dict[str, Any] | None = None
        self.create_workspace: str | None = None
        self.stream_session_id: str | None = None
        self.confirms: list[tuple[str, str, str]] = []  # (session_id, tool_use_id, decision)

    async def wait_ready(
        self, compute_url: str, ppauth: str, *, on_tick: OnTick | None = None
    ) -> None:
        self.wait_ready_calls += 1

    async def create_session(
        self, compute_url: str, ppauth: str, workspace: str, body: dict[str, Any]
    ) -> str:
        self.create_body = body
        self.create_workspace = workspace
        return self._session_id

    async def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        self.stream_session_id = session_id
        for evt in self._events:
            yield evt

    async def confirm_session(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        tool_use_id: str,
        decision: str,
    ) -> None:
        self.confirms.append((session_id, tool_use_id, decision))


class _FakeElicitSession:
    """Stand-in for ServerSession capability probe (elicitation tests)."""

    def __init__(self, *, supports: bool) -> None:
        self._supports = supports

    def check_client_capability(self, _capability: Any) -> bool:
        return self._supports


class _FakeRequestContext:
    def __init__(self, session: _FakeElicitSession) -> None:
        self.session = session


class _AcceptedShape:
    def __init__(self, confirm: bool) -> None:
        self.confirm = confirm


class _AcceptedElicit:
    def __init__(self, confirm: bool) -> None:
        self.action = "accept"
        self.data = _AcceptedShape(confirm=confirm)


class _DeclinedElicit:
    action = "decline"
    data = None


class FakeContext:
    """Duck-typed stand-in for fastmcp Context — records calls for assertion.

    We exercise `report_progress`/`info`/`warning`/`elicit` only; the real
    Context has more surface than we use. Typed as Any in call sites via
    `# type: ignore`. By default the host advertises elicitation as
    UNSUPPORTED, which matches the legacy disabled-mode tests (no prompt
    surfaces, fast hard-error path).
    """

    def __init__(
        self,
        *,
        supports_elicitation: bool = False,
        elicit_result: Any = None,
    ) -> None:
        self.progress: list[tuple[float, str | None]] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.elicit_calls: list[str] = []
        self.request_context = _FakeRequestContext(
            _FakeElicitSession(supports=supports_elicitation)
        )
        self._elicit_result = elicit_result

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.progress.append((progress, message))

    async def info(self, msg: str) -> None:
        self.infos.append(msg)

    async def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    async def elicit(self, *, message: str, schema: type) -> Any:
        self.elicit_calls.append(message)
        return self._elicit_result


class _DelayingOllieClient(FakeOllieClient):
    """Like FakeOllieClient but sleeps before yielding the first event.

    Lets us simulate pod silence so the heartbeat coroutine has a chance to
    fire before any real SSE event arrives.
    """

    def __init__(
        self,
        events: list[SSEEvent],
        *,
        pre_stream_delay: float,
        session_id: str = "sess-1",
    ) -> None:
        super().__init__(events, session_id=session_id)
        self._pre_stream_delay = pre_stream_delay

    async def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        self.stream_session_id = session_id
        await anyio.sleep(self._pre_stream_delay)
        for evt in self._events:
            yield evt


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"opik_api_key": "k", "comet_workspace": "ws"}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_happy_path_streams_text_and_returns_session_id() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("thinking_delta", {"delta": "Let me check... "}),
            _ev("message_delta", {"delta": "You have 3 traces."}),
            _ev("message_end", {}),
        ],
        session_id="sess-abc",
    )
    result = await run_ask_ollie(
        query="how many traces?",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert result.text == "Let me check... You have 3 traces."
    assert result.thread_id == "sess-abc"
    assert result.navigate == []
    assert result.complete is True
    assert comet.workspaces == ["ws"]
    assert ollie.wait_ready_calls == 1
    assert ollie.create_body == {"message": "how many traces?"}
    assert ollie.create_workspace == "ws"
    assert ollie.stream_session_id == "sess-abc"


@pytest.mark.anyio
async def test_thread_continuation_forwards_session_id() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})], session_id="t1")
    await run_ask_ollie(
        query="follow up",
        thread_id="t1",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body is not None
    assert ollie.create_body["session_id"] == "t1"


@pytest.mark.anyio
async def test_page_context_sent_as_snapshot() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        page_context="# current view\n- traces table",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body == {
        "message": "q",
        "snapshot": "# current view\n- traces table",
    }


@pytest.mark.anyio
async def test_project_name_sent_as_context() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        project_name="chatbot-prod",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body == {
        "message": "q",
        "context": {"project_name": "chatbot-prod"},
    }


@pytest.mark.anyio
async def test_no_project_means_no_context_key() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body is not None
    assert "context" not in ollie.create_body


@pytest.mark.anyio
async def test_project_and_thread_id_both_forwarded() -> None:
    """The common production path: continuing a thread with project scope."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})], session_id="t1")
    await run_ask_ollie(
        query="follow up",
        thread_id="t1",
        project_name="chatbot-prod",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body == {
        "message": "follow up",
        "session_id": "t1",
        "context": {"project_name": "chatbot-prod"},
    }


@pytest.mark.anyio
async def test_empty_string_project_name_omits_context() -> None:
    """Empty string would deserialize as a valid-but-broken project filter on the pod."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        project_name="",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body is not None
    assert "context" not in ollie.create_body


@pytest.mark.anyio
async def test_project_and_snapshot_coexist() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        project_name="chatbot-prod",
        page_context="# view",
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body == {
        "message": "q",
        "context": {"project_name": "chatbot-prod"},
        "snapshot": "# view",
    }


@pytest.mark.anyio
async def test_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPIK_API_KEY", "COMET_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingConfigError):
        await run_ask_ollie(query="q", settings=Settings())


@pytest.mark.anyio
async def test_error_event_raises_stream_error() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("error", {"message": "boom", "recoverable": False})])
    with pytest.raises(OllieStreamError, match="boom"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)


@pytest.mark.anyio
async def test_error_event_with_missing_message_uses_generic_string() -> None:
    """A malformed pod `error` event must not leak the raw payload dict to the host."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("error", {"code": 500, "recoverable": False})])
    with pytest.raises(OllieStreamError, match="Unknown pod error"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)


@pytest.mark.anyio
async def test_non_dict_sse_data_treated_as_no_op_event() -> None:
    """`OllieClient.stream_events` falls back to `{"raw": <str>}` on malformed
    SSE data — and could in principle also surface non-dict types (a pod
    serialization bug, or a future event type that puts a string at the top
    level). `ask_ollie` defends against this by treating non-dict `sse.data`
    as having an empty payload (line 350). Without this, the next dict access
    would AttributeError and tear down the whole turn.

    This drives the defensive branch with a string-typed `sse.data` to prove
    the turn completes cleanly when followed by a valid `message_end`."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    weird = SSEEvent(event="message_delta", data="not-a-dict")  # type: ignore[arg-type]
    ollie = FakeOllieClient([weird, _ev("message_end", {})])

    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    # No text was extractable from the malformed event, but the stream
    # completed normally — that's the contract.
    assert result.complete is True
    assert result.text == "(no response)"


@pytest.mark.anyio
async def test_non_dict_payload_field_treated_as_empty() -> None:
    """`ask_ollie.py:351-352` defends against `sse.data["payload"]` being
    a non-dict (e.g. a string). A message_delta whose payload is a string
    must NOT crash with `'str' object has no attribute 'get'` — it should
    simply produce no text and let the stream continue to message_end."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    bad = SSEEvent(event="message_delta", data={"parent_id": None, "payload": "oops"})
    ollie = FakeOllieClient([bad, _ev("message_delta", {"delta": "hi"}), _ev("message_end", {})])

    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    # Only the well-formed delta contributes text.
    assert result.text == "hi"
    assert result.complete is True


@pytest.mark.anyio
async def test_error_propagates_unwrapped_through_task_group_when_ctx_set() -> None:
    """With ctx set, the heartbeat task runs alongside the SSE loop. The pod
    `error` SSE must still raise OllieStreamError directly — not as a
    BaseExceptionGroup wrapping it together with the heartbeat's CancelledError.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("error", {"message": "boom"})])
    fake_ctx = FakeContext()
    with pytest.raises(OllieStreamError, match="boom"):
        await run_ask_ollie(
            query="q",
            ctx=fake_ctx,  # type: ignore[arg-type]
            settings=_settings(opik_mcp_heartbeat_interval_s=0.05),
            comet_client=comet,
            ollie_client=ollie,
        )


@pytest.mark.anyio
async def test_confirm_required_auto_approves() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add an item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)
    assert ollie.confirms == [("sess-1", "tu-1", "yes")]


@pytest.mark.anyio
async def test_confirm_required_emits_audit_row(caplog: pytest.LogCaptureFixture) -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1", "expected": "hi"},
                    "summary": "add an item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    with caplog.at_level(logging.INFO, logger="opik_mcp.audit"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)

    audit_records = [r for r in caplog.records if r.name == "opik_mcp.audit"]
    assert len(audit_records) == 1
    # Lazy %s format: parse args[0] directly instead of getMessage() to stay
    # robust under structlog/json-logger interception (Phase 2).
    args = audit_records[0].args
    assert isinstance(args, tuple)
    payload = args[0]
    assert isinstance(payload, str)
    row = json.loads(payload)
    assert row["event"] == "ollie_write_auto_approved"
    assert row["workspace"] == "ws"
    assert row["session_id"] == "sess-1"
    assert row["tool"] == "ask_ollie"
    assert row["target_tool"] == "add_test_suite_item"
    assert row["tool_use_id"] == "tu-1"
    assert row["summary"] == "add an item"
    assert row["input"] == {"suite": "s1", "expected": "hi"}
    assert row["auto_approved"] is True


@pytest.mark.anyio
async def test_confirm_required_missing_tool_use_id_no_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("confirm_required", {"tool_name": "delete_thing"}),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    with caplog.at_level(logging.WARNING, logger="opik_mcp.ask_ollie"):
        result = await run_ask_ollie(
            query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
        )
    assert ollie.confirms == []
    assert result.complete is True
    warnings = [
        r for r in caplog.records if r.name == "opik_mcp.ask_ollie" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "missing tool_use_id" in warnings[0].getMessage()


@pytest.mark.anyio
async def test_navigate_path_with_search_collected() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("navigate", {"path": "/traces", "search": {"project": "demo"}}),
            _ev("navigate", {"path": "/dashboard"}),
            _ev("message_end", {}),
        ]
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.navigate == ["/traces?project=demo", "/dashboard"]


@pytest.mark.anyio
async def test_empty_stream_returns_placeholder() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})], session_id="sess-1")
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == "(no response)"
    assert result.thread_id == "sess-1"


@pytest.mark.anyio
async def test_stream_truncated_without_message_end_marks_incomplete() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [_ev("message_delta", {"delta": "partial"})],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == "partial"
    assert result.thread_id == "sess-1"
    assert result.complete is False


@pytest.mark.anyio
async def test_message_cancelled_marks_partial_not_complete() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("message_delta", {"delta": "in progress..."}),
            _ev("message_cancelled", {}),
        ],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == "in progress..."
    assert result.complete is False
    assert result.cancelled is True


@pytest.mark.anyio
async def test_navigate_with_non_scalar_search_keeps_path() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "navigate",
                {
                    "path": "/traces",
                    "search": {"filters": [{"col": "status", "op": "eq", "val": "fail"}]},
                },
            ),
            _ev("message_end", {}),
        ]
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    # All search values are non-scalar → query string dropped, path preserved.
    assert result.navigate == ["/traces"]


@pytest.mark.anyio
async def test_every_sse_event_emits_progress() -> None:
    """Universal per-event progress keeps host tool-call timeouts alive.

    Hosts that reset their timeout clock on `notifications/progress` (per MCP
    spec §Lifecycle/Timeouts) need at least one progress tick per event —
    info-level messages don't count.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("thinking_delta", {"delta": "..."}),
            _ev("message_delta", {"delta": "answer"}),
            _ev("tool_call_start", {"tool": "list_traces"}),
            _ev("tool_call_end", {"tool": "list_traces"}),
            _ev("compaction_start", {}),
            _ev("compaction_end", {}),
            _ev("message_end", {}),
        ]
    )
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )

    messages = [m for _, m in fake_ctx.progress]
    assert messages == [
        "thinking_delta",
        "message_delta",
        "tool_call_start",
        "tool_call_end",
        "compaction_start",
        "compaction_end",
        "message_end",
    ]

    # MCP spec: progress values MUST strictly increase.
    values = [v for v, _ in fake_ctx.progress]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


@pytest.mark.anyio
async def test_audit_failure_skips_confirm_post(caplog: pytest.LogCaptureFixture) -> None:
    """If audit.write_auto_approval raises, the pod confirm POST MUST be suppressed.

    YOLO mode treats the audit log as the only safety net — silently sending
    `decision="yes"` without an audit row would defeat the contract documented
    in ADR 0005.
    """
    import opik_mcp.audit as audit_mod

    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )

    def _boom(**_: Any) -> None:
        raise RuntimeError("audit backend exploded")

    original = audit_mod.write_auto_approval
    audit_mod.write_auto_approval = _boom  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.ERROR, logger="opik_mcp.ask_ollie"):
            result = await run_ask_ollie(
                query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
            )
    finally:
        audit_mod.write_auto_approval = original

    assert ollie.confirms == []
    assert result.complete is True  # stream still completed; only the confirm was suppressed
    audit_errors = [
        r for r in caplog.records if r.name == "opik_mcp.ask_ollie" and r.levelno == logging.ERROR
    ]
    assert len(audit_errors) == 1
    msg = audit_errors[0].getMessage()
    assert "audit_failed" in msg
    assert "tu-1" in msg


@pytest.mark.anyio
async def test_pod_silence_emits_heartbeat_progress() -> None:
    """A silent pod must still produce progress ticks via the watchdog heartbeat.

    Without this, Ollie operations longer than the host's tool-call timeout
    (Claude Code, MCP Inspector default 60s) would silently fail with no
    response surfaced to the user.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _DelayingOllieClient(
        [_ev("message_end", {})],
        pre_stream_delay=0.2,
    )
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        settings=_settings(opik_mcp_heartbeat_interval_s=0.05),
        comet_client=comet,
        ollie_client=ollie,
    )

    messages = [m for _, m in fake_ctx.progress]
    heartbeats = [m for m in messages if m == "streaming"]
    assert heartbeats, "heartbeat watchdog must fire during pod silence"
    # The heartbeat must arrive BEFORE the real event — that's what proves it
    # actually filled the silent gap (vs. firing once after the SSE finishes).
    first_heartbeat_idx = messages.index("streaming")
    message_end_idx = messages.index("message_end")
    assert first_heartbeat_idx < message_end_idx, (
        f"heartbeat must fire before message_end during silence: {messages}"
    )
    # Heartbeats share the events_seen counter — values still strictly increase.
    values = [v for v, _ in fake_ctx.progress]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


@pytest.mark.anyio
async def test_stream_idle_timeout_aborts_call() -> None:
    """A pod that goes silent past the idle threshold must abort with a typed error.

    Without this, a stalled pod + a working heartbeat would keep the host
    waiting forever (every heartbeat resets the host's tool-call timeout).
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    # 0.5s pre-stream silence with a 0.1s idle threshold → watchdog must fire.
    ollie = _DelayingOllieClient(
        [_ev("message_end", {})],
        pre_stream_delay=0.5,
    )
    with pytest.raises(OllieStreamError, match="idle"):
        await run_ask_ollie(
            query="q",
            ctx=FakeContext(),  # type: ignore[arg-type]
            settings=_settings(
                opik_mcp_heartbeat_interval_s=0.05,
                opik_mcp_stream_idle_timeout_s=0.1,
            ),
            comet_client=comet,
            ollie_client=ollie,
        )


@pytest.mark.anyio
async def test_confirm_session_failure_raises_typed_error() -> None:
    """A confirm POST failure must surface as OllieStreamError, not a raw httpx exception.

    The audit row is already written before the POST attempt, so callers (and
    the host LLM) need a typed error to distinguish "intent recorded but not
    delivered" from generic transport errors.
    """

    class _ExplodingConfirmOllieClient(FakeOllieClient):
        async def confirm_session(
            self,
            compute_url: str,
            ppauth: str,
            workspace: str,
            session_id: str,
            *,
            tool_use_id: str,
            decision: str,
        ) -> None:
            raise RuntimeError("network exploded")

    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _ExplodingConfirmOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    with pytest.raises(OllieStreamError, match="confirm POST failed"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)


@pytest.mark.anyio
async def test_heartbeat_disabled_when_interval_zero() -> None:
    """Setting opik_mcp_heartbeat_interval_s=0 must disable the heartbeat entirely.

    Both as an explicit opt-out and as a defensive guard against a spin loop
    (anyio.sleep(0) in a while True would starve the SSE consumer).
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _DelayingOllieClient(
        [_ev("message_end", {})],
        pre_stream_delay=0.1,
    )
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        settings=_settings(
            opik_mcp_heartbeat_interval_s=0,
            opik_mcp_stream_idle_timeout_s=0,  # disable idle watchdog too
        ),
        comet_client=comet,
        ollie_client=ollie,
    )
    heartbeats = [m for _, m in fake_ctx.progress if m == "streaming"]
    assert heartbeats == [], (
        f"heartbeat must be suppressed when interval=0; got {fake_ctx.progress}"
    )
    # Real SSE events still emit progress.
    assert any(m == "message_end" for _, m in fake_ctx.progress)


@pytest.mark.anyio
async def test_duplicate_tool_use_id_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A repeated confirm_required (pod retry, stream reconnect) must NOT send
    `decision="yes"` twice. YOLO would otherwise duplicate-write non-idempotent
    pod tools (add_test_suite_item, score) and produce two matching audit rows.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add item",
                },
            ),
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",  # same id → must be skipped
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    with caplog.at_level(logging.WARNING, logger="opik_mcp.ask_ollie"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)
    # Only one POST and one audit row, despite two events.
    assert ollie.confirms == [("sess-1", "tu-1", "yes")]
    duplicates = [
        r
        for r in caplog.records
        if r.name == "opik_mcp.ask_ollie"
        and r.levelno == logging.WARNING
        and "duplicate tool_use_id" in r.getMessage()
    ]
    assert len(duplicates) == 1


@pytest.mark.anyio
async def test_warmup_and_sse_progress_are_strictly_monotonic() -> None:
    """Warmup `on_tick` and SSE per-event progress share one progressToken in
    MCP. The values MUST strictly increase across the whole call — a regression
    that sent warmup as `elapsed` (10.0, 20.0, ...) then SSE as 1, 2 would
    violate the spec.
    """

    class _WarmupTickingOllieClient(FakeOllieClient):
        async def wait_ready(
            self, compute_url: str, ppauth: str, *, on_tick: OnTick | None = None
        ) -> None:
            self.wait_ready_calls += 1
            if on_tick is not None:
                # Simulate three warmup ticks at elapsed = 5s, 10s, 15s.
                await on_tick(5.0)
                await on_tick(10.0)
                await on_tick(15.0)

    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = _WarmupTickingOllieClient(
        [
            _ev("message_delta", {"delta": "answer"}),
            _ev("message_end", {}),
        ]
    )
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    values = [v for v, _ in fake_ctx.progress]
    assert values == sorted(values)
    assert len(set(values)) == len(values), f"progress values must be unique: {values}"
    # Warmup ticks come first, then SSE — verify the SSE values don't reset.
    warmup_count = 3
    assert values[warmup_count - 1] < values[warmup_count]


@pytest.mark.anyio
async def test_progress_values_are_integers_on_wire() -> None:
    """MCP `notifications/progress.progress` should be integer-typed for strict
    hosts; sending `1.0` instead of `1` triggers `type: integer` validation
    failures on hosts that lock down their JSON Schema.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    for v, _ in fake_ctx.progress:
        assert isinstance(v, int), f"progress must be int, got {type(v).__name__}: {v!r}"


@pytest.mark.anyio
async def test_unknown_event_does_not_crash_and_stream_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forward-compat path: pod may add new event types we don't handle yet.
    The stream must continue and complete on the eventual `message_end`.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("some_future_event_we_dont_know", {"payload": "anything"}),
            _ev("message_delta", {"delta": "ok"}),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    with caplog.at_level(logging.DEBUG, logger="opik_mcp.ask_ollie"):
        result = await run_ask_ollie(
            query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
        )
    assert result.complete is True
    assert result.text == "ok"
    unknowns = [
        r
        for r in caplog.records
        if r.name == "opik_mcp.ask_ollie" and "unknown_event" in r.getMessage()
    ]
    assert len(unknowns) == 1


@pytest.mark.anyio
async def test_confirm_flow_with_ctx_set_preserves_audit_then_post_order() -> None:
    """The audit row MUST land before the confirm POST even when `ctx.info(...)`
    yields between them. A future refactor that moves `ctx.info` before the
    audit write would silently break ADR 0005's audit-then-POST invariant.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev(
                "confirm_required",
                {
                    "tool_use_id": "tu-1",
                    "tool_name": "add_test_suite_item",
                    "input": {"suite": "s1"},
                    "summary": "add item",
                },
            ),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    fake_ctx = FakeContext()
    await run_ask_ollie(
        query="q",
        ctx=fake_ctx,  # type: ignore[arg-type]
        # Disable heartbeat to keep ctx.infos list noise-free for the assertion.
        settings=_settings(opik_mcp_heartbeat_interval_s=0),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.confirms == [("sess-1", "tu-1", "yes")]
    # `ctx.info` MUST have been called with the auto-approval message.
    assert any("Ollie auto-approved" in m for m in fake_ctx.infos)


@pytest.mark.anyio
async def test_attach_resources_is_silently_dropped_from_body() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient([_ev("message_end", {})])
    await run_ask_ollie(
        query="q",
        attach_resources=["trace-1", "trace-2"],
        settings=_settings(),
        comet_client=comet,
        ollie_client=ollie,
    )
    assert ollie.create_body is not None
    assert "attach_resources" not in ollie.create_body


# ---------------------------------------------------------------------------
# OPIK_MCP_AUTO_APPROVE opt-out + end-of-stream footer + session-complete log
# ---------------------------------------------------------------------------


def _confirm_event(
    tool_use_id: str,
    *,
    tool_name: str = "add_test_suite_item",
    summary: str | None = "add an item",
    suite: str = "s1",
) -> SSEEvent:
    payload: dict[str, Any] = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "input": {"suite": suite},
    }
    if summary is not None:
        payload["summary"] = summary
    return _ev("confirm_required", payload)


@pytest.mark.anyio
async def test_auto_approve_disabled_surfaces_typed_error_no_audit_no_post(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Opt-out path: confirm_required → OllieStreamError carrying pod summary.

    The whole point of OPIK_MCP_AUTO_APPROVE=disabled is that NOTHING happens
    server-side without the user's say-so: no audit row (the approval didn't
    happen), no confirm POST (we tell the pod nothing — let it time out), and
    the host LLM sees the pod-supplied `summary` so it can show the user what
    was requested and let them re-issue manually.
    """
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [_confirm_event("tu-1", summary="add 'foo' to suite 'bar'"), _ev("message_end", {})],
        session_id="sess-1",
    )
    with (
        caplog.at_level(logging.INFO, logger="opik_mcp.audit"),
        pytest.raises(OllieStreamError) as exc_info,
    ):
        await run_ask_ollie(
            query="q",
            settings=_settings(opik_mcp_auto_approve="disabled"),
            comet_client=comet,
            ollie_client=ollie,
        )

    # 1. Error message carries the pod-supplied summary so the LLM/user can decide
    assert "add 'foo' to suite 'bar'" in str(exc_info.value)
    assert "OPIK_MCP_AUTO_APPROVE" in str(exc_info.value)
    # 2. No confirm POST was sent
    assert ollie.confirms == []
    # 3. No audit row was emitted (declined-by-policy ≠ approved)
    audit_records = [r for r in caplog.records if r.name == "opik_mcp.audit"]
    assert audit_records == []


@pytest.mark.anyio
async def test_auto_approve_disabled_falls_back_when_summary_missing() -> None:
    """Pod may ship confirm_required without a `summary` field; the error must
    still be informative — fall back to tool_name, then tool_use_id."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [_confirm_event("tu-xyz", summary=None, tool_name="delete_dataset")],
        session_id="sess-1",
    )
    with pytest.raises(OllieStreamError, match="delete_dataset"):
        await run_ask_ollie(
            query="q",
            settings=_settings(opik_mcp_auto_approve="disabled"),
            comet_client=comet,
            ollie_client=ollie,
        )


@pytest.mark.anyio
async def test_no_approvals_no_footer_appended() -> None:
    """If the turn auto-approved nothing, the result text MUST NOT carry a
    footer. Otherwise we'd be tacking a misleading "Auto-approved during this
    turn:" line onto every response, which makes the feature obvious noise
    instead of a useful safety signal."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("message_delta", {"delta": "no tools used."}),
            _ev("message_end", {}),
        ]
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == "no tools used."
    assert "Auto-approved" not in result.text


@pytest.mark.anyio
async def test_single_approval_renders_footer_with_summary() -> None:
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _ev("message_delta", {"delta": "Done."}),
            _confirm_event("tu-1", tool_name="add_test_suite_item", summary="add 'foo'"),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    # Content first, then footer separated by a blank line so chat hosts that
    # render newlines (most do) keep them visually distinct.
    assert result.text == (
        "Done.\n\nAuto-approved during this turn: add_test_suite_item (add 'foo')"
    )


@pytest.mark.anyio
async def test_three_approvals_render_verbatim_no_truncation() -> None:
    """Three approvals = under the 5-entry cap, so all three appear verbatim in
    order. This is the case that proves we render the actual approvals (not a
    summary or a count) when the list is short."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _confirm_event("tu-1", tool_name="add_test_suite_item", summary="add foo"),
            _confirm_event("tu-2", tool_name="score", summary="score 0.9"),
            _confirm_event("tu-3", tool_name="comment", summary="add comment"),
            _ev("message_delta", {"delta": "Done."}),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == (
        "Done.\n\nAuto-approved during this turn: "
        "add_test_suite_item (add foo), score (score 0.9), comment (add comment)"
    )


@pytest.mark.anyio
async def test_six_approvals_truncate_with_and_n_more() -> None:
    """Six approvals = over the 5-entry cap. First five must appear verbatim,
    remainder collapses to `…and N more` so the footer can't dominate the
    response in chat-style UIs."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    events: list[SSEEvent] = [
        _confirm_event(f"tu-{i}", tool_name=f"tool_{i}", summary=f"sum {i}") for i in range(1, 7)
    ]
    events.append(_ev("message_end", {}))
    ollie = FakeOllieClient(events, session_id="sess-1")

    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    expected = (
        "Auto-approved during this turn: "
        "tool_1 (sum 1), tool_2 (sum 2), tool_3 (sum 3), tool_4 (sum 4), "
        "tool_5 (sum 5), …and 1 more"
    )
    assert result.text == expected


@pytest.mark.anyio
async def test_approval_without_summary_shows_tool_only() -> None:
    """Footer must degrade gracefully when the pod omits `summary`: render just
    the tool name (NOT `tool_name (None)`, which leaks the missing-field as if
    it were the action description)."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _confirm_event("tu-1", tool_name="add_test_suite_item", summary=None),
            _ev("message_delta", {"delta": "Done."}),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    assert result.text == "Done.\n\nAuto-approved during this turn: add_test_suite_item"
    assert "(None)" not in result.text


@pytest.mark.anyio
async def test_footer_alone_when_text_buffer_empty() -> None:
    """A turn where the pod only ran tools (no message text) but auto-approved
    them must still surface the footer. Falling back to "(no response)" here
    would hide the fact that real writes happened."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _confirm_event("tu-1", tool_name="score", summary="score 0.5"),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    result = await run_ask_ollie(
        query="q", settings=_settings(), comet_client=comet, ollie_client=ollie
    )
    # No "(no response)" prefix — the footer is the response.
    assert result.text == "Auto-approved during this turn: score (score 0.5)"
    assert "(no response)" not in result.text


@pytest.mark.anyio
async def test_session_complete_log_includes_approval_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The end-of-call structured log line must include the auto-approval count
    so anyone tailing the MCP server log can spot turns where Ollie wrote
    things without re-reading every audit row."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _confirm_event("tu-1", tool_name="score", summary="s"),
            _confirm_event("tu-2", tool_name="comment", summary="c"),
            _ev("message_end", {}),
        ],
        session_id="sess-7",
    )
    with caplog.at_level(logging.INFO, logger="opik_mcp.ask_ollie"):
        await run_ask_ollie(query="q", settings=_settings(), comet_client=comet, ollie_client=ollie)
    completes = [
        r
        for r in caplog.records
        if r.name == "opik_mcp.ask_ollie" and "session_complete" in r.getMessage()
    ]
    assert len(completes) == 1
    msg = completes[0].getMessage()
    assert "auto_approvals=2" in msg
    assert "session_id=sess-7" in msg
    assert "completion=message_end" in msg


@pytest.mark.anyio
async def test_disabled_mode_skips_audit_row_explicitly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Belt-and-suspenders: when disabled, even a confirm_required that goes
    through the normal flow path must not produce an audit row. Splits the
    coverage from the typed-error test so a refactor that emits-then-raises
    can't accidentally pass the typed-error assertion while still leaving an
    orphan audit row behind."""
    import opik_mcp.audit as audit_mod

    write_calls: list[dict[str, Any]] = []
    original = audit_mod.write_auto_approval

    def _recording(**kwargs: Any) -> Any:
        write_calls.append(kwargs)
        return original(**kwargs)

    audit_mod.write_auto_approval = _recording
    try:
        comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
        ollie = FakeOllieClient(
            [_confirm_event("tu-1"), _ev("message_end", {})],
            session_id="sess-1",
        )
        with pytest.raises(OllieStreamError):
            await run_ask_ollie(
                query="q",
                settings=_settings(opik_mcp_auto_approve="disabled"),
                comet_client=comet,
                ollie_client=ollie,
            )
    finally:
        audit_mod.write_auto_approval = original
    assert write_calls == []


# --- Elicitation-augmented disabled mode (OPIK-6567) ------------------- #


@pytest.mark.anyio
async def test_disabled_with_elicit_accept_falls_through_to_normal_flow() -> None:
    """When the host supports elicitation and the user accepts the prompt,
    `disabled` mode should land in the normal audit + confirm path -- the
    user's per-action approval substitutes for the YOLO flag for this one
    tool call. Without this branch, `disabled` mode is unusable on hosts
    that DO support elicitation, defeating the point of OPIK-6567."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [
            _confirm_event("tu-1", summary="add 'foo' to suite 'bar'"),
            _ev("message_end", {}),
        ],
        session_id="sess-1",
    )
    ctx = FakeContext(
        supports_elicitation=True,
        elicit_result=_AcceptedElicit(confirm=True),
    )
    result = await run_ask_ollie(
        query="q",
        settings=_settings(opik_mcp_auto_approve="disabled"),
        comet_client=comet,
        ollie_client=ollie,
        ctx=ctx,  # type: ignore[arg-type]
    )
    # Confirm POST was sent (the per-action approval routed us into the
    # normal flow rather than the legacy hard-error path).
    assert ollie.confirms == [("sess-1", "tu-1", "yes")]
    # The result stream finished normally -- not aborted via OllieStreamError.
    assert result.complete is True
    # The prompt actually reached the host.
    assert len(ctx.elicit_calls) == 1
    assert "add 'foo'" in ctx.elicit_calls[0]


@pytest.mark.anyio
async def test_disabled_with_elicit_decline_still_raises() -> None:
    """User said no via the prompt: same observable behavior as the legacy
    hard-error path. Pinning this guarantees we can't accidentally swallow
    a denial into a quiet no-op (which would leave the pod hanging waiting
    for a confirm POST we never sent)."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [_confirm_event("tu-1", summary="add 'foo'"), _ev("message_end", {})],
        session_id="sess-1",
    )
    ctx = FakeContext(
        supports_elicitation=True,
        elicit_result=_DeclinedElicit(),
    )
    with pytest.raises(OllieStreamError, match="add 'foo'"):
        await run_ask_ollie(
            query="q",
            settings=_settings(opik_mcp_auto_approve="disabled"),
            comet_client=comet,
            ollie_client=ollie,
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert ollie.confirms == []
    assert len(ctx.elicit_calls) == 1


@pytest.mark.anyio
async def test_disabled_without_elicitation_capability_keeps_legacy_hard_error() -> None:
    """Hosts without the elicitation capability MUST behave exactly as
    before OPIK-6567 -- a typed error carrying the pod summary, no audit,
    no confirm POST. This is the original disabled-mode contract."""
    comet = FakeCometClient(PodDiscovery(compute_url="https://pod", ppauth="ppa"))
    ollie = FakeOllieClient(
        [_confirm_event("tu-1", summary="risky"), _ev("message_end", {})],
        session_id="sess-1",
    )
    ctx = FakeContext(
        supports_elicitation=False,  # legacy host
        elicit_result=_AcceptedElicit(confirm=True),  # would-be accept if asked
    )
    with pytest.raises(OllieStreamError, match="risky"):
        await run_ask_ollie(
            query="q",
            settings=_settings(opik_mcp_auto_approve="disabled"),
            comet_client=comet,
            ollie_client=ollie,
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert ollie.confirms == []
    assert ctx.elicit_calls == []  # the helper bypassed `ctx.elicit` entirely
