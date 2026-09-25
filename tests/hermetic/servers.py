"""The server as a host launches it, over either transport, against the stub.

stdio is how Claude Code and Cursor run the PyPI package. Streamable HTTP is
what the Docker image serves at ``/opik/api/v1/mcp``: the same ``python -m
opik_mcp`` entrypoint, with ``OPIK_MCP_TRANSPORT=http``, and each request's
bearer passed through to the backend. Both are real subprocesses here, so the
startup path each deployment runs is the one under test.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import IO

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from tests.hermetic.stub_backend import StubBackend

#: A hung server must fail the job, not hang the runner.
SESSION_TIMEOUT_S = 60
#: How long uvicorn gets to bind before the fixture gives up.
HTTP_STARTUP_TIMEOUT_S = 30

WORKSPACE = "stub-workspace"
#: What the stdio server is configured with, and what an HTTP test sends as
#: its API-key bearer.
API_KEY = "stub-key"

#: Both telemetry channels off where they take effect, in the child process,
#: rather than by inheritance from conftest.
_QUIET_ENV = {
    "OPIK_MCP_ANALYTICS_ENABLED": "false",
    "OPIK_MCP_SENTRY_ENABLED": "false",
    "OPIK_MCP_LOG_LEVEL": "WARNING",
}


def stub_rest_base(stub: StubBackend) -> str:
    return f"http://127.0.0.1:{stub.port}/api"


@asynccontextmanager
async def stdio_session(stub: StubBackend, **extra_env: str) -> AsyncIterator[ClientSession]:
    """A session with a fresh stdio server that holds the install's own key."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opik_mcp"],
        env={
            **os.environ,
            "OPIK_URL": stub_rest_base(stub),
            "OPIK_API_KEY": API_KEY,
            "OPIK_WORKSPACE": WORKSPACE,
            **_QUIET_ENV,
            **extra_env,
        },
    )
    with anyio.fail_after(SESSION_TIMEOUT_S):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


@dataclass(frozen=True)
class HttpServer:
    """A running ``python -m opik_mcp`` serving Streamable HTTP."""

    port: int

    @property
    def mcp_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@contextmanager
def serve_http(stub: StubBackend, **extra_env: str) -> Iterator[HttpServer]:
    """The server the Docker image runs, on a loopback port.

    One process can serve many tests, since each test opens its own MCP
    session with its own bearer. No ``OPIK_API_KEY`` is set: the hosted image
    has none, and a request that reached the backend with the server's own
    key would hide a broken pass-through.
    """
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPIK_API_KEY", "COMET_API_KEY", "OPIK_WORKSPACE", "COMET_WORKSPACE"}
    }
    # A file, not a pipe: nothing reads a pipe while the server runs, and a
    # full one would block the server mid-test.
    with (
        tempfile.TemporaryFile() as stderr,
        subprocess.Popen(
            [sys.executable, "-m", "opik_mcp"],
            env={
                **env,
                "OPIK_URL": stub_rest_base(stub),
                "OPIK_MCP_TRANSPORT": "http",
                "OPIK_MCP_HOST": "127.0.0.1",
                "OPIK_MCP_PORT": str(port),
                **_QUIET_ENV,
                **extra_env,
            },
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        ) as process,
    ):
        try:
            _wait_until_live(process, port, stderr)
            yield HttpServer(port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@contextmanager
def stub_with_http_server(**extra_env: str) -> Iterator[tuple[StubBackend, HttpServer]]:
    """A stub and an HTTP server pointed at it, for a module to share."""
    stub = StubBackend()
    stub.start()
    try:
        with serve_http(stub, **extra_env) as server:
            yield stub, server
    finally:
        stub.stop()


def _wait_until_live(process: subprocess.Popen[bytes], port: int, stderr: IO[bytes]) -> None:
    deadline = time.monotonic() + HTTP_STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr.seek(0)
            output = stderr.read().decode(errors="replace")
            raise RuntimeError(f"HTTP server exited with {process.returncode}:\n{output}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"HTTP server did not answer /health within {HTTP_STARTUP_TIMEOUT_S}s")


@asynccontextmanager
async def http_session(
    server: HttpServer, *, bearer: str, workspace: str | None = WORKSPACE
) -> AsyncIterator[ClientSession]:
    """A session over Streamable HTTP, sending the headers a host sends."""
    headers = {"Authorization": f"Bearer {bearer}"}
    if workspace is not None:
        headers["Comet-Workspace"] = workspace
    with anyio.fail_after(SESSION_TIMEOUT_S):
        async with (
            httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=60)) as client,
            streamable_http_client(server.mcp_url, http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


async def post_initialize(server: HttpServer, *, headers: dict[str, str]) -> httpx.Response:
    """One raw ``initialize``, for asserting what a host sees at the HTTP layer.

    The SDK's client turns a 401 into an exception inside its task group, so
    the status, body and challenge a host keys its refresh on are only
    readable from a plain request.
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "hermetic", "version": "0"},
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            server.mcp_url,
            json=body,
            headers={"Accept": "application/json, text/event-stream", **headers},
        )


def result_text(result: CallToolResult) -> str:
    return "".join(part.text for part in result.content if hasattr(part, "text"))


def result_json(result: CallToolResult) -> dict[str, object]:
    """The JSON object a write answers with, success or error.

    A tool error arrives as text that FastMCP may prefix, so the object is
    read from its first brace.
    """
    text = result_text(result)
    start = text.find("{")
    assert start >= 0, f"no JSON object in the answer: {text[:500]}"
    parsed = json.loads(text[start:])
    assert isinstance(parsed, dict), f"answer is not an object: {text[:500]}"
    return parsed


def issues(envelope: dict[str, object]) -> list[dict[str, object]]:
    """The ``issues`` of a ``validation_failed`` envelope."""
    raw = envelope.get("issues")
    assert isinstance(raw, list), f"no issues list in {envelope}"
    assert all(isinstance(issue, dict) for issue in raw), raw
    return raw


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port
