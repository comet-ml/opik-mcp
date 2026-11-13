"""list('recommendation'): the ranked next steps, each with its write call."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_NAME

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("list", {"project_name": PROJECT_NAME})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "recommendation", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "recommendation", CALLS)


async def test_the_page_is_cut_here_and_says_what_is_left_out(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await call(
            session, "list", entity_type="recommendation", project_name=PROJECT_NAME, size=1
        )
    assert "showing 1 of 2" in answer
    assert "Use page=2" in answer
    assert "omitted: evidence" in answer
