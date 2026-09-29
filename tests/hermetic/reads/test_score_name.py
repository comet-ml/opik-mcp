"""list('score_name'): the score names a project has recorded, paged here."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
    retired_addresses,
    text,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID, PROJECT_NAME

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("list", {"project_id": PROJECT_ID})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "score_name", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "score_name", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [
            await text(session, "list", {"entity_type": "score_name", "project_name": PROJECT_NAME})
        ]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_the_score_names_answer_under_a_project(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        scores = await call(session, "list", entity_type="score_name", project_id=PROJECT_ID)

    assert "Hallucination" in scores
    assert "Answer Relevance" in scores
    assert "trace, span and thread scores together" in scores, "what a name does not say"


async def test_a_score_name_page_is_cut_here_and_says_so(backend: StubBackend) -> None:
    """The endpoint has no paging of its own, so returning everything with a
    total of everything promised a page 2 that returned the same rows."""
    backend.score_names = [f"score-{i:02d}" for i in range(30)]
    async with stdio_session(backend) as session:
        first = await call(
            session, "list", entity_type="score_name", project_id=PROJECT_ID, size=10
        )
        second = await call(
            session, "list", entity_type="score_name", project_id=PROJECT_ID, size=10, page=2
        )

    assert "score-00" in first
    assert "score-09" in first
    assert "score-10" not in first
    assert "score-10" in second
    assert "score-00" not in second
