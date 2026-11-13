"""list('workflow'): each recipe's state for a project."""

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
    await assert_sized_envelopes(http, "workflow", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "workflow", CALLS)


async def test_a_named_field_reaches_into_the_estimate(backend: StubBackend) -> None:
    """The table summarises the estimate; a dotted ``fields`` entry reads one figure of it."""
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "list",
            entity_type="workflow",
            project_name=PROJECT_NAME,
            fields=["recipe", "estimate.fired"],
        )
    assert "collect | 240" in answer
    assert "align | 0" in answer
