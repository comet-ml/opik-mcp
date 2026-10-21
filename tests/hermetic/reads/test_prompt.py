"""read('prompt') and list('prompt'): a prompt with its versions inlined."""

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
from tests.hermetic.servers import stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID, PROMPT_ID
from tests.read_list.test_link_shape import live_project_url

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("read", {"id": PROMPT_ID}), ("list", {})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "prompt", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "prompt", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [await text(session, "read", {"entity_type": "prompt", "id": PROMPT_ID})]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_project_scoped_record_links_to_its_page(backend: StubBackend) -> None:
    """A prompt may exist without a project. The stub's carries one, so it must
    link; if this fails because the stub's record has no project_id, the stub
    is what is wrong. The workspace-level record is unit-tested."""
    async with stdio_session(backend) as session:
        answer = await text(session, "read", {"entity_type": "prompt", "id": PROMPT_ID})
    url = payload(answer).get("url")
    assert isinstance(url, str), "read('prompt') returned no url"
    assert f"/projects/{PROJECT_ID}/prompts/{PROMPT_ID}" in url, url
    assert live_project_url(url), url
