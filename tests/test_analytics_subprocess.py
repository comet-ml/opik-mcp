"""Real-subprocess startup_error tests.

These spawn a fresh ``python -m opik_mcp`` process so the entire import chain
runs with the broken env in place. They exist because respx-based tests in
``test_analytics_server_startup.py`` cannot catch bugs where the failure
happens at module-import time — by the time ``monkeypatch.setenv`` runs in
the test process, ``opik_mcp`` is already imported and any import-time
``get_settings()`` calls have already succeeded with whatever env the test
runner started with.

The motivating regression: ``opik_mcp/__init__.py`` used to re-export
``mcp`` from ``opik_mcp.server``, which eagerly called ``get_settings()`` via
``render_instructions()``. A bad ``COMET_WORKSPACE_ID`` raised
``ValidationError`` out of ``import opik_mcp`` itself — before ``main()``
could catch it and emit. The respx test for the fallback path passed
green but the real subprocess silently dropped the event.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class _CaptureServer:
    """Tiny local listener that records POST bodies for assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        # Pass port 0 so the OS assigns a free port AND we bind it atomically.
        # The previous pattern (_free_port → close → HTTPServer rebinds the same
        # port number) had a TOCTOU window where the OS could give that port
        # to a different process before HTTPServer claimed it.
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _build_handler(self.events))
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        # server_address is typed ``tuple[Any, ...]`` in the stubs; we know we
        # bound to ("127.0.0.1", <int>) so cast for the f-string.
        host = str(self._srv.server_address[0])
        port = int(self._srv.server_address[1])
        self.url = f"http://{host}:{port}/notify/event/"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        # 5s is plenty for the serve_forever loop to notice ``shutdown()`` and
        # exit; a hung worker thread is always a bug, not a transient — fail
        # the test rather than leaking a thread that can interfere with the
        # next test's port assignment.
        self._thread.join(timeout=5)
        assert not self._thread.is_alive(), "_CaptureServer worker thread did not exit"


def _build_handler(sink: list[dict[str, Any]]) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                sink.append(json.loads(raw.decode("utf-8")))
            except json.JSONDecodeError:
                sink.append({"_raw": raw.decode("utf-8", errors="replace")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success":true}')

        def log_message(self, fmt: str, *args: object) -> None:  # silence pytest noise
            return

    return _Handler


@pytest.fixture()
def capture() -> Iterator[_CaptureServer]:
    srv = _CaptureServer()
    try:
        yield srv
    finally:
        srv.close()


def _run_opik_mcp(
    extra_env: dict[str, str], timeout: float = 15.0
) -> subprocess.CompletedProcess[bytes]:
    """Run ``python -m opik_mcp`` as a fresh process and return the result.

    The env is built from a clean baseline (no inheriting of the dev's
    OPIK_API_KEY or COMET_WORKSPACE) so failures are deterministic.
    """
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
        # Stamp every event payload so receiver-side filters can drop the
        # test traffic if these somehow leak to a real receiver.
        "OPIK_MCP_ANALYTICS_SOURCE": "opik-mcp-test",
    }
    return subprocess.run(
        [sys.executable, "-m", "opik_mcp"],
        env={**base_env, **extra_env},
        cwd=REPO_ROOT,
        timeout=timeout,
        capture_output=True,
    )


def test_invalid_config_emits_in_real_subprocess(capture: _CaptureServer) -> None:
    """Bad ``COMET_WORKSPACE_ID`` must POST ``opik_mcp_startup_error`` even
    when the ``ValidationError`` originates at module-import time.

    Without the ``__init__.py`` fix this assertion fails — the subprocess
    raises before ``main()`` runs, the fallback emit never fires, and BI
    sees zero signal for the most common install-failure mode.
    """
    result = _run_opik_mcp(
        {
            "OPIK_MCP_ANALYTICS_URL": capture.url,
            "COMET_WORKSPACE_ID": "not-a-uuid",
            "OPIK_API_KEY": "test-key",
            "OPIK_MCP_TRANSPORT": "stdio",
        }
    )

    # Non-zero exit: ValidationError propagated out of main(); that's expected.
    assert result.returncode != 0, "expected ValidationError to terminate the subprocess"

    # The actual contract: at least one POST landed, and it's the startup_error.
    errors = [e for e in capture.events if e.get("event_type") == "opik_mcp_startup_error"]
    assert errors, (
        "fallback client must POST opik_mcp_startup_error before the subprocess unwinds; "
        f"captured events = {capture.events!r}"
    )
    props = errors[0]["event_properties"]
    assert props["phase"] == "config"
    assert props["error_kind"] == "invalid_config"
    assert props["exception_type"] == "ValidationError"
    # ``server_started`` MUST NOT fire — we never reached a usable Settings.
    assert not any(e.get("event_type") == "opik_mcp_server_started" for e in capture.events), (
        "server_started leaked on the config-fail path"
    )


def test_invalid_config_subprocess_omits_pii(capture: _CaptureServer) -> None:
    """Raw bad env value must not leak into the subprocess's POST body."""
    canary = "PII-CANARY-subprocess-not-a-real-uuid-3c7e9d11"
    _run_opik_mcp(
        {
            "OPIK_MCP_ANALYTICS_URL": capture.url,
            "COMET_WORKSPACE_ID": canary,
            "OPIK_API_KEY": "test-key",
            "OPIK_MCP_TRANSPORT": "stdio",
        }
    )
    body = json.dumps(capture.events)
    assert canary not in body, f"PII leak in subprocess POST: {body!r}"


def _ipv6_loopback_supported() -> bool:
    """Probe whether ``::1`` is bindable on this host.

    Required because CI runners frequently disable IPv6 (e.g. GitHub-hosted
    runners depending on image generation), and the preflight test must skip
    cleanly there rather than fail with a misleading bind error.
    """
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s.bind(("::1", 0))
        finally:
            s.close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_loopback_supported(), reason="IPv6 loopback unavailable on host")
def test_preflight_handles_ipv6_loopback_via_getaddrinfo(capture: _CaptureServer) -> None:
    """``OPIK_MCP_HOST=::1`` must NOT trigger a false-positive transport_crash.

    Regression for the bug where ``_preflight_bind_check`` hardcoded
    ``AF_INET``: any user binding to ``::1`` (or to ``localhost`` on a host
    where it resolves to ``::1``, common on macOS 15+) would have got
    ``OSError: Invalid argument`` from the IPv4 socket trying to bind an
    IPv6 address — and we'd have emitted a misleading ``transport_crash``
    for a server that would otherwise have booted fine.

    Test strategy: bind ``::1:<free_port>``, run opik-mcp targeting the
    *same* address. Pre-flight check must surface a real ``OSError`` (port
    in use) — not the spurious "Invalid argument" from the wrong family.
    """
    hold = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    hold.bind(("::1", 0))
    held_port = hold.getsockname()[1]
    hold.listen(1)
    try:
        _run_opik_mcp(
            {
                "OPIK_MCP_ANALYTICS_URL": capture.url,
                "OPIK_API_KEY": "test-key",
                "COMET_WORKSPACE": "test-ws",
                "OPIK_MCP_TRANSPORT": "streamable-http",
                "OPIK_MCP_HOST": "::1",
                "OPIK_MCP_PORT": str(held_port),
                "OPIK_MCP_DEV_TOKEN": "test-token-not-default-1234567890",
            }
        )
    finally:
        hold.close()

    errors = [e for e in capture.events if e.get("event_type") == "opik_mcp_startup_error"]
    assert errors, "preflight must surface real OSError for IPv6 bind, not silently mis-classify"
    props = errors[0]["event_properties"]
    # Must be transport_crash with OSError — NOT some other phase or "unknown".
    # An "Invalid argument" leak would still be OSError, but it would fire
    # even on a free port; the held-port setup proves we caught a real bind.
    assert props["phase"] == "transport_start"
    assert props["error_kind"] == "transport_crash"
    assert props["exception_type"] == "OSError"


def test_port_in_use_emits_transport_crash_in_subprocess(capture: _CaptureServer) -> None:
    """Pre-flight bind check must surface ``OSError`` for an occupied port.

    Without the pre-flight check, uvicorn catches the bind failure
    internally, runs the shutdown lifespan, and returns normally — the
    process exits 0 with no startup_error event. This regression test
    holds a port from the test process and asserts opik-mcp's subprocess
    POSTs ``transport_crash`` before unwinding.
    """
    # Bind to port 0 so the OS picks AND assigns atomically — eliminates the
    # window where _free_port → close → re-bind could lose the port to another
    # process under CI load.
    hold = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    hold.bind(("127.0.0.1", 0))
    held_port = hold.getsockname()[1]
    hold.listen(1)
    try:
        _run_opik_mcp(
            {
                "OPIK_MCP_ANALYTICS_URL": capture.url,
                "OPIK_API_KEY": "test-key",
                "COMET_WORKSPACE": "test-ws",
                "OPIK_MCP_TRANSPORT": "streamable-http",
                "OPIK_MCP_HOST": "127.0.0.1",
                "OPIK_MCP_PORT": str(held_port),
                # Bypass the http_bind_check that would otherwise short-circuit.
                "OPIK_MCP_DEV_TOKEN": "test-token-not-default-1234567890",
            }
        )
    finally:
        hold.close()

    errors = [e for e in capture.events if e.get("event_type") == "opik_mcp_startup_error"]
    assert errors, (
        "transport_crash must fire when uvicorn's port is already bound; "
        f"captured events = {capture.events!r}"
    )
    props = errors[0]["event_properties"]
    assert props["phase"] == "transport_start"
    assert props["error_kind"] == "transport_crash"
    assert props["exception_type"] == "OSError"
    assert props["transport"] == "streamable-http"


def test_opt_out_suppresses_emit_in_subprocess(capture: _CaptureServer) -> None:
    """``OPIK_MCP_ANALYTICS_ENABLED=false`` must hold on the config-fail
    subprocess path — the fallback client honours opt-out even when the
    rest of Settings is unusable.
    """
    _run_opik_mcp(
        {
            "OPIK_MCP_ANALYTICS_URL": capture.url,
            "OPIK_MCP_ANALYTICS_ENABLED": "false",
            "COMET_WORKSPACE_ID": "not-a-uuid",
            "OPIK_API_KEY": "test-key",
            "OPIK_MCP_TRANSPORT": "stdio",
        }
    )
    assert not capture.events, (
        f"opt-out must suppress emit on config-fail subprocess path; got {capture.events!r}"
    )
