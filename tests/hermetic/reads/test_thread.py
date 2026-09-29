"""read('thread') and list('thread'): a conversation, addressed by its own id."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    payload,
    retired_addresses,
    text,
)
from tests.hermetic.servers import WORKSPACE, stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID, THREAD_ID
from tests.read_list.test_link_shape import live_project_url

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [
    ("read", {"id": THREAD_ID, "project_id": PROJECT_ID}),
    ("list", {"project_id": PROJECT_ID}),
]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "thread", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "thread", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [
            await text(
                session,
                "read",
                {"entity_type": "thread", "id": THREAD_ID, "project_id": PROJECT_ID},
            )
        ]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_read_carries_a_link_that_opens_the_thing_it_returned(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await text(
            session, "read", {"entity_type": "thread", "id": THREAD_ID, "project_id": PROJECT_ID}
        )
    url = payload(answer).get("url")
    assert isinstance(url, str), "read('thread') returned no url"
    assert url.startswith(f"http://127.0.0.1:{backend.port}/{WORKSPACE}"), url
    assert f"/projects/{PROJECT_ID}/logs?logsType=threads&thread={THREAD_ID}" in url, url
    assert live_project_url(url), url
