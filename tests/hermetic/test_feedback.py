"""``send_feedback`` in a real stdio server with analytics on, posting to the stub.

The in-process tests replace the analytics client. This one runs the path a
user's server runs: the tool queues the event, and the analytics worker thread
posts it after the answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import anyio
import pytest

from tests.hermetic.servers import API_KEY, result_text, stdio_session
from tests.hermetic.stub_backend import ANALYTICS_PATH, StubBackend

pytestmark = [pytest.mark.hermetic, pytest.mark.anyio]

#: The worker posts after the answer; this bounds the wait for it.
DELIVERY_TIMEOUT_S = 10


@pytest.fixture
def stub() -> Iterator[StubBackend]:
    backend = StubBackend()
    backend.start()
    try:
        yield backend
    finally:
        backend.stop()


def feedback_events(stub: StubBackend) -> list[dict[str, object]]:
    return [
        request.payload
        for request in stub.sent(ANALYTICS_PATH)
        if request.payload.get("event_type") == "opik_mcp_feedback_sent"
    ]


async def test_a_report_from_a_real_server_reaches_the_endpoint_redacted(
    stub: StubBackend, tmp_path: Path
) -> None:
    env = {
        "OPIK_MCP_ANALYTICS_ENABLED": "true",
        "OPIK_MCP_ANALYTICS_URL": f"http://127.0.0.1:{stub.port}{ANALYTICS_PATH}",
        # The install id is written under HOME; keep it out of the real one.
        "HOME": str(tmp_path),
    }
    # The stub key has no digits, so only the server's own key list removes it.
    description = (
        f"write score.create returned validation_failed when I passed the key {API_KEY} "
        "in a comment. I expected the score to be saved."
    )
    async with stdio_session(stub, **env) as session:
        names = [tool.name for tool in (await session.list_tools()).tools]
        result = await session.call_tool(
            "send_feedback",
            {
                "category": "error",
                "impact": "blocked",
                "tool": "write",
                "target": "score.create",
                "description": description,
            },
        )

        with anyio.fail_after(DELIVERY_TIMEOUT_S):
            while not (events := feedback_events(stub)):
                await anyio.sleep(0.05)

    assert "send_feedback" in names
    assert not result.isError, result_text(result)
    [event] = events
    properties = event["event_properties"]
    assert isinstance(properties, dict)
    assert properties["target"] == "score.create"
    assert properties["feedback_id"] in result_text(result)
    assert API_KEY not in str(properties["description"])
    assert properties["redacted_count"] == "1"
