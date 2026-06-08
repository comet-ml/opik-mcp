"""Enforce §4.5 — no user prose ever appears in an analytics event.

Drives the *real* MCP tool entry points (server.read, server.list_entities,
server.write, run_ask_ollie) so the wrapper's `props_fn` is exercised on
every call. Each test:

1. Calls the actual tool with PII-shaped inputs.
2. Asserts the wrapper emitted `tool_called` (an empty recorder is a bug, not
   a privacy guarantee — that was the old failure mode).
3. Asserts every FORBIDDEN substring is absent from the serialized event.
4. Asserts the bucketed signal that REPLACED the raw input is present and
   correct (`id_kind`, `had_name_filter`, `is_batch`, etc.).

Without (2) and (4), the privacy test passes for any broken implementation
that simply drops the event entirely.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from opik_mcp.comet_client import PodDiscovery
from opik_mcp.ollie_client import OnTick, SSEEvent

# Substrings that must NEVER appear in any analytics event. Each one is a
# realistic free-text payload a user might pass, chosen to be globally unique
# inside the test process so even a partial leak would trigger.
FORBIDDEN = [
    "Why-did-trace-7c4a-fail-on-prod-PRIVATE-QUERY",
    "https://internal.example.com/super-secret-page",
    "RegressionVsYesterday-INTERNAL-COMMENT-TOKEN",
    "BadOutputReason-SHOULD-NEVER-APPEAR-IN-TELEMETRY",
    "ProjectNameMustNotLeak-XYZ-001",
    "AttachedTraceURI-opik://traces/UNIQUE-LEAK-CANARY",
    # read.id free-text canary — must never appear in analytics event properties
    "free-text-read-id-UNIQUE-CANARY-8f3a2b1c",
    # list.name filter canary — must never appear in analytics event properties
    "free-text-list-name-UNIQUE-CANARY-9d4e5f6a",
    # write.data canary (PII payload inside the structured object)
    "write-data-payload-UNIQUE-CANARY-1a2b3c4d",
    # PR1 fingerprint canaries — install env values that MUST never appear
    # in any analytics event payload.
    "FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
    "FORBIDDEN-CANARY-socket-hostname-9d3e5f4a",
    "FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
    "FORBIDDEN-CANARY-home-path-5e6f7a8b",
    # OAuth bearer token canary — auth_rejected derives its reason from the
    # header SHAPE only; the raw token must never reach the event.
    "opik_at_FORBIDDEN-CANARY-oauth-token-2f8e1d3c",
]


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def track_event(self, et: str, props: dict[str, str]) -> None:
        self.events.append((et, props))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: r)
    monkeypatch.setattr("opik_mcp.ask_ollie._analytics", lambda: r)
    monkeypatch.setattr("opik_mcp.audit._analytics_for_audit", lambda: r)
    return r


def _assert_no_leak(events: list[tuple[str, dict[str, str]]]) -> None:
    payload = json.dumps(events)
    for forbidden in FORBIDDEN:
        assert forbidden not in payload, (
            f"PRIVACY BREACH: {forbidden!r} leaked into analytics payload"
        )


def _tool_called(events: list[tuple[str, dict[str, str]]]) -> dict[str, str]:
    """Return the single `tool_called` event's properties, failing loudly if absent.

    The old version of these tests passed when the recorder was empty — that's
    a false negative, not a privacy guarantee. Asserting at least one event
    fired forces the test to fail if the wrapper is bypassed or the tool isn't
    decorated.
    """
    matches = [props for et, props in events if et == "opik_mcp_tool_called"]
    assert matches, (
        f"Expected exactly one opik_mcp_tool_called event, got events={events!r}. "
        "If this fires, the tool wrapper didn't run — the privacy assertion is "
        "trivially passing on an empty payload."
    )
    return matches[0]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeComet:
    async def discover_pod(self, workspace: str) -> PodDiscovery:
        return PodDiscovery(compute_url="http://c", ppauth="p")


async def _message_end_iter() -> AsyncIterator[SSEEvent]:
    yield SSEEvent(event="message_end", data={"payload": {}})


class _FakeOllie:
    async def wait_ready(
        self, compute_url: str, ppauth: str, *, on_tick: OnTick | None = None
    ) -> None:
        pass

    async def create_session(
        self, compute_url: str, ppauth: str, workspace: str, body: dict[str, Any]
    ) -> str:
        return "sess-1"

    def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        return _message_end_iter()

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
        pass


# --- ask_ollie ------------------------------------------------------------ #


@pytest.mark.anyio
async def test_ask_ollie_strips_all_user_text(recorder: _Recorder) -> None:
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.config import Settings

    await run_ask_ollie(
        query=FORBIDDEN[0],
        page_context=FORBIDDEN[1],
        project_name=FORBIDDEN[4],
        attach_resources=[FORBIDDEN[5]],
        settings=Settings(opik_api_key="k", comet_workspace="ws-1"),
        comet_client=_FakeComet(),
        ollie_client=_FakeOllie(),
    )
    _assert_no_leak(recorder.events)
    # An ask_ollie_completed event MUST have fired even when the call ran
    # against the fake stack — otherwise the "no leak" assertion is vacuous.
    assert any(et == "opik_mcp_ask_ollie_completed" for et, _ in recorder.events)


# --- write: drive server.write so _write_props executes ------------------ #


@pytest.mark.anyio
async def test_write_props_emits_only_low_cardinality_signals(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.write with a PII data payload MUST emit only bucketed flags."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_write",
        lambda **_kw: _noop_coroutine_result({"ok": True, "operation": "comment.create"}),
    )

    await server.write(
        operation="comment.create",
        data={
            "target": "trace",
            "target_id": "00000000-0000-0000-0000-000000000001",
            "text": FORBIDDEN[2] + " " + FORBIDDEN[3],
        },
    )
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["operation"] == "comment.create"
    assert props["is_batch"] == "false"
    assert props["dry_run"] == "false"
    assert props["had_idempotency_key"] == "false"


@pytest.mark.anyio
async def test_write_props_emits_batch_size_bucket(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.write with an array data payload MUST emit is_batch + bucketed size."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_write",
        lambda **_kw: _noop_coroutine_result({"ok": True, "operation": "trace.create"}),
    )

    payload = [{"name": f"trace-{i}", "input": FORBIDDEN[3]} for i in range(50)]
    await server.write(operation="trace.create", data=payload)
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["operation"] == "trace.create"
    assert props["is_batch"] == "true"
    # bucket_count maps 50 into a discrete bucket; the raw "50" must not appear.
    assert "50" not in json.dumps(props)


@pytest.mark.anyio
async def test_write_props_emits_dry_run_flag(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_write",
        lambda **_kw: _noop_coroutine_result({"dry_run": True}),
    )

    await server.write(
        operation="score.create",
        data={
            "target": "trace",
            "target_id": "00000000-0000-0000-0000-000000000001",
            "name": "helpfulness",
            "value": 0.5,
            "reason": FORBIDDEN[3],
        },
        dry_run=True,
    )
    props = _tool_called(recorder.events)
    assert props["dry_run"] == "true"
    assert props["operation"] == "score.create"


@pytest.mark.anyio
async def test_schema_props_emits_only_operation(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.schema MUST only emit `operation` — no input payload to leak."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_schema",
        lambda operation: {"operation": operation, "schema": {}},
    )

    await server.schema(operation="trace.create")
    props = _tool_called(recorder.events)
    assert props["operation"] == "trace.create"


# --- read: drive server.read so _read_props executes ---------------------- #


@pytest.mark.parametrize(
    ("raw_id", "expected_kind"),
    [
        # URI shape → "uri"; the raw URI is PII (carries a unique canary tail).
        ("opik://traces/" + FORBIDDEN[6], "uri"),
        # Valid UUID → "uuid".
        ("00000000-0000-0000-0000-deadbeefcafe", "uuid"),
        # Free-text name → "name"; the raw value is the canary itself.
        (FORBIDDEN[6], "name"),
    ],
)
@pytest.mark.anyio
async def test_read_props_buckets_id_kind_without_leaking(
    raw_id: str,
    expected_kind: str,
    recorder: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """server.read MUST emit only `entity_type` + `id_kind` — never the raw id."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_read",
        lambda **_kw: _noop_coroutine("[read: project / x / SKELETON / 1 / 1]\n{}"),
    )

    await server.read(entity_type="project", id=raw_id)
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["entity_type"] == "project"
    assert props["id_kind"] == expected_kind
    # The raw id MUST NOT appear verbatim anywhere in the event.
    assert raw_id not in json.dumps(props), f"raw id {raw_id!r} leaked into props {props!r}"


# --- list: drive server.list_entities so _list_props executes ------------- #


@pytest.mark.anyio
async def test_list_props_emits_had_name_filter_without_leaking(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.list_entities with a PII `name` filter MUST only emit a boolean."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_list",
        lambda **_kw: _noop_coroutine("[list: project / page 1 / 0 items]\n"),
    )

    await server.list_entities(entity_type="project", name=FORBIDDEN[7], page=3, size=50)
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["entity_type"] == "project"
    assert props["had_name_filter"] == "true"
    assert props["page"] == "3"
    assert props["size"] == "50"


@pytest.mark.anyio
async def test_list_props_emits_had_name_filter_false_when_absent(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative branch — no `name` filter must yield `had_name_filter=false`."""
    from opik_mcp import server

    monkeypatch.setattr(
        "opik_mcp.server.run_list",
        lambda **_kw: _noop_coroutine("[list: project / page 1 / 0 items]\n"),
    )

    await server.list_entities(entity_type="project")
    props = _tool_called(recorder.events)
    assert props["had_name_filter"] == "false"


# --- failure paths: error_kind / exception_type / http_status MUST be bucketed -- #
#
# When a tool raises, the wrapper emits ``error_kind`` + ``exception_type`` (+
# optional ``http_status``). The privacy contract says NONE of those props may
# embed the exception message — only the class-keyed bucket, the class name
# itself, and a numeric status. These tests stuff canary substrings into the
# exception message and assert they never surface in the analytics payload.


class _CanaryAuthError(Exception):
    """Stand-in for an Opik 401 carrying a PII-shaped message body."""


@pytest.mark.anyio
async def test_tool_called_failure_strips_exception_message(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that raises with a PII-shaped message MUST NOT surface that
    text in the analytics event — only the class-keyed bucket and class name.
    Drives ``server.read`` so ``_read_props`` + the wrapper's error-emit arm
    both execute on the real tool surface."""
    from opik_mcp import server
    from opik_mcp.opik_client import OpikAuthError

    canary = "raw-error-message-UNIQUE-CANARY-7e1f2a3b"

    async def _raise(**_kw: Any) -> str:
        raise OpikAuthError(canary)

    monkeypatch.setattr("opik_mcp.server.run_read", _raise)

    with pytest.raises(OpikAuthError):
        await server.read(entity_type="project", id="00000000-0000-0000-0000-000000000001")

    # Canary must not appear anywhere in the recorded payload.
    payload = json.dumps(recorder.events)
    assert canary not in payload, (
        f"PRIVACY BREACH: raw exception message {canary!r} leaked into analytics"
    )
    props = _tool_called(recorder.events)
    assert props["success"] == "false"
    assert props["error_kind"] == "auth"
    assert props["exception_type"] == "OpikAuthError"
    assert props["http_status"] == "401"
    # Defense in depth: even an http_status of "401" must not be confused
    # with a PII substring leak — the props_fn must not have populated
    # anything that contains "raw-error-message".
    for v in props.values():
        assert "UNIQUE-CANARY" not in v


@pytest.mark.anyio
async def test_tool_called_failure_unknown_class_uses_class_name_only(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A custom Exception class falls through to ``error_kind=unknown``. The
    only granular signal is ``exception_type`` (the class name), which is
    fixed by the class declaration — never an attacker-controlled string."""
    from opik_mcp import server

    canary = "unknown-class-message-UNIQUE-CANARY-c4d5e6f7"

    async def _raise(**_kw: Any) -> str:
        raise _CanaryAuthError(canary)

    monkeypatch.setattr("opik_mcp.server.run_read", _raise)

    with pytest.raises(_CanaryAuthError):
        await server.read(entity_type="project", id="00000000-0000-0000-0000-000000000001")

    payload = json.dumps(recorder.events)
    assert canary not in payload
    props = _tool_called(recorder.events)
    assert props["error_kind"] == "unknown"
    assert props["exception_type"] == "_CanaryAuthError"
    assert "http_status" not in props


@pytest.mark.anyio
async def test_tool_called_cause_type_is_class_only(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production raise site: ``read_tool.py`` wraps every upstream failure as
    ``raise ToolError(...) from <typed exc>``. The new ``cause_type`` prop
    must carry the leaf class name and NOTHING from either the wrapper's
    user-facing message OR the cause's exception message — both are tested
    here with distinct canaries so a regression in either path fails loudly.

    This is the privacy guard for the unwrap-to-real-cause change. Without
    this test, a future refactor that started reading ``str(real)`` /
    ``real.args`` could silently exfiltrate exception messages into BI."""
    from mcp.server.fastmcp.exceptions import ToolError

    from opik_mcp import server
    from opik_mcp.opik_client import OpikAuthError

    wrapper_canary = "tool-error-wrapper-msg-UNIQUE-CANARY-1f2e3d4c"
    cause_canary = "opik-auth-cause-msg-UNIQUE-CANARY-5b6a7980"

    async def _raise(**_kw: Any) -> str:
        try:
            raise OpikAuthError(cause_canary)
        except OpikAuthError as e:
            raise ToolError(wrapper_canary) from e

    monkeypatch.setattr("opik_mcp.server.run_read", _raise)

    with pytest.raises(ToolError):
        await server.read(entity_type="project", id="00000000-0000-0000-0000-000000000001")

    payload = json.dumps(recorder.events)
    assert wrapper_canary not in payload, (
        f"PRIVACY BREACH: ToolError message {wrapper_canary!r} leaked into analytics"
    )
    assert cause_canary not in payload, (
        f"PRIVACY BREACH: cause message {cause_canary!r} leaked into analytics"
    )
    props = _tool_called(recorder.events)
    # exception_type captures the wrapper (where in our code the failure
    # surfaced); cause_type captures the leaf (what actually broke).
    assert props["exception_type"] == "ToolError"
    assert props["cause_type"] == "OpikAuthError"
    # Unwrap routing kicks in — auth bucket comes from the cause, not the
    # opaque ToolError wrapper.
    assert props["error_kind"] == "auth"
    assert props["http_status"] == "401"


class _CanaryOllieClient:
    """``_OllieClientProto`` fake that fires an SSE ``error`` frame whose
    ``message`` field is a unique canary. Used to verify the pod's error
    message never reaches the ``ask_ollie_completed`` analytics event."""

    canary_message: str = "ollie-stream-error-UNIQUE-CANARY-9a8b7c6d"
    # Shape-valid identifier: lowercase alnum + ``_-``, ≤ 32 chars. Matches
    # ``_UPSTREAM_CODE_PATTERN`` in ask_ollie.py — passes through to BI
    # unchanged so the test can assert the wire-up actually surfaces it.
    canary_code: str = "rate_limited_canary"

    async def wait_ready(
        self, compute_url: str, ppauth: str, *, on_tick: OnTick | None = None
    ) -> None:
        pass

    async def create_session(
        self, compute_url: str, ppauth: str, workspace: str, body: dict[str, Any]
    ) -> str:
        return "sess-canary"

    def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        return self._error_iter()

    async def _error_iter(self) -> AsyncIterator[SSEEvent]:
        yield SSEEvent(
            event="error",
            data={"payload": {"message": self.canary_message, "code": self.canary_code}},
        )

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
        pass


@pytest.mark.anyio
async def test_ask_ollie_failure_strips_stream_error_message(recorder: _Recorder) -> None:
    """A pod-side SSE ``error`` frame carries a free-text ``message`` plus
    an optional structured ``code``. The completed event MUST surface only
    the coarse bucket + class name + (length-capped) code — never the
    message body."""
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.config import Settings
    from opik_mcp.ollie_client import OllieStreamError

    fake = _CanaryOllieClient()
    with pytest.raises(OllieStreamError):
        await run_ask_ollie(
            query="placeholder-query",
            settings=Settings(opik_api_key="k", comet_workspace="ws-1"),
            comet_client=_FakeComet(),
            ollie_client=fake,
        )

    payload = json.dumps(recorder.events)
    assert fake.canary_message not in payload, (
        f"PRIVACY BREACH: pod error message {fake.canary_message!r} leaked into analytics"
    )

    completed = [props for et, props in recorder.events if et == "opik_mcp_ask_ollie_completed"]
    assert completed, "ask_ollie must emit a completed event on the error path"
    props = completed[0]
    assert props["completion_state"] == "error"
    # Pod error frame surfaces as ``PodErrorEventError`` (subclass of
    # ``OllieStreamError`` raised at the SSE ``error`` event site) — its
    # ClassVar pins ``error_kind`` to ``stream_error_frame``.
    assert props["error_kind"] == "stream_error_frame"
    assert props["exception_type"] == "PodErrorEventError"
    # ``code`` IS allowlisted into analytics — it's the one field pod authors
    # are expected to keep enum-shaped. Shape-valid codes (alnum + ``_-``,
    # ≤ 32 chars) pass through unchanged; anything else collapses to
    # ``"other"`` so a misbehaving pod can't smuggle text past the cap. The
    # canary here is shape-valid by construction; the long/uppercase rejection
    # path has its own test below.
    assert props["upstream_error_code"] == fake.canary_code


class _LongCodeOllieClient(_CanaryOllieClient):
    """Same canary stream as ``_CanaryOllieClient`` but with a code field
    that violates the identifier shape (uppercase, > 32 chars, dashes after
    uppercase). Used to verify the shape-check bucket fires."""

    # 100 chars + uppercase + sentence punctuation — comfortably outside the
    # ``^[a-z0-9][a-z0-9_-]{0,31}$`` shape. If any character of the canary
    # tail appears in the recorded props, the shape-check regressed.
    canary_code: str = "x" * 64 + "TAIL-MUST-BE-CHOPPED-UNIQUE-CANARY-d4e5f6a7"


@pytest.mark.anyio
async def test_ask_ollie_failure_buckets_misshaped_upstream_error_code_to_other(
    recorder: _Recorder,
) -> None:
    """Pod-controlled ``code`` that doesn't match the stable-identifier shape
    (alnum + ``_-``, ≤ 32 chars) MUST collapse to ``"other"`` on emit — the
    earlier 64-char truncation was insufficient because uppercase, spaces,
    and punctuation could still slip ~64 chars of pod-controlled text past
    the message-stripping privacy contract. The shape check is the load-
    bearing fix; this test pins it on the end-to-end emit path."""
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.config import Settings
    from opik_mcp.ollie_client import OllieStreamError

    fake = _LongCodeOllieClient()
    assert len(fake.canary_code) > 32  # guard the test itself: must violate cap

    with pytest.raises(OllieStreamError):
        await run_ask_ollie(
            query="placeholder-query",
            settings=Settings(opik_api_key="k", comet_workspace="ws-1"),
            comet_client=_FakeComet(),
            ollie_client=fake,
        )

    completed = [props for et, props in recorder.events if et == "opik_mcp_ask_ollie_completed"]
    assert completed
    code = completed[0]["upstream_error_code"]
    assert code == "other", f"expected misshaped code bucketed to 'other', got: {code!r}"
    # Belt-and-braces: NO substring of the canary may appear anywhere in
    # the recorded payload — the bucket helper is the only path that
    # touches the field, but if a future change adds a second emit site,
    # this catches the leak.
    payload = json.dumps(recorder.events)
    assert "TAIL-MUST-BE-CHOPPED" not in payload


@pytest.mark.anyio
async def test_ask_ollie_failure_typed_exception_bucketed_correctly(
    recorder: _Recorder,
) -> None:
    """A typed pod-discovery failure (``CometPermissionError``) must surface
    as ``error_kind=permission`` even when the exception message is PII."""
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.comet_client import CometPermissionError
    from opik_mcp.config import Settings

    canary = "comet-permission-message-UNIQUE-CANARY-5e6f7a8b"

    class _PermissionComet:
        async def discover_pod(self, workspace: str) -> PodDiscovery:
            raise CometPermissionError(canary)

    with pytest.raises(CometPermissionError):
        await run_ask_ollie(
            query="placeholder-query",
            settings=Settings(opik_api_key="k", comet_workspace="ws-1"),
            comet_client=_PermissionComet(),
            ollie_client=_FakeOllie(),
        )

    payload = json.dumps(recorder.events)
    assert canary not in payload
    completed = [props for et, props in recorder.events if et == "opik_mcp_ask_ollie_completed"]
    assert completed
    props = completed[0]
    assert props["completion_state"] == "error"
    # CometPermissionError's ClassVar pins ``error_kind`` to
    # ``comet_permission`` (ask_ollie-specific bucket) — distinct from a
    # generic write/read 403 which buckets as ``permission``.
    assert props["error_kind"] == "comet_permission"
    assert props["exception_type"] == "CometPermissionError"
    # No SSE error frame on this path → no upstream_error_code.
    assert "upstream_error_code" not in props


# --- per-call session context: bucketed host + env, no raw strings -------- #


def test_call_context_props_buckets_host_without_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-call session-context block (stamped on tool_called /
    ask_ollie_completed) must bucket the MCP host and env cohort — never echo
    a raw ``clientInfo.name``, version, protocolVersion, or HOME path."""
    from types import SimpleNamespace

    from opik_mcp.analytics.mcp_client_info import (
        _reset_call_context_cache_for_tests,
        call_context_props,
    )

    _reset_call_context_cache_for_tests()
    monkeypatch.setenv("HOME", "/tmp/FORBIDDEN-CANARY-home-path-5e6f7a8b")

    client_info = SimpleNamespace(
        name="FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
        version="FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
    )
    params = SimpleNamespace(
        clientInfo=client_info,
        protocolVersion="FORBIDDEN-CANARY-home-path-5e6f7a8b",
        capabilities=None,
    )
    session = SimpleNamespace(client_params=params)

    props = call_context_props(session)
    # Unknown host name → "other"; nothing host-controlled survives.
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"
    payload = json.dumps(props)
    for canary in FORBIDDEN:
        assert canary not in payload, f"PRIVACY BREACH: {canary!r} leaked into call context"
    _reset_call_context_cache_for_tests()


@pytest.mark.anyio
async def test_tool_called_session_context_buckets_canary_host(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real ``instrument_tool`` emit path with a canary-laden
    ``clientInfo`` and assert the recorded tool_called carries only bucketed
    host/env signals — the raw host strings must never reach the event."""
    from types import SimpleNamespace

    from opik_mcp.analytics.mcp_client_info import _reset_call_context_cache_for_tests
    from opik_mcp.analytics.wrappers import _reset_seen_sessions_for_tests, instrument_tool

    _reset_call_context_cache_for_tests()
    _reset_seen_sessions_for_tests()

    client_info = SimpleNamespace(
        name="FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
        version="FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
    )
    params = SimpleNamespace(
        clientInfo=client_info,
        protocolVersion="FORBIDDEN-CANARY-home-path-5e6f7a8b",
        capabilities=None,
    )
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=params))

    @instrument_tool("read")
    async def fn(*, ctx: Any) -> str:
        return "ok"

    await fn(ctx=ctx)

    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["mcp_host"] == "other"
    assert props["host_llm_family"] == "unknown"
    assert props["is_ci"] in {"true", "false"}
    assert props["install_id_freshly_generated"] in {"true", "false"}
    _reset_call_context_cache_for_tests()


# --- helpers -------------------------------------------------------------- #


async def _noop_coroutine(result: str) -> str:
    return result


async def _noop_coroutine_result(result: Any) -> Any:
    return result


# --- cross-event privacy sweep ------------------------------------------ #


@pytest.mark.parametrize(
    "event_name",
    [
        "opik_mcp_server_started",
        "opik_mcp_session_initialized",
        "opik_mcp_tools_listed",
        "opik_mcp_server_shutdown",
        "opik_mcp_auth_rejected",
    ],
)
def test_new_events_carry_no_forbidden_substring(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, event_name: str
) -> None:
    """Sweep PR1 event vocabulary against the FORBIDDEN canary list.

    Drives each event's emit path with monkeypatched leak sources (HOME,
    getpass.getuser, socket.gethostname, os.uname().nodename) and asserts
    none of the canaries surface in the recorded property dicts.
    """
    import getpass
    import os
    import socket

    monkeypatch.setenv("HOME", "/tmp/FORBIDDEN-CANARY-home-path-5e6f7a8b")
    monkeypatch.setattr(
        getpass,
        "getuser",
        lambda: "FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
    )
    monkeypatch.setattr(
        socket,
        "gethostname",
        lambda: "FORBIDDEN-CANARY-socket-hostname-9d3e5f4a",
    )
    if hasattr(os, "uname"):
        fake = os.uname_result(
            ("Linux", "FORBIDDEN-CANARY-uname-nodename-1b2c3d4e", "5.0", "#1", "x86_64"),
        )
        monkeypatch.setattr(os, "uname", lambda: fake)

    if event_name == "opik_mcp_server_started":
        from opik_mcp.analytics import EVENT_SERVER_STARTED
        from opik_mcp.analytics.environment import collect_environment_fingerprint
        from opik_mcp.analytics.identity import install_id_was_freshly_generated

        # The other tests in this module patch `analytics.wrappers._client`,
        # but server_started emits via the top-level `track_event` -> singleton
        # path, so we patch `get_analytics` to redirect to the recorder.
        monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)
        from opik_mcp.analytics import track_event

        track_event(
            EVENT_SERVER_STARTED,
            {
                "transport": "stdio",
                "install_id_freshly_generated": str(install_id_was_freshly_generated()).lower(),
                **collect_environment_fingerprint(),
            },
        )
    elif event_name == "opik_mcp_session_initialized":
        from types import SimpleNamespace

        from opik_mcp.analytics.wrappers import (
            _maybe_emit_session_initialized,
            _reset_seen_sessions_for_tests,
        )

        _reset_seen_sessions_for_tests()
        # Push canaries through EVERY host-controlled string field: name,
        # clientInfo.version, and protocolVersion. A regression that drops
        # the bucketing on version fields would surface here.
        client_info = SimpleNamespace(
            name="FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
            version="FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
        )
        params = SimpleNamespace(
            clientInfo=client_info,
            protocolVersion="FORBIDDEN-CANARY-home-path-5e6f7a8b",
            capabilities=None,
        )
        ctx = SimpleNamespace(session=SimpleNamespace(client_params=params))
        _maybe_emit_session_initialized({"ctx": ctx})

    elif event_name == "opik_mcp_tools_listed":
        import anyio
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ListToolsRequest

        from opik_mcp.analytics.wrappers import (
            _reset_seen_tools_listed_for_tests,
            install_tools_listed_emitter,
        )

        _reset_seen_tools_listed_for_tests()
        mcp = FastMCP("privacy-probe")

        @mcp.tool()
        def hi() -> str:
            return "x"

        monkeypatch.setattr("opik_mcp.analytics.wrappers._client", lambda: recorder)
        install_tools_listed_emitter(mcp)
        handler = mcp._mcp_server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        anyio.run(handler, req)

    elif event_name == "opik_mcp_server_shutdown":
        from opik_mcp.analytics import EVENT_SERVER_SHUTDOWN, track_event, transport_probe
        from opik_mcp.analytics.events import bucket_seconds

        monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)
        track_event(
            EVENT_SERVER_SHUTDOWN,
            {
                "reason": "clean_exit",
                "lifespan_seconds_bucket": bucket_seconds(42.0),
                "first_rpc_received": str(transport_probe.first_rpc_received()).lower(),
                "session_reached": str(transport_probe.session_reached()).lower(),
            },
        )

    elif event_name == "opik_mcp_auth_rejected":
        from opik_mcp import server
        from opik_mcp.config import Settings

        monkeypatch.setattr(
            "opik_mcp.server.track_event", lambda et, p: recorder.track_event(et, p)
        )
        mw = server.AuthRejectionMiddleware(
            None,  # type: ignore[arg-type]  # app unused by _emit_rejection
            settings=Settings(opik_mcp_analytics_enabled=False, _env_file=None),  # type: ignore[call-arg]
        )
        # Drive the emit path directly with a canary-laden bearer token; the
        # event must carry only the bucketed reason/auth_mode, never the token.
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"authorization", b"Bearer opik_at_FORBIDDEN-CANARY-oauth-token-2f8e1d3c"),
            ],
        }
        mw._emit_rejection(scope, 401)

    # An empty recorder would let the canary check pass vacuously, hiding the
    # case where the emit path silently no-ops (e.g. a future regression that
    # short-circuits before track_event). Pin the emit shape before scanning.
    assert recorder.events, (
        f"no event recorded for {event_name} — privacy sweep would pass vacuously"
    )
    assert recorder.events[0][0] == event_name

    payload = json.dumps(recorder.events)
    for canary in FORBIDDEN:
        assert canary not in payload, (
            f"PRIVACY BREACH on {event_name}: {canary!r} leaked into payload"
        )


@pytest.mark.anyio
async def test_sentry_capture_path_carries_no_forbidden_substring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sweep the Sentry capture path against the FORBIDDEN canary list.

    BI and Sentry consume the SAME ``collect_session_props`` extractor, but
    they hand the payload to different sinks; a refactor that only updates
    the BI sink would silently drift Sentry's cardinality contract. This
    test exercises the actual ``instrument_tool`` → ``_capture_to_sentry``
    path with canaries stuffed into every host-controlled field, then
    asserts none reach ``error_tracking.capture_exception``.
    """
    from types import SimpleNamespace

    from opik_mcp.analytics.wrappers import instrument_tool
    from opik_mcp.opik_client import OpikServerError

    captured_tags: dict[str, str] = {}
    captured_extras: dict[str, Any] = {}

    def _fake_capture(
        exc: BaseException,
        *,
        tags: dict[str, str] | None = None,
        extras: dict[str, Any] | None = None,
        transaction: str | None = None,
        fingerprint: list[str] | None = None,
    ) -> None:
        captured_tags.update(tags or {})
        captured_extras.update(extras or {})

    monkeypatch.setattr("opik_mcp.error_tracking.capture_exception", _fake_capture)

    client_info = SimpleNamespace(
        name="FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
        version="FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
    )
    params = SimpleNamespace(
        clientInfo=client_info,
        protocolVersion="FORBIDDEN-CANARY-home-path-5e6f7a8b",
        capabilities=None,
    )
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=params))

    @instrument_tool("read")
    async def fn(*, ctx: Any) -> str:
        raise OpikServerError("boom")

    with pytest.raises(OpikServerError):
        await fn(ctx=ctx)

    assert captured_tags, "Sentry capture wasn't called — sweep would pass vacuously"

    payload = json.dumps({"tags": captured_tags, "extras": captured_extras})
    for canary in FORBIDDEN:
        assert canary not in payload, (
            f"PRIVACY BREACH on sentry capture path: {canary!r} leaked into tags/extras"
        )
