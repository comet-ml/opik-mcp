"""read('experiment') and list('experiment'): one run, and the call that compares it."""

from __future__ import annotations

import pytest

from tests.hermetic.reads.answers import (
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
from tests.hermetic.stub_records import EXPERIMENT_A, PROJECT_ID, SUITE_ID
from tests.read_list.test_link_shape import live_project_url

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("read", {"id": EXPERIMENT_A}), ("list", {})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "experiment", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "experiment", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [await text(session, "read", {"entity_type": "experiment", "id": EXPERIMENT_A})]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_read_carries_a_link_that_opens_the_thing_it_returned(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await text(session, "read", {"entity_type": "experiment", "id": EXPERIMENT_A})
    url = payload(answer).get("url")
    assert isinstance(url, str), "read('experiment') returned no url"
    assert url.startswith(f"http://127.0.0.1:{backend.port}/{WORKSPACE}"), url
    assert f"/projects/{PROJECT_ID}/experiments/{SUITE_ID}/compare" in url, url
    assert live_project_url(url), url


async def test_reading_an_experiment_names_the_call_that_compares_it(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await call(session, "read", entity_type="experiment", id=EXPERIMENT_A)

    assert "list('dataset_item', experiment_ids=" in answer
    assert EXPERIMENT_A in answer
