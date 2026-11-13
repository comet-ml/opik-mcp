"""list('loop'): the project's objects and the enabled automations between them."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
    refuse,
    retired_addresses,
    text,
)
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID, PROJECT_NAME

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("list", {"project_name": PROJECT_NAME}), ("list", {"project_id": PROJECT_ID})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "loop", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "loop", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [
            await text(session, "list", {"entity_type": "loop", **args}) for _, args in CALLS
        ]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_project_with_nothing_running_is_one_node_and_where_to_start(
    backend: StubBackend,
) -> None:
    loop = {
        "project_id": PROJECT_ID,
        "window_days": 14,
        "nodes": [
            {
                "id": "production",
                "kind": "project",
                "name": "production traces",
                "object_id": PROJECT_ID,
            }
        ],
        "edges": [],
    }
    backend.loop = loop
    async with stdio_session(backend) as session:
        answer = await call(session, "list", entity_type="loop", project_name=PROJECT_NAME)
    assert "Found 0 edges over 1 node(s) (14 days)" in answer
    assert f"list('workflow', project_id='{PROJECT_ID}')" in answer


async def test_narrowing_is_refused_with_the_call_to_retry(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        refusal = await refuse(
            session, "list", entity_type="loop", project_name=PROJECT_NAME, sort="name", size=5
        )
    assert "does not take size, sort" in refusal
    assert "list('loop', project_id=…)" in refusal
    assert backend.sent("/v1/private/automations/loop") == []
