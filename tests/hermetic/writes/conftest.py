from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession

from tests.hermetic.servers import (
    API_KEY,
    HttpServer,
    http_session,
    stdio_session,
    stub_with_http_server,
)
from tests.hermetic.stub_backend import StubBackend
from tests.hermetic.writes.surface import Wire


@pytest.fixture(scope="package")
def http_server() -> Iterator[tuple[StubBackend, HttpServer]]:
    with stub_with_http_server() as running:
        yield running


@pytest.fixture(params=["stdio", "http"])
def wire(request: pytest.FixtureRequest) -> Iterator[Wire]:
    if request.param == "http":
        stub, server = request.getfixturevalue("http_server")
        stub.reset()

        @asynccontextmanager
        async def over_http() -> AsyncIterator[ClientSession]:
            async with http_session(server, bearer=API_KEY) as session:
                yield session

        yield Wire("http", stub, over_http)
        return

    stub = StubBackend()
    stub.start()

    @asynccontextmanager
    async def over_stdio() -> AsyncIterator[ClientSession]:
        async with stdio_session(stub) as session:
            yield session

    try:
        yield Wire("stdio", stub, over_stdio)
    finally:
        stub.stop()
