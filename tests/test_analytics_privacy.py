"""Enforce §4.5 — no user prose ever appears in an analytics event.

Drives the *real* MCP tool entry points (server.read, server.list_entities,
server.score, server.comment, run_ask_ollie) so the wrapper's `props_fn` is
exercised on every call. Each test:

1. Calls the actual tool with PII-shaped inputs.
2. Asserts the wrapper emitted `tool_called` (an empty recorder is a bug, not
   a privacy guarantee — that was the old failure mode).
3. Asserts every FORBIDDEN substring is absent from the serialized event.
4. Asserts the bucketed signal that REPLACED the raw input is present and
   correct (`id_kind`, `had_name_filter`, `text_length_bucket`, etc.).

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
from opik_mcp.opik_client import FeedbackScore
from opik_mcp.score_comment import Target

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
    # score.category_name canary
    "category-name-UNIQUE-CANARY-1a2b3c4d",
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


class _ScoreStubClient:
    async def add_trace_feedback_score(self, trace_id: str, score: FeedbackScore) -> None:
        pass

    async def add_span_feedback_score(self, span_id: str, score: FeedbackScore) -> None:
        pass

    async def add_thread_feedback_score(
        self,
        thread_id: str,
        score: FeedbackScore,
        *,
        project_name: str | None = None,
    ) -> None:
        pass

    async def add_trace_comment(self, trace_id: str, text: str) -> None:
        pass

    async def add_span_comment(self, span_id: str, text: str) -> None:
        pass

    async def add_thread_comment(self, thread_id: str, text: str) -> None:
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


# --- score / comment: drive server.* so props_fn actually executes -------- #


@pytest.mark.anyio
async def test_score_props_buckets_reason_and_category_without_leaking(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.score with PII reason+category MUST emit only bucketed flags."""
    from opik_mcp import server
    from opik_mcp.score_comment import ScoreResult

    target = Target(type="trace", id="00000000-0000-0000-0000-000000000001")
    fake_result = ScoreResult(target=target, name="helpfulness", value=0.5)
    monkeypatch.setattr(
        "opik_mcp.server.run_score",
        lambda **_kw: _noop_coroutine_result(fake_result),
    )

    await server.score(
        target=target,
        name="helpfulness",
        value=0.5,
        reason=FORBIDDEN[3],
        category_name=FORBIDDEN[8],
    )
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    # _score_props turns the raw fields into low-cardinality booleans.
    assert props["target_type"] == "trace"
    assert props["score_name_bucket"] == "helpfulness"
    assert props["has_reason"] == "true"
    assert props["has_category"] == "true"


@pytest.mark.anyio
async def test_score_props_emits_has_reason_false_when_absent(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative branch — `reason=None` must yield `has_reason=false`."""
    from opik_mcp import server
    from opik_mcp.score_comment import ScoreResult

    target = Target(type="span", id="00000000-0000-0000-0000-000000000002")
    fake_result = ScoreResult(target=target, name="tone", value=1.0)
    monkeypatch.setattr(
        "opik_mcp.server.run_score",
        lambda **_kw: _noop_coroutine_result(fake_result),
    )

    await server.score(target=target, name="tone", value=1.0)
    props = _tool_called(recorder.events)
    assert props["has_reason"] == "false"
    assert props["has_category"] == "false"
    assert props["target_type"] == "span"


@pytest.mark.anyio
async def test_score_non_canonical_name_bucketed(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-canonical score names must be bucketed as 'other' AND must not leak."""
    _FORBIDDEN_SCORE_NAME = "FORBIDDEN_X_custom_score_name_UNIQUE_9a8b7c"
    from opik_mcp import server
    from opik_mcp.score_comment import ScoreResult

    target = Target(type="trace", id="00000000-0000-0000-0000-000000000001")
    fake_result = ScoreResult(target=target, name=_FORBIDDEN_SCORE_NAME, value=0.5)
    monkeypatch.setattr(
        "opik_mcp.server.run_score",
        lambda **_kw: _noop_coroutine_result(fake_result),
    )

    await server.score(target=target, name=_FORBIDDEN_SCORE_NAME, value=0.5)
    payload = json.dumps(recorder.events)
    assert _FORBIDDEN_SCORE_NAME not in payload
    props = _tool_called(recorder.events)
    assert props["score_name_bucket"] == "other"


@pytest.mark.anyio
async def test_comment_props_buckets_text_length_without_leaking(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server.comment with PII text MUST emit only text_length_bucket."""
    from opik_mcp import server
    from opik_mcp.score_comment import CommentResult

    target = Target(type="trace", id="00000000-0000-0000-0000-000000000001")
    long_pii = FORBIDDEN[2] * 50  # well over 1000 chars to hit the largest bucket
    assert len(long_pii) > 1000  # guard against future shrinkage of FORBIDDEN[2]
    monkeypatch.setattr(
        "opik_mcp.server.run_comment",
        lambda **_kw: _noop_coroutine_result(CommentResult(target=target)),
    )

    await server.comment(target=target, text=long_pii)
    _assert_no_leak(recorder.events)
    props = _tool_called(recorder.events)
    assert props["target_type"] == "trace"
    assert props["text_length_bucket"] == ">1000"
    # Sanity: the raw text length is gone, replaced by the bucket label.
    assert str(len(long_pii)) not in props["text_length_bucket"]


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
