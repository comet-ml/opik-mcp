"""One HTTP connection per tool call.

Serving one tool call can mean several backend requests — a trace plus its
spans today, a project overview's fan-out tomorrow. Every ``httpx.AsyncClient``
carries its own connection pool, so building one per request means a fresh TCP
+ TLS handshake per request. Measured against www.comet.com: six sequential
GETs cost 2648 ms with a client per request and 1355 ms with one shared client
— 259 ms per extra request.

These tests pin the lifecycle at the tool entry points rather than inside the
client: one tool call builds at most one HTTP client, and a client handed in
from outside is used as-is and left open for whoever owns it. Counting
constructions is the observable form of "one connection" at this level — a
second client is a second pool is a second handshake.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikClient
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read

OPIK_BASE = "https://opik.test"
SETTINGS = Settings(opik_api_key="k", comet_workspace="ws", opik_url=OPIK_BASE)

TRACE_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clients_built(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.AsyncClient]]:
    """Record every ``httpx.AsyncClient`` constructed while the fixture is active.

    Patched on the ``httpx`` module because the client resolves
    ``httpx.AsyncClient`` at call time, so this catches construction wherever
    in our code it happens.
    """
    built: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def spy(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        instance = real(*args, **kwargs)
        built.append(instance)
        return instance

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    yield built


def _page(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {"content": content, "page": 1, "size": len(content), "total": len(content)}


@pytest.mark.anyio
async def test_composite_read_builds_one_client_for_both_requests(
    clients_built: list[httpx.AsyncClient],
) -> None:
    """A trace read is two backend calls — the trace, then its spans. Both
    must ride one connection; a client per call is a handshake per call."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        trace = mock.get(f"/v1/private/traces/{TRACE_ID}").mock(
            return_value=httpx.Response(
                200, json={"id": TRACE_ID, "name": "t", "project_id": PROJECT_ID}
            ),
        )
        spans = mock.get("/v1/private/spans").mock(
            return_value=httpx.Response(200, json=_page([{"id": "s-1", "name": "span"}])),
        )
        await run_read("trace", TRACE_ID, settings=SETTINGS)

    assert trace.called
    assert spans.called
    assert len(clients_built) == 1, (
        f"{len(clients_built)} HTTP clients built for one read; "
        "every backend call in a tool call must share one connection"
    )


@pytest.mark.anyio
async def test_list_builds_one_client(clients_built: list[httpx.AsyncClient]) -> None:
    """The list entry point owns its client the same way read does."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(200, json=_page([{"id": PROJECT_ID, "name": "demo"}])),
        )
        await run_list("project", settings=SETTINGS)

    assert len(clients_built) == 1


@pytest.mark.anyio
async def test_read_leaves_an_injected_client_open_and_builds_none(
    clients_built: list[httpx.AsyncClient],
) -> None:
    """A caller that supplies its own client owns its lifecycle: we must not
    build another one, and we must not close theirs out from under them —
    a hosted server reuses one client across many tool calls."""
    async with httpx.AsyncClient() as injected:
        clients_built.clear()  # the `async with` above is the test's own client
        opik = OpikClient(base_url=OPIK_BASE, api_key="k", workspace="ws", client=injected)
        with respx.mock(base_url=OPIK_BASE) as mock:
            mock.get(f"/v1/private/traces/{TRACE_ID}").mock(
                return_value=httpx.Response(
                    200, json={"id": TRACE_ID, "name": "t", "project_id": PROJECT_ID}
                ),
            )
            mock.get("/v1/private/spans").mock(
                return_value=httpx.Response(200, json=_page([])),
            )
            await run_read("trace", TRACE_ID, client=opik, settings=SETTINGS)

        assert not clients_built, "an injected client must not be replaced"
        assert not injected.is_closed, "an injected client must be left open for its owner"


@pytest.mark.anyio
async def test_the_connection_does_not_survive_the_call(
    clients_built: list[httpx.AsyncClient],
) -> None:
    """A hosted process serves many tool calls for many tenants. A connection
    that outlived its call would be process-wide state nobody asked for, so
    each call's client must be closed on the way out — including when the call
    fails."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get("/v1/private/projects").mock(
            return_value=httpx.Response(200, json=_page([{"id": PROJECT_ID, "name": "demo"}])),
        )
        await run_list("project", settings=SETTINGS)
    assert [c.is_closed for c in clients_built] == [True]

    clients_built.clear()
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.get(f"/v1/private/traces/{TRACE_ID}").mock(
            return_value=httpx.Response(500, json={"message": "boom"}),
        )
        with pytest.raises(ToolError):
            await run_read("trace", TRACE_ID, settings=SETTINGS)
    assert [c.is_closed for c in clients_built] == [True], (
        "a failed call must still close its connection"
    )


@pytest.mark.anyio
async def test_concurrent_requests_in_one_call_share_the_connection() -> None:
    """The project overview fans out several backend calls at once. Sharing one
    client across them is what makes that cost one handshake rather than one
    each, so concurrent use has to be correct: every leg gets its own response,
    with its own auth headers."""
    async with httpx.AsyncClient() as http:
        opik = OpikClient(base_url=OPIK_BASE, api_key="k", workspace="ws", client=http)
        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.get("/v1/private/projects").mock(
                side_effect=lambda request: httpx.Response(
                    200, json=_page([{"id": request.url.params["name"], "name": "p"}])
                ),
            )
            results = await asyncio.gather(*(opik.list_projects(name=f"p-{i}") for i in range(6)))

    assert [r["content"][0]["id"] for r in results] == [f"p-{i}" for i in range(6)]
    assert len(route.calls) == 6
    assert all(call.request.headers["authorization"] == "k" for call in route.calls)
