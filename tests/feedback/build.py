"""A server with ``send_feedback`` on, and a stand-in for the analytics endpoint."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from opik_mcp.analytics.client import AnalyticsClient
from opik_mcp.analytics.events import EVENT_FEEDBACK_SENT
from opik_mcp.server.app.instance import build_server
from tests.factories import make_settings

API_KEY = "opik-test-key-Zx81Lq0Pw4Rt"


class FakeEndpoint:
    """Records each event the analytics worker posts. Reading drains the queue first.

    Clear ``is_open`` to hold each post until it is set again (up to five seconds).
    """

    def __init__(self) -> None:
        self._received: list[dict[str, object]] = []
        self.is_open = threading.Event()
        self.is_open.set()
        self.drain: Callable[[], None] = lambda: None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.is_open.wait(timeout=5)
        self._received.append(json.loads(request.content))
        return httpx.Response(201)

    @property
    def events(self) -> list[dict[str, object]]:
        self.drain()
        return list(self._received)

    def feedback_properties(self) -> list[dict[str, str]]:
        return [
            properties
            for event in self.events
            if event["event_type"] == EVENT_FEEDBACK_SENT
            and isinstance(properties := event["event_properties"], dict)
        ]


@contextmanager
def fake_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEndpoint]:
    """Only the feedback path sends here; the rest of analytics stays off for the test.

    The server's own settings carry the key, as a real one does, so the tool knows
    it as a secret. The client's settings carry none, so building an event never
    starts an account lookup over the network."""
    endpoint = FakeEndpoint()
    client = AnalyticsClient(
        make_settings(opik_mcp_analytics_enabled=True, opik_api_key=None),
        http_client=httpx.Client(transport=httpx.MockTransport(endpoint.handle)),
    )
    endpoint.drain = lambda: client.flush(deadline_s=5)
    monkeypatch.setattr("opik_mcp.feedback.report.get_analytics", lambda: client)
    monkeypatch.setenv("OPIK_API_KEY", API_KEY)
    try:
        yield endpoint
    finally:
        client.close()


def build_feedback_server(**overrides: object) -> FastMCP[object]:
    settings = {"opik_mcp_analytics_enabled": True, "opik_api_key": None, **overrides}
    return build_server(make_settings(**settings))
