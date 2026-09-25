"""read('trace') and list('trace'): the record whole, its spans slim."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
    URL,
    Call,
    Http,
    assert_refusals_hide_the_backend,
    assert_sized_envelopes,
    call,
    payload,
    retired_addresses,
    text,
)
from tests.hermetic.servers import WORKSPACE, stdio_session
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.stub_records import PROJECT_ID, TRACE_ID
from tests.read_list.test_link_shape import live_project_url

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("read", {"id": TRACE_ID}), ("list", {"project_id": PROJECT_ID})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "trace", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "trace", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [
            await text(session, "read", {"entity_type": "trace", "id": TRACE_ID}),
            await text(session, "list", {"entity_type": "trace", "project_id": PROJECT_ID}),
        ]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_read_carries_a_link_that_opens_the_thing_it_returned(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await text(session, "read", {"entity_type": "trace", "id": TRACE_ID})
    url = payload(answer).get("url")
    assert isinstance(url, str), "read('trace') returned no url"
    assert url.startswith(f"http://127.0.0.1:{backend.port}/{WORKSPACE}"), url
    assert f"/projects/{PROJECT_ID}/logs?logsType=traces&trace={TRACE_ID}" in url, url
    assert live_project_url(url), url


async def test_a_page_of_rows_carries_one_template_and_no_row_urls(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await text(session, "list", {"entity_type": "trace", "project_id": PROJECT_ID})
    assert "Open a row in Opik:" in answer
    assert answer.count("http") == 1, "one link for the page, not one per row"
    template = URL.search(answer)
    assert template is not None
    assert live_project_url(template.group().replace("{id}", TRACE_ID))


async def test_a_trace_is_listed_and_read_after_the_split(backend: StubBackend) -> None:
    """The refactor gave every entity its own namespace. A handler that fell
    out of the registry fails nothing until someone calls it."""
    async with stdio_session(backend) as session:
        traces = await call(session, "list", entity_type="trace", project_id=PROJECT_ID, size=5)
        trace = await call(session, "read", entity_type="trace", id=TRACE_ID)

    assert TRACE_ID in traces
    assert '"name": "checkout"' in trace


async def test_a_trace_read_asks_for_slim_spans_and_a_whole_trace(backend: StubBackend) -> None:
    """``truncate`` is a query parameter, so it is only real once it is on the
    wire, and the backend parses it as a literal, which a Python ``True``
    would not survive. The other half of the assertion is the one that keeps
    the design honest: the record the caller named carries no such parameter,
    which is what makes a cut span recoverable."""
    async with stdio_session(backend) as session:
        answer = await call(session, "read", entity_type="trace", id=TRACE_ID)

    assert backend.one("/v1/private/spans").query["truncate"] == ["true"]
    assert "truncate" not in backend.one(f"/v1/private/traces/{TRACE_ID}").query
    assert "1 of 1 spans had a field cut" in answer, "counted from what arrived"
