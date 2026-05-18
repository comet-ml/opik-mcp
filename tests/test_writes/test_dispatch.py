"""Dispatcher tests with mocked BE (spec §7.2).

These pin the routing, scope, dry-run, and idempotency-key logic. The
BE is a ``respx`` mock so every assertion is on the *exact* request the
dispatcher would send to a real Opik backend.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from opik_mcp.opik_client import OpikClient
from opik_mcp.writes.dispatch import run_write
from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchTooLargeError,
    ValidationFailedError,
)
from opik_mcp.writes.scopes import (
    ALL_WRITE_SCOPES,
    SCOPE_TRACE_SPAN_THREAD_ANNOTATE,
    SCOPE_TRACE_SPAN_THREAD_LOG,
)

OPIK_BASE = "https://opik.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> OpikClient:
    return OpikClient(base_url=OPIK_BASE, api_key="key-abc", workspace="ws")


# --- path-encoded target routing (score.create) ------------------------- #


@pytest.mark.anyio
async def test_score_create_routes_per_target() -> None:
    """target=trace/span/thread each hit their respective BE path.

    Spec §7.2 collapses these into one test because the dispatcher's
    ``_TARGET_PATH`` table is the single source of routing truth — three
    parametric assertions catch any regression that would swap them.
    """
    target_id = "00000000-0000-0000-0000-000000000001"
    with respx.mock(base_url=OPIK_BASE) as mock:
        trace_route = mock.put(f"/v1/private/traces/{target_id}/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        span_route = mock.put(f"/v1/private/spans/{target_id}/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        thread_route = mock.put("/v1/private/traces/threads/feedback-scores").mock(
            return_value=httpx.Response(204)
        )

        await run_write(
            operation="score.create",
            data={"target": "trace", "target_id": target_id, "name": "h", "value": 0.5},
            client=_client(),
        )
        await run_write(
            operation="score.create",
            data={"target": "span", "target_id": target_id, "name": "h", "value": 0.5},
            client=_client(),
        )
        # Thread requires batch form; pass a one-element array.
        await run_write(
            operation="score.create",
            data=[
                {
                    "target": "thread",
                    "target_id": target_id,
                    "name": "h",
                    "value": 0.5,
                }
            ],
            client=_client(),
        )

    assert trace_route.called
    assert span_route.called
    assert thread_route.called
    # Verify thread batch body uses thread_id key (not id).
    body = json.loads(thread_route.calls.last.request.read())
    assert "scores" in body
    assert body["scores"][0]["thread_id"] == target_id


# --- single vs. batch endpoint selection -------------------------------- #


@pytest.mark.anyio
async def test_trace_create_single_vs_batch_endpoint() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        single = mock.post("/v1/private/traces").mock(return_value=httpx.Response(201, json={}))
        batch = mock.post("/v1/private/traces/batch").mock(return_value=httpx.Response(204))

        await run_write(
            operation="trace.create",
            data={"name": "t", "start_time": "2026-05-18T12:00:00Z"},
            client=_client(),
        )
        await run_write(
            operation="trace.create",
            data=[
                {"name": "t1", "start_time": "2026-05-18T12:00:00Z"},
                {"name": "t2", "start_time": "2026-05-18T12:01:00Z"},
            ],
            client=_client(),
        )

    assert single.called and batch.called
    # Batch body wraps in the {traces: [...]} envelope.
    body = json.loads(batch.calls.last.request.read())
    assert "traces" in body and len(body["traces"]) == 2


@pytest.mark.anyio
async def test_span_create_single_vs_batch_endpoint() -> None:
    trace_id = "00000000-0000-0000-0000-000000000001"
    with respx.mock(base_url=OPIK_BASE) as mock:
        single = mock.post("/v1/private/spans").mock(return_value=httpx.Response(201, json={}))
        batch = mock.post("/v1/private/spans/batch").mock(return_value=httpx.Response(204))

        await run_write(
            operation="span.create",
            data={"trace_id": trace_id, "name": "s", "start_time": "2026-05-18T12:00:00Z"},
            client=_client(),
        )
        await run_write(
            operation="span.create",
            data=[
                {"trace_id": trace_id, "name": "s1", "start_time": "2026-05-18T12:00:00Z"},
                {"trace_id": trace_id, "name": "s2", "start_time": "2026-05-18T12:01:00Z"},
            ],
            client=_client(),
        )

    assert single.called and batch.called


# --- batch size enforcement --------------------------------------------- #


@pytest.mark.anyio
async def test_batch_too_large_rejects_before_be() -> None:
    payload = [{"name": f"t{i}", "start_time": "2026-05-18T12:00:00Z"} for i in range(1001)]
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(500))
        with pytest.raises(BatchTooLargeError):
            await run_write(operation="trace.create", data=payload, client=_client())
    assert not route.called, "BE was hit despite batch_too_large"


# --- OAuth scope rejection ---------------------------------------------- #


@pytest.mark.anyio
async def test_missing_scope_rejects_before_be() -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(500))
        with pytest.raises(AuthorizationDeniedError) as exc_info:
            await run_write(
                operation="trace.create",
                data={"name": "t", "start_time": "2026-05-18T12:00:00Z"},
                scopes=frozenset(),
                client=_client(),
            )
    body = json.loads(exc_info.value.to_json())
    assert body["required_scope"] == SCOPE_TRACE_SPAN_THREAD_LOG
    assert not route.called


@pytest.mark.anyio
async def test_partial_scope_advertised_but_rejected_per_op() -> None:
    """Token with TRACE_SPAN_THREAD_LOG only → score.create denied (needs ANNOTATE)."""
    with pytest.raises(AuthorizationDeniedError) as exc_info:
        await run_write(
            operation="score.create",
            data={
                "target": "trace",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "name": "h",
                "value": 0.5,
            },
            scopes=frozenset({SCOPE_TRACE_SPAN_THREAD_LOG}),
            client=_client(),
        )
    body = json.loads(exc_info.value.to_json())
    assert body["required_scope"] == SCOPE_TRACE_SPAN_THREAD_ANNOTATE


# --- dry_run ------------------------------------------------------------ #


@pytest.mark.anyio
async def test_dry_run_returns_would_call_without_be() -> None:
    with respx.mock(base_url=OPIK_BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(500))
        result = await run_write(
            operation="trace.create",
            data={"name": "t", "start_time": "2026-05-18T12:00:00Z"},
            dry_run=True,
            client=_client(),
        )
    assert result["dry_run"] is True
    assert result["would_call"]["method"] == "POST"
    assert result["would_call"]["path"] == "/v1/private/traces"
    assert result["would_call"]["body_size"] > 0
    assert not route.called


@pytest.mark.anyio
async def test_dry_run_does_not_mask_validation_failure() -> None:
    """dry_run must surface validation_failed, NOT a phony would_call body."""
    with pytest.raises(ValidationFailedError):
        await run_write(
            operation="trace.create",
            data={"name": "t"},  # missing start_time
            dry_run=True,
            client=_client(),
        )


# --- idempotency-key passthrough --------------------------------------- #


@pytest.mark.anyio
async def test_idempotency_key_header_forwarded_to_be(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/traces").mock(return_value=httpx.Response(201, json={}))
        with caplog.at_level(logging.WARNING, logger="opik_mcp.writes.dispatch"):
            await run_write(
                operation="trace.create",
                data={
                    "id": "00000000-0000-0000-0000-000000000099",
                    "name": "t",
                    "start_time": "2026-05-18T12:00:00Z",
                },
                idempotency_key="tool-key-overrides",
                client=_client(),
            )
    req = route.calls.last.request
    assert req.headers.get("idempotency-key") == "tool-key-overrides"
    # Conflict warning fired because data.id != idempotency_key.
    assert any("idempotency_conflict" in rec.message for rec in caplog.records)


# --- BE 4xx wrap -------------------------------------------------------- #


@pytest.mark.anyio
async def test_backend_4xx_wraps_with_body_verbatim() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/traces").mock(
            return_value=httpx.Response(400, json={"errors": ["project not found"]})
        )
        with pytest.raises(BackendError) as exc_info:
            await run_write(
                operation="trace.create",
                data={"name": "t", "start_time": "2026-05-18T12:00:00Z"},
                client=_client(),
            )
    body = json.loads(exc_info.value.to_json())
    assert body["error"] == "backend_error"
    assert body["backend_error"]["status"] == 400
    assert body["backend_error"]["body"] == {"errors": ["project not found"]}
    assert body["backend_error"]["method"] == "POST"
    assert body["backend_error"]["path"] == "/v1/private/traces"


@pytest.mark.anyio
async def test_backend_5xx_wraps_with_body() -> None:
    with respx.mock(base_url=OPIK_BASE) as mock:
        mock.post("/v1/private/traces").mock(
            return_value=httpx.Response(503, text="upstream timeout")
        )
        with pytest.raises(BackendError) as exc_info:
            await run_write(
                operation="trace.create",
                data={"name": "t", "start_time": "2026-05-18T12:00:00Z"},
                client=_client(),
            )
    body = json.loads(exc_info.value.to_json())
    assert body["backend_error"]["status"] == 503


# --- path templating ---------------------------------------------------- #


@pytest.mark.anyio
async def test_trace_update_path_templates_id() -> None:
    trace_id = "00000000-0000-0000-0000-0000abcdef99"
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.patch(f"/v1/private/traces/{trace_id}").mock(return_value=httpx.Response(204))
        await run_write(
            operation="trace.update",
            data={"id": trace_id, "end_time": "2026-05-18T13:00:00Z"},
            client=_client(),
        )
    assert route.called
    body = json.loads(route.calls.last.request.read())
    assert "id" not in body, "id must be stripped from PATCH body — it's a path param"


@pytest.mark.anyio
async def test_comment_create_path_templates_target() -> None:
    target_id = "00000000-0000-0000-0000-000000000042"
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post(f"/v1/private/spans/{target_id}/comments").mock(
            return_value=httpx.Response(201)
        )
        await run_write(
            operation="comment.create",
            data={"target": "span", "target_id": target_id, "text": "retry"},
            client=_client(),
        )
    assert route.called
    body = json.loads(route.calls.last.request.read())
    assert body == {"text": "retry"}


# --- trace.update batch coercion ---------------------------------------- #


@pytest.mark.anyio
async def test_trace_update_batch_coerces_patch_to_post() -> None:
    """trace.update singleton is PATCH /traces/{id}; batch must POST /traces/batch.

    The Opik BE batch endpoint is POST-only (upsert by id). Without the
    PATCH→POST coercion in ``_build_request_with_method`` the batch call
    returns 405 — pinned here so a future "use op.method everywhere" refactor
    can't quietly regress.
    """
    trace_id = "00000000-0000-0000-0000-0000abcdef01"
    with respx.mock(base_url=OPIK_BASE) as mock:
        # Only POST is registered — a PATCH would not match and respx would
        # surface the unmatched request, failing the test.
        route = mock.post("/v1/private/traces/batch").mock(return_value=httpx.Response(204))
        await run_write(
            operation="trace.update",
            data=[{"id": trace_id, "end_time": "2026-05-18T13:00:00Z"}],
            client=_client(),
        )
    assert route.called
    body = json.loads(route.calls.last.request.read())
    # ``id`` stays inside each item — the batch endpoint matches on it for upsert.
    assert body["traces"][0]["id"] == trace_id


# --- prompt_version envelope shaping ----------------------------------- #


@pytest.mark.anyio
async def test_prompt_version_save_wraps_in_version_envelope() -> None:
    """The BE expects {name, version: {template, …}, change_description?} — verify."""
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.post("/v1/private/prompts/versions").mock(
            return_value=httpx.Response(201, json={})
        )
        await run_write(
            operation="prompt_version.save",
            data={
                "name": "support_reply",
                "template": "Hi {{name}}",
                "commit": "v3",
                "change_description": "tighten greeting",
            },
            client=_client(),
        )
    body = json.loads(route.calls.last.request.read())
    assert body["name"] == "support_reply"
    assert body["version"]["template"] == "Hi {{name}}"
    assert body["version"]["commit"] == "v3"
    assert body["change_description"] == "tighten greeting"


# --- score.create batch body shape ------------------------------------- #


@pytest.mark.anyio
async def test_score_batch_trace_uses_id_key_not_target_id() -> None:
    target_id = "00000000-0000-0000-0000-000000000001"
    with respx.mock(base_url=OPIK_BASE) as mock:
        route = mock.put("/v1/private/traces/feedback-scores").mock(
            return_value=httpx.Response(204)
        )
        await run_write(
            operation="score.create",
            data=[
                {"target": "trace", "target_id": target_id, "name": "h", "value": 0.5},
            ],
            client=_client(),
        )
    body = json.loads(route.calls.last.request.read())
    assert body["scores"][0]["id"] == target_id
    assert "target" not in body["scores"][0]
    assert "target_id" not in body["scores"][0]


@pytest.mark.anyio
async def test_score_batch_heterogeneous_targets_rejected() -> None:
    """Batches mixing trace + span targets fail Stage 2 — separate BE routes."""
    with pytest.raises(ValidationFailedError) as exc_info:
        await run_write(
            operation="score.create",
            data=[
                {
                    "target": "trace",
                    "target_id": "00000000-0000-0000-0000-000000000001",
                    "name": "h",
                    "value": 0.5,
                },
                {
                    "target": "span",
                    "target_id": "00000000-0000-0000-0000-000000000002",
                    "name": "h",
                    "value": 0.5,
                },
            ],
            client=_client(),
        )
    body = json.loads(exc_info.value.to_json())
    assert any("heterogeneous_targets" in i.get("code", "") for i in body["issues"])


# --- all-scope happy path covers Stage 3 pass-through ----------------- #


@pytest.mark.anyio
async def test_all_scopes_grants_every_operation() -> None:
    """The default ``ALL_WRITE_SCOPES`` set must clear every op's Stage 3.

    Pinned because §5 plays it dangerous — if a future op adds a new scope
    without expanding the set, this fails before users notice.
    """
    from opik_mcp.writes.registry import WRITE_REGISTRY

    for op in WRITE_REGISTRY.values():
        assert op.oauth_scope in ALL_WRITE_SCOPES, op.name
