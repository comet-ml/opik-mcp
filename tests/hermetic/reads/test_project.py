"""read('project') and list('project'): the overview, assembled from five endpoints."""

from __future__ import annotations

import json

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
from tests.hermetic.stub_records import PROJECT_ID, PROJECT_NAME
from tests.read_list.test_link_shape import live_project_url

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

CALLS: list[Call] = [("read", {"id": PROJECT_ID}), ("list", {})]


async def test_each_answer_is_a_sized_header_over_its_envelope(http: Http) -> None:
    await assert_sized_envelopes(http, "project", CALLS)


async def test_a_failing_backend_is_refused_without_its_body_or_path(http: Http) -> None:
    await assert_refusals_hide_the_backend(http, "project", CALLS)


async def test_no_answer_contains_a_retired_address(http: Http) -> None:
    async with http.session() as session:
        answers = [await text(session, "read", {"entity_type": "project", "id": PROJECT_ID})]
    offenders = retired_addresses(answers)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )


async def test_a_read_carries_a_link_that_opens_the_thing_it_returned(
    backend: StubBackend,
) -> None:
    async with stdio_session(backend) as session:
        answer = await text(session, "read", {"entity_type": "project", "id": PROJECT_ID})
    url = payload(answer).get("url")
    assert isinstance(url, str), "read('project') returned no url"
    assert url.startswith(f"http://127.0.0.1:{backend.port}/{WORKSPACE}"), url
    assert f"/projects/{PROJECT_ID}/logs" in url, url
    assert live_project_url(url), url


async def test_the_header_tells_the_agent_what_to_call_the_link(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await text(session, "read", {"entity_type": "project", "id": PROJECT_ID})
    header = answer.splitlines()[0]
    assert "open as a link named" in header
    assert "http" not in header, "the header names the link; the url is the payload's job"


async def test_the_link_is_built_from_this_session_not_baked_in(backend: StubBackend) -> None:
    """The workspace in a link is a fact about the credential in front of us.

    Not a constant: the same server serves one workspace over stdio and
    another over the next connection, and a link that kept the first would
    send the second's user somewhere they cannot see. (The refusal when no
    workspace can be known at all is OAuth-only, so it is unit-tested.)
    """
    async with stdio_session(backend, OPIK_WORKSPACE="somewhere-else") as session:
        answer = await text(session, "read", {"entity_type": "project", "id": PROJECT_ID})
    url = payload(answer)["url"]
    assert isinstance(url, str)
    assert f"/somewhere-else/projects/{PROJECT_ID}/" in url
    assert WORKSPACE not in url


async def test_a_project_read_is_assembled_from_every_part(backend: StubBackend) -> None:
    """One call, five endpoints, one payload, and each part in it."""
    async with stdio_session(backend) as session:
        answer = await call(session, "read", entity_type="project", id=PROJECT_ID)

    body = json.loads(answer.split("\n", 1)[1])
    assert body["project"]["name"] == PROJECT_NAME
    assert body["summary"]["source"] == "sdk"
    assert body["summary"]["traces"]["count"] == {"current": 120.0, "previous": 80.0}
    assert body["vocabulary"]["score_names"]["names"] == ["Hallucination", "Answer Relevance"]
    assert body["vocabulary"]["usage_keys"]["total"] == 3
    assert body["vocabulary"]["online_rules"]["names"] == ["hallucination-judge"]
    assert body["contains"]["experiment"]["name"] == "rerank-v3"
    assert body["url"].endswith(f"/projects/{PROJECT_ID}/logs")

    # The fan-out went out as five calls on one connection, not one call that
    # the read then guessed the rest from.
    for endpoint in (
        f"/projects/{PROJECT_ID}",
        "kpi-cards",
        "feedback-scores/names",
        "token-usage/names",
        "activities",
        "automations/evaluators/",
    ):
        assert backend.called(endpoint), f"{endpoint} was never called"


async def test_the_summary_asks_for_sdk_traffic_as_a_json_string(backend: StubBackend) -> None:
    """``kpi-cards`` declares ``filters`` as a String where every other
    endpoint takes an array. Sending the array shape silently drops the
    filter, and the numbers stop matching the Logs page."""
    async with stdio_session(backend) as session:
        await call(session, "read", entity_type="project", id=PROJECT_ID)

    sent = backend.one("kpi-cards")
    assert isinstance(sent.body and sent.payload["filters"], str)
    assert sent.filters() == [{"field": "source", "operator": "=", "key": "", "value": "sdk"}]
    assert sent.payload["entity_type"] == "traces"


async def test_a_window_reaches_the_backend_and_comes_back_stated(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await call(
            session,
            "read",
            entity_type="project",
            id=PROJECT_ID,
            since="2026-08-01T00:00:00Z",
            until="2026-08-31T00:00:00Z",
        )

    window = json.loads(answer.split("\n", 1)[1])["summary"]["window"]
    assert window["since"] == "2026-08-01T00:00:00Z"
    assert window["until"] == "2026-08-31T00:00:00Z"
    assert window["days"] == 30
    assert window["compared_to"] == {
        "since": "2026-07-02T00:00:00Z",
        "until": "2026-08-01T00:00:00Z",
    }
    sent = backend.one("kpi-cards")
    assert sent.payload["interval_start"] == "2026-08-01T00:00:00Z"
    assert sent.payload["interval_end"] == "2026-08-31T00:00:00Z"


async def test_one_failing_part_does_not_take_the_read_down(backend: StubBackend) -> None:
    """Every decoration is optional; the record is not. A backend that fails
    on the score names must still answer the question that was asked."""
    backend.failing = {"feedback-scores/names"}
    async with stdio_session(backend) as session:
        answer = await call(session, "read", entity_type="project", id=PROJECT_ID)

    body = json.loads(answer.split("\n", 1)[1])
    assert body["project"]["name"] == PROJECT_NAME
    assert body["summary"]["traces"]["count"]["current"] == 120.0
    assert "error" in body["vocabulary"]["score_names"], "the failure is stated, not hidden"
    assert "names" not in body["vocabulary"]["score_names"]
    assert body["vocabulary"]["usage_keys"]["names"], "its siblings still answered"


async def test_a_project_is_readable_by_name(backend: StubBackend) -> None:
    async with stdio_session(backend) as session:
        answer = await call(session, "read", entity_type="project", id=PROJECT_NAME)

    assert json.loads(answer.split("\n", 1)[1])["project"]["id"] == PROJECT_ID


async def test_the_project_list_still_answers_after_the_split(backend: StubBackend) -> None:
    """The refactor gave every entity its own namespace. A handler that fell
    out of the registry fails nothing until someone calls it."""
    async with stdio_session(backend) as session:
        projects = await call(session, "list", entity_type="project")

    assert PROJECT_NAME in projects
