"""End-to-end recovery test — spec §7.4 'Real-world recovery test'.

Proves the `validation_failed` envelope is *shaped to be recoverable*. The
contract is: when the model receives a `validation_failed` body, it can
copy the embedded `example` (filling in IDs it knows from context), call
`write` again, and that retry succeeds.

We simulate the loop hermetically — no live LLM. The 'transcript' is a
pair of recorded calls; the test asserts:

1. The first (wrong) call produces a `validation_failed` body that contains
   both `expected_schema` and `example`.
2. The model's 'recovery' (mechanically derived from the embedded example
   plus the IDs the model would have from prior context) clears Stage 2
   and reaches the BE.

If the envelope shape ever changes such that the example can't be used as
the basis for a retry, this test fails. That's the alarm bell.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session

from opik_mcp.opik_client import OpikClient
from opik_mcp.server import mcp

OPIK_BASE = "https://opik.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the write tool to use a respx-mockable client.

    The server normally builds the client from `get_settings()`; in tests
    we want a fixed base URL the respx router can match against. We override
    when the caller passed `client=None` (the server's default).
    """
    test_client = OpikClient(base_url=OPIK_BASE, api_key="k", workspace="ws")

    from opik_mcp.writes import dispatch as _dispatch_mod

    _orig = _dispatch_mod.run_write

    async def _patched_run_write(**kw: Any) -> Any:
        if kw.get("client") is None:
            kw["client"] = test_client
        return await _orig(**kw)

    monkeypatch.setattr("opik_mcp.writes.write_tool._dispatch", _patched_run_write)


@pytest.mark.anyio
async def test_validation_error_carries_recoverable_example(patched_client: None) -> None:
    """The end-to-end recovery loop: bad call → error body → corrected retry."""
    # IDs the model would already have from prior context — these stand in
    # for the "trace_id" the LLM mentioned earlier in its session.
    trace_id_from_context = "00000000-0000-0000-0000-00000000abcd"

    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()

        # --- Turn 1: model omits the required `trace_id`. -------------- #
        bad = await session.call_tool(
            "write",
            {
                "operation": "span.create",
                "data": {"name": "openai.chat", "start_time": "2026-05-18T12:00:00Z"},
            },
        )
        assert bad.isError, "missing trace_id MUST produce isError=true"
        body = _decode_error_text(bad.content[0].text)  # type: ignore[union-attr]
        assert body["error"] == "validation_failed"
        assert body["operation"] == "span.create"
        # The two recovery handles MUST be present — the model uses these
        # to mechanically construct the retry.
        assert "expected_schema" in body, "no expected_schema → no recovery"
        assert "example" in body, "no example → no recovery"
        assert "trace_id" in {i["field"] for i in body["issues"]}

        # --- Turn 2: the 'model' patches the example with a real ID. -- #
        corrected = dict(body["example"])
        corrected["trace_id"] = trace_id_from_context

        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.post("/v1/private/spans").mock(
                return_value=httpx.Response(201, json={"id": "span-1"})
            )
            good = await session.call_tool("write", {"operation": "span.create", "data": corrected})

        assert not good.isError, f"corrected call failed; expected success. result={good!r}"
        assert route.called, "BE was never hit — corrected call didn't reach Stage 4"
        # And the BE body actually carries the context-injected trace_id —
        # not the placeholder from the example.
        sent = json.loads(route.calls.last.request.read())
        assert sent["trace_id"] == trace_id_from_context, (
            f"recovery substituted nothing — BE saw {sent.get('trace_id')!r}"
        )


@pytest.mark.anyio
async def test_score_thread_error_proposes_array_form_for_retry(
    patched_client: None,
) -> None:
    """A targeted recovery case — `score.create` with target=thread in singleton
    form returns the array-shaped example. The model's retry uses the array
    and clears Stage 2.

    Verifies the spec's rule that the *example* in a validation error is the
    corrected form, not a regurgitation of the bad input.
    """
    thread_id = "00000000-0000-0000-0000-00000000beef"

    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()

        bad = await session.call_tool(
            "write",
            {
                "operation": "score.create",
                "data": {
                    "target": "thread",
                    "target_id": thread_id,
                    "name": "helpfulness",
                    "value": 0.5,
                },
            },
        )
        assert bad.isError
        body = _decode_error_text(bad.content[0].text)  # type: ignore[union-attr]
        assert body["error"] == "validation_failed"
        assert isinstance(body["example"], list), (
            "thread error MUST embed the array-shaped example — single-form is invalid"
        )
        assert any("thread_requires_batch" in i.get("code", "") for i in body["issues"])

        # The model copies the array example — fills in its real thread_id +
        # name/value — and the retry succeeds.
        corrected = []
        for item in body["example"]:
            patched = dict(item)
            patched["target_id"] = thread_id
            corrected.append(patched)

        with respx.mock(base_url=OPIK_BASE) as mock:
            route = mock.put("/v1/private/traces/threads/feedback-scores").mock(
                return_value=httpx.Response(204)
            )
            good = await session.call_tool(
                "write", {"operation": "score.create", "data": corrected}
            )

        assert not good.isError
        assert route.called
        sent = json.loads(route.calls.last.request.read())
        assert sent["scores"][0]["thread_id"] == thread_id


# --- helpers ------------------------------------------------------------ #


def _decode_error_text(text: str) -> dict[str, Any]:
    """Strip FastMCP's `Error executing tool <name>: ` prefix; JSON-decode the rest."""
    marker = ": "
    if text.startswith("Error executing tool ") and marker in text:
        body: dict[str, Any] = json.loads(text.split(marker, 1)[1])
    else:
        body = json.loads(text)
    return body
