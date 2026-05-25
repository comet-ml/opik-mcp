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


class _CanaryOllieClient:
    """``_OllieClientProto`` fake that fires an SSE ``error`` frame whose
    ``message`` field is a unique canary. Used to verify the pod's error
    message never reaches the ``ask_ollie_completed`` analytics event."""

    canary_message: str = "ollie-stream-error-UNIQUE-CANARY-9a8b7c6d"
    canary_code: str = "rate_limited_canary_UNIQUE-2f3e4d5c"

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
    assert props["error_kind"] == "unknown"
    assert props["exception_type"] == "OllieStreamError"
    # ``code`` IS allowlisted into analytics (length-capped) — it's the one
    # field pod authors are expected to keep enum-shaped. The test passes
    # an obviously-not-PII canary to confirm the wire-up; downstream BI is
    # the place to enforce the actual enum.
    assert props["upstream_error_code"] == fake.canary_code[:64]


class _LongCodeOllieClient(_CanaryOllieClient):
    """Same canary stream as ``_CanaryOllieClient`` but with a code field
    longer than the 64-char cap. Used to verify the length-cap actually
    fires (the base class's canary is only 36 chars long, which would
    pass the assertion even if the slicing were removed)."""

    # 100 chars — comfortably over the 64-char cap. The trailing canary
    # tail (positions 64..) must be sliced off; if it appears in props,
    # the cap regressed.
    canary_code: str = "x" * 64 + "TAIL-MUST-BE-CHOPPED-UNIQUE-CANARY-d4e5f6a7"


@pytest.mark.anyio
async def test_ask_ollie_failure_caps_upstream_error_code_at_64_chars(
    recorder: _Recorder,
) -> None:
    """Production cap at ``ask_ollie.py``: ``upstream_error_code`` MUST be
    truncated to 64 chars before emit. Pod-controlled field — without the
    cap, a misbehaving pod could stamp arbitrary text into ``code`` and
    smuggle it past the message-stripping privacy contract."""
    from opik_mcp.ask_ollie import run_ask_ollie
    from opik_mcp.config import Settings
    from opik_mcp.ollie_client import OllieStreamError

    fake = _LongCodeOllieClient()
    assert len(fake.canary_code) > 64  # guard the test itself

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
    assert len(code) == 64, f"expected 64-char cap, got len={len(code)}: {code!r}"
    assert code == fake.canary_code[:64]
    # The truncated tail must not appear anywhere in the recorded payload —
    # if it does, the cap fired but something else (a duplicate field,
    # a log line, etc.) is still leaking the raw value.
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
    assert props["error_kind"] == "permission"
    assert props["exception_type"] == "CometPermissionError"
    # No SSE error frame on this path → no upstream_error_code.
    assert "upstream_error_code" not in props


# --- helpers -------------------------------------------------------------- #


async def _noop_coroutine(result: str) -> str:
    return result


async def _noop_coroutine_result(result: Any) -> Any:
    return result
