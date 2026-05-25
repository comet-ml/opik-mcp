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
        getpass, "getuser",
        lambda: "FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
    )
    monkeypatch.setattr(
        socket, "gethostname",
        lambda: "FORBIDDEN-CANARY-socket-hostname-9d3e5f4a",
    )
    if hasattr(os, "uname"):
        fake = os.uname_result(
            ("Linux", "FORBIDDEN-CANARY-uname-nodename-1b2c3d4e",
             "5.0", "#1", "x86_64"),
        )
        monkeypatch.setattr(os, "uname", lambda: fake)

    if event_name == "opik_mcp_server_started":
        from opik_mcp.analytics import EVENT_SERVER_STARTED
        from opik_mcp.analytics.environment import collect_environment_fingerprint
        from opik_mcp.analytics.identity import install_id_was_freshly_generated
        # The other tests in this module patch `analytics.wrappers._client`,
        # but server_started emits via the top-level `track_event` -> singleton
        # path, so we patch `get_analytics` to redirect to the recorder.
        monkeypatch.setattr(
            "opik_mcp.analytics.get_analytics", lambda: recorder
        )
        from opik_mcp.analytics import track_event
        track_event(EVENT_SERVER_STARTED, {
            "transport": "stdio",
            "install_id_freshly_generated": str(install_id_was_freshly_generated()).lower(),
            **collect_environment_fingerprint(),
        })
    elif event_name == "opik_mcp_session_initialized":
        from types import SimpleNamespace

        from opik_mcp.analytics.wrappers import (
            _maybe_emit_session_initialized,
            _reset_seen_sessions_for_tests,
        )
        _reset_seen_sessions_for_tests()
        client_info = SimpleNamespace(
            name="FORBIDDEN-CANARY-getpass-username-7c4a2b1c",
            version="0.1",
        )
        params = SimpleNamespace(
            clientInfo=client_info, protocolVersion="2025-06-01",
            capabilities=None,
        )
        ctx = SimpleNamespace(session=SimpleNamespace(client_params=params))
        _maybe_emit_session_initialized({"ctx": ctx})

    elif event_name == "opik_mcp_tools_listed":
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

        monkeypatch.setattr(
            "opik_mcp.analytics.wrappers._client", lambda: recorder
        )
        install_tools_listed_emitter(mcp)
        handler = mcp._mcp_server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        import anyio
        anyio.run(handler, req)

    elif event_name == "opik_mcp_server_shutdown":
        from opik_mcp.analytics import EVENT_SERVER_SHUTDOWN, track_event
        from opik_mcp.analytics.events import bucket_seconds
        from opik_mcp.analytics import transport_probe
        monkeypatch.setattr("opik_mcp.analytics.get_analytics", lambda: recorder)
        track_event(EVENT_SERVER_SHUTDOWN, {
            "reason": "clean_exit",
            "lifespan_seconds_bucket": bucket_seconds(42.0),
            "first_rpc_received": str(transport_probe.first_rpc_received()).lower(),
            "session_reached": str(transport_probe.session_reached()).lower(),
        })

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
