"""list('online_rule'): the project's online evaluators."""

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
    await assert_sized_envelopes(http, "online_rule", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "online_rule", CALLS)


async def test_the_rules_answer_under_a_project(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        rules = await call(session, "list", entity_type="online_rule", project_name=PROJECT_NAME)

    assert "hallucination-judge" in rules
    assert "llm_as_judge" in rules
    assert "0.5" in rules
