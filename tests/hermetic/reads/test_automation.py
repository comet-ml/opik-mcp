"""read('automation') and its list: a project's automations, with their stats."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
    refuse,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import AUTOMATION_ID, GATE_AUTOMATION_ID, PROJECT_NAME

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [
    ("read", {"id": AUTOMATION_ID}),
    ("read", {"id": GATE_AUTOMATION_ID}),
    ("list", {"project_name": PROJECT_NAME}),
]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "automation", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "automation", CALLS)


async def test_a_backend_without_automations_says_so_without_naming_a_route(
    backend: StubBackend,
) -> None:
    """An older backend answers 404 on the collection, which names no record;
    the refusal says what is missing and where the rules still are."""
    backend.has_automations = False
    async with stdio_session(backend) as session:
        refusal = await refuse(session, "list", entity_type="automation", project_name=PROJECT_NAME)
    assert "no automations yet" in refusal
    assert "online_rule" in refusal
    assert "/v1/private" not in refusal


async def test_a_sort_is_refused_with_what_the_backend_offers(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        refusal = await refuse(
            session, "list", entity_type="automation", project_name=PROJECT_NAME, sort="name"
        )
        answer = await call(session, "schema", operation="list.automation")
    assert "sort is not supported" in refusal
    for field in ("recipe", "enabled", "event"):
        assert field in answer
