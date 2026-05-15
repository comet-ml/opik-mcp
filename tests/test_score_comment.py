from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from opik_mcp.config import MissingConfigError, Settings
from opik_mcp.opik_client import FeedbackScore
from opik_mcp.score_comment import (
    CommentResult,
    ScoreResult,
    Target,
    run_comment,
    run_score,
)


@dataclass
class FakeOpikClient:
    """Captures every call the orchestrator makes — one slot per endpoint."""

    trace_scores: list[tuple[str, FeedbackScore]] = field(default_factory=list)
    span_scores: list[tuple[str, FeedbackScore]] = field(default_factory=list)
    thread_scores: list[tuple[str, FeedbackScore, str | None]] = field(default_factory=list)
    trace_comments: list[tuple[str, str]] = field(default_factory=list)
    span_comments: list[tuple[str, str]] = field(default_factory=list)
    thread_comments: list[tuple[str, str]] = field(default_factory=list)

    async def add_trace_feedback_score(self, trace_id: str, score: FeedbackScore) -> None:
        self.trace_scores.append((trace_id, score))

    async def add_span_feedback_score(self, span_id: str, score: FeedbackScore) -> None:
        self.span_scores.append((span_id, score))

    async def add_thread_feedback_score(
        self,
        thread_id: str,
        score: FeedbackScore,
        *,
        project_name: str | None = None,
    ) -> None:
        self.thread_scores.append((thread_id, score, project_name))

    async def add_trace_comment(self, trace_id: str, text: str) -> None:
        self.trace_comments.append((trace_id, text))

    async def add_span_comment(self, span_id: str, text: str) -> None:
        self.span_comments.append((span_id, text))

    async def add_thread_comment(self, thread_id: str, text: str) -> None:
        self.thread_comments.append((thread_id, text))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"opik_api_key": "k", "comet_workspace": "ws"}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- score dispatch ------------------------------------------------------- #


@pytest.mark.anyio
async def test_score_trace_routes_to_trace_endpoint() -> None:
    client = FakeOpikClient()
    result = await run_score(
        target=Target(type="trace", id="tr-1"),
        name="helpfulness",
        value=0.8,
        settings=_settings(),
        client=client,
    )

    assert client.trace_scores == [("tr-1", FeedbackScore(name="helpfulness", value=0.8))]
    assert client.span_scores == [] and client.thread_scores == []
    assert isinstance(result, ScoreResult)
    assert result.target.type == "trace" and result.target.id == "tr-1"
    assert result.value == 0.8


@pytest.mark.anyio
async def test_score_span_routes_to_span_endpoint() -> None:
    client = FakeOpikClient()
    await run_score(
        target=Target(type="span", id="sp-1"),
        name="x",
        value=1.0,
        settings=_settings(),
        client=client,
    )

    assert client.span_scores == [("sp-1", FeedbackScore(name="x", value=1.0))]
    assert client.trace_scores == [] and client.thread_scores == []


@pytest.mark.anyio
async def test_score_thread_routes_to_thread_endpoint_with_optional_project() -> None:
    client = FakeOpikClient()
    await run_score(
        target=Target(type="thread", id="th-1"),
        name="x",
        value=0.5,
        project_name="demo",
        settings=_settings(),
        client=client,
    )

    assert client.thread_scores == [
        ("th-1", FeedbackScore(name="x", value=0.5), "demo"),
    ]


@pytest.mark.anyio
async def test_score_trace_ignores_project_name() -> None:
    """`project_name` applies only to thread targets — silently dropped elsewhere."""
    client = FakeOpikClient()
    await run_score(
        target=Target(type="trace", id="tr-1"),
        name="x",
        value=0.5,
        project_name="chatbot-prod",
        settings=_settings(),
        client=client,
    )

    assert client.trace_scores == [("tr-1", FeedbackScore(name="x", value=0.5))]


@pytest.mark.anyio
async def test_score_passes_reason_and_category_through_to_dataclass() -> None:
    client = FakeOpikClient()
    await run_score(
        target=Target(type="trace", id="tr-1"),
        name="quality",
        value=0.9,
        reason="user-confirmed",
        category_name="manual",
        settings=_settings(),
        client=client,
    )

    _, score = client.trace_scores[0]
    assert score.reason == "user-confirmed"
    assert score.category_name == "manual"
    assert score.source == "sdk"  # default — MCP server is reporting as SDK


# --- comment dispatch ----------------------------------------------------- #


@pytest.mark.anyio
async def test_comment_trace_routes_to_trace_endpoint() -> None:
    client = FakeOpikClient()
    result = await run_comment(
        target=Target(type="trace", id="tr-1"),
        text="looks good",
        settings=_settings(),
        client=client,
    )

    assert client.trace_comments == [("tr-1", "looks good")]
    assert client.span_comments == [] and client.thread_comments == []
    assert isinstance(result, CommentResult)
    assert result.target.id == "tr-1"


@pytest.mark.anyio
async def test_comment_span_routes_to_span_endpoint() -> None:
    client = FakeOpikClient()
    await run_comment(
        target=Target(type="span", id="sp-1"),
        text="note",
        settings=_settings(),
        client=client,
    )

    assert client.span_comments == [("sp-1", "note")]


@pytest.mark.anyio
async def test_comment_thread_routes_to_thread_endpoint() -> None:
    client = FakeOpikClient()
    await run_comment(
        target=Target(type="thread", id="th-1"),
        text="see follow-up",
        settings=_settings(),
        client=client,
    )

    assert client.thread_comments == [("th-1", "see follow-up")]


# --- target validation (pydantic boundary) -------------------------------- #


def test_target_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Target(type="dataset", id="x")  # type: ignore[arg-type]


def test_target_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        Target(type="trace", id="")


# --- config requirements -------------------------------------------------- #


@pytest.mark.anyio
async def test_score_raises_missing_config_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("OPIK_API_KEY", "COMET_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(MissingConfigError):
        await run_score(
            target=Target(type="trace", id="tr-1"),
            name="x",
            value=1.0,
            settings=Settings(),
        )


@pytest.mark.anyio
async def test_comment_raises_missing_config_without_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMET_WORKSPACE", raising=False)
    with pytest.raises(MissingConfigError):
        await run_comment(
            target=Target(type="trace", id="tr-1"),
            text="x",
            settings=Settings(opik_api_key="k"),
        )
