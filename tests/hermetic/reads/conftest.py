from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.hermetic.reads.answers import Http
from tests.hermetic.servers import HttpServer, stub_with_http_server
from tests.hermetic.stub_backend import StubBackend


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    """A stub of its own, for a test that starts a stdio server against it."""
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture(scope="package")
def http_server() -> Iterator[tuple[StubBackend, HttpServer]]:
    """One HTTP server for the package: its sessions are cheap, its startup is not."""
    with stub_with_http_server() as running:
        yield running


@pytest.fixture
def http(http_server: tuple[StubBackend, HttpServer]) -> Http:
    stub, server = http_server
    stub.reset()
    return Http(stub, server)
