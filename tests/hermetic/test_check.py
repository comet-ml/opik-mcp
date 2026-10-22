"""`opik-mcp --check` as an agent runs it: the real process, against the stub."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Iterator

import pytest

from tests.hermetic.servers import API_KEY, stub_rest_base
from tests.hermetic.stub_backend import StubBackend

pytestmark = pytest.mark.hermetic


@pytest.fixture
def backend() -> Iterator[StubBackend]:
    stub = StubBackend()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


def _check(opik_url: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPIK_", "COMET_"))}
    return subprocess.run(
        [sys.executable, "-m", "opik_mcp", "--check"],
        env={
            **env,
            "OPIK_URL": opik_url,
            "OPIK_API_KEY": API_KEY,
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "OPIK_MCP_SENTRY_ENABLED": "false",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_check__a_working_setup_exits_0(backend: StubBackend) -> None:
    result = _check(stub_rest_base(backend))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith(f"OK: Opik at {stub_rest_base(backend)}, workspace ")


def test_check__a_rejected_key_exits_1_with_what_to_change(backend: StubBackend) -> None:
    backend.dead_bearers.add(API_KEY)

    result = _check(stub_rest_base(backend))

    assert result.returncode == 1
    assert "(401). Check OPIK_API_KEY and OPIK_WORKSPACE" in result.stdout


def test_check__nothing_listening_exits_1_with_where_it_tried() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{probe.getsockname()[1]}/api"

    result = _check(closed)

    assert result.returncode == 1
    assert "Could not reach Opik to list projects: " in result.stdout
    assert f"(tried {closed})" in result.stdout
    assert "Is Opik running?" in result.stdout
