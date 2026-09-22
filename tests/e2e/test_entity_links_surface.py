"""Every link this server hands out, end to end over stdio.

WHAT THIS COVERS THAT NOTHING ELSE DOES. A url is assembled from two session
facts — where Opik lives and which workspace this credential is in — and from
a record the backend sent. The in-process suites supply both by hand, so they
prove the shape and cannot prove the wiring: a handler whose ``link_fn``
stopped being registered, a settings path that resolves the UI base
differently under a real environment, a record whose ``project_id`` the
fetcher drops before the link is built. None of that fails a unit test.

So this runs the real server as a subprocess against ``stub_backend`` and
reads the links out of the answers an agent would actually get.

The sweep at the end is the part that does not need maintaining: it walks
every url any of these answers contains and asserts the live-route shape, so
an entity that starts emitting a retired address fails here even if nobody
adds a case for it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.e2e.stub_backend import (
    EXPERIMENT_A,
    PROJECT_ID,
    PROJECT_NAME,
    SPAN_ID,
    SUITE_ID,
    THREAD_ID,
    TRACE_ID,
    StubBackend,
)
from tests.test_read_list.test_link_shape import live_project_url

_TIMEOUT_S = 60
_WORKSPACE = "stub-workspace"
_URL = re.compile(r"https?://[^\s\"',)]+")

pytestmark = pytest.mark.e2e


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@asynccontextmanager
async def _session(stub: StubBackend, **extra_env: str) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            "OPIK_URL": f"http://127.0.0.1:{stub.port}/api",
            "OPIK_API_KEY": "stub-key",
            "OPIK_WORKSPACE": _WORKSPACE,
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
            "OPIK_MCP_LOG_LEVEL": "WARNING",
            **extra_env,
        },
    )
    with anyio.fail_after(_TIMEOUT_S):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


async def _text(session: ClientSession, tool: str, args: dict[str, object]) -> str:
    result = await session.call_tool(tool, args)
    return "".join(part.text for part in result.content if hasattr(part, "text"))


def _payload(answer: str) -> dict[str, object]:
    body = answer.split("\n", 1)[1] if "\n" in answer else answer
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


#: (entity, read args, the path the link must end on). One per entity that
#: claims a link, so a handler that quietly stops emitting one fails here.
_READS: list[tuple[str, dict[str, object], str]] = [
    ("project", {"id": PROJECT_ID}, f"/projects/{PROJECT_ID}/logs"),
    (
        "trace",
        {"id": TRACE_ID},
        f"/projects/{PROJECT_ID}/logs?logsType=traces&trace={TRACE_ID}",
    ),
    (
        "span",
        {"id": SPAN_ID},
        f"/projects/{PROJECT_ID}/logs?logsType=traces&trace={TRACE_ID}&span={SPAN_ID}",
    ),
    (
        "thread",
        {"id": THREAD_ID, "project_id": PROJECT_ID},
        f"/projects/{PROJECT_ID}/logs?logsType=threads&thread={THREAD_ID}",
    ),
    (
        "experiment",
        {"id": EXPERIMENT_A},
        f"/projects/{PROJECT_ID}/experiments/{SUITE_ID}/compare",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("entity", "args", "ends_on"), _READS, ids=[r[0] for r in _READS])
async def test_a_read_carries_a_link_that_opens_the_thing_it_returned(
    backend: StubBackend, entity: str, args: dict[str, object], ends_on: str
) -> None:
    async with _session(backend) as session:
        answer = await _text(session, "read", {"entity_type": entity, **args})
    url = _payload(answer).get("url")
    assert isinstance(url, str), f"read({entity!r}) returned no url"
    assert url.startswith(f"http://127.0.0.1:{backend.port}/{_WORKSPACE}"), url
    assert ends_on in url, url
    assert live_project_url(url), url


@pytest.mark.anyio
async def test_the_header_tells_the_agent_what_to_call_the_link(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        answer = await _text(session, "read", {"entity_type": "project", "id": PROJECT_ID})
    header = answer.splitlines()[0]
    assert "open as a link named" in header
    assert "http" not in header, "the header names the link; the url is the payload's job"


@pytest.mark.anyio
async def test_a_page_of_rows_carries_one_template_and_no_row_urls(
    backend: StubBackend,
) -> None:
    async with _session(backend) as session:
        answer = await _text(session, "list", {"entity_type": "trace", "project_id": PROJECT_ID})
    assert "Open a row in Opik:" in answer
    assert answer.count("http") == 1, "one link for the page, not one per row"
    template = _URL.search(answer)
    assert template is not None
    assert live_project_url(template.group().replace("{id}", TRACE_ID))


@pytest.mark.anyio
async def test_the_link_is_built_from_this_session_not_baked_in(
    backend: StubBackend,
) -> None:
    """The workspace in a link is a fact about the credential in front of us.

    Not a constant: the same server serves one workspace over stdio and
    another over the next connection, and a link that kept the first would
    send the second's user somewhere they cannot see. (The refusal when no
    workspace can be known at all is OAuth-only — under an API key the
    settings always name one — so it is unit-tested rather than here.)
    """
    async with _session(backend, OPIK_WORKSPACE="somewhere-else") as session:
        answer = await _text(session, "read", {"entity_type": "project", "id": PROJECT_ID})
    url = _payload(answer)["url"]
    assert isinstance(url, str)
    assert f"/somewhere-else/projects/{PROJECT_ID}/" in url
    assert _WORKSPACE not in url


@pytest.mark.anyio
async def test_no_answer_anywhere_contains_a_retired_address(
    backend: StubBackend,
) -> None:
    """The sweep. Every url in every answer above, against the live shape.

    This is the case nobody has to remember to add: an entity that starts
    emitting a workspace-level path, or one of the routes v2 keeps only to
    forward, fails here without a case of its own.
    """
    offenders: list[str] = []
    async with _session(backend) as session:
        answers = [
            await _text(session, "read", {"entity_type": entity, **args})
            for entity, args, _ in _READS
        ]
        answers.append(
            await _text(session, "list", {"entity_type": "trace", "project_id": PROJECT_ID})
        )
        answers.append(
            await _text(
                session, "list", {"entity_type": "score_name", "project_name": PROJECT_NAME}
            )
        )
    for answer in answers:
        for url in _URL.findall(answer):
            # A template's slots stand in for a row's columns; fill them so the
            # shape check sees the address a caller would actually open.
            filled = re.sub(r"\{[a-z_]+\}", "x", url)
            if not live_project_url(filled):
                offenders.append(url)
    assert not offenders, "answers carried addresses the UI does not serve:\n  " + "\n  ".join(
        offenders
    )
