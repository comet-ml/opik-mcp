"""Validation + dispatch pipeline for the universal ``write`` tool (spec §4).

Stages, fail-fast:

1. Registry lookup — ``operation`` must be a known enum value.
2. Shape validation — ``data`` parsed against the operation's Pydantic
   model. Arrays are validated element-by-element; the first failing
   index produces an error with the failing index, path, and the
   operation's expected schema.
3. OAuth scope check — token's scopes must include the operation's
   required scope.
4. BE dispatch — single vs. batch endpoint chosen from ``data`` shape,
   path-template filled from ``data`` for path-encoded operations.

``dry_run=True`` runs stages 1-3 and returns ``{dry_run, would_call}``
without touching the backend.

All non-success outcomes raise a ``WriteError`` subclass; the tool layer
converts those into MCP-friendly error envelopes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import (
    OpikClient,
    make_opik_client,
    note_backend_401,
)
from opik_mcp.writes.errors import (
    AuthorizationDeniedError,
    BackendError,
    BatchTooLargeError,
    UnknownOperationError,
    ValidationFailedError,
    ValidationIssue,
)
from opik_mcp.writes.registry import (
    BATCH_LIMIT,
    WRITE_OPERATIONS,
    WRITE_REGISTRY,
    WriteOperation,
)
from opik_mcp.writes.scopes import ALL_WRITE_SCOPES
from opik_mcp.writes.wire import BuildContext, WireRequest, dump, safe_body

logger = logging.getLogger("opik_mcp.writes.dispatch")


# Map ``target`` discriminator → URL path segment used by the Opik BE for
# score/comment endpoints. Centralizing the table prevents drift between
# Stage 2 and Stage 4.
async def run_write(
    *,
    operation: str,
    data: Any,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    scopes: frozenset[str] = ALL_WRITE_SCOPES,
    client: OpikClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute a write. Returns the success envelope; raises ``WriteError`` on failure.

    Five stages, the same for every operation: look it up, validate the
    payload, check the scope, send the request, finalize the response.
    Everything an operation does differently is a hook on its registry entry,
    so nothing here knows what any particular operation is for.
    """
    op = _stage1_lookup(operation)
    items, is_batch = _stage2_validate(op, data)
    _stage3_authorize(op, scopes)

    effective_idem = _resolve_idempotency_key(idempotency_key, items)

    # Live path only: build the client and let the operation resolve whatever
    # the wire needs but the caller does not carry. dry_run stays pure — no
    # client, no backend calls — so it never depends on config or network.
    http_client: OpikClient | None = None
    resolved_settings = settings or get_settings()
    prepared: str | None = None
    if not dry_run:
        http_client = client if client is not None else make_opik_client(resolved_settings)
        if op.prepare_fn is not None:
            prepared = await op.prepare_fn(op, items, http_client)

    ctx = BuildContext(is_batch=is_batch, prepared=prepared, dry_run=dry_run)
    request = _build(op, items, ctx)
    # The registry's method, unless the operation's builder overrode it.
    method = request.method or op.method

    if dry_run:
        would_call: dict[str, Any] = {
            "method": method,
            "path": request.path,
            "body_size": len(json.dumps(request.body)),
            "batch": is_batch,
            "item_count": len(items),
            # Echoing the body lets the caller verify whatever the
            # operation's builder translated before committing the live call.
            "body": request.body,
        }
        if op.dry_run_note_fn is not None:
            note = op.dry_run_note_fn(op, items, prepared)
            if note is not None:
                would_call["note"] = note
        return {"dry_run": True, "would_call": would_call}

    assert http_client is not None  # set above whenever not dry_run
    resp = await http_client.write_json(
        method, request.path, request.body, idempotency_key=effective_idem
    )
    if op.retry_fn is not None:
        request, resp = await op.retry_fn(op, http_client, request, resp)
        method = request.method or method
    out = _stage4_finalize(op, resp, items, is_batch=is_batch, method=method, path=request.path)
    if op.decorate_fn is not None:
        op.decorate_fn(op, items, out, resolved_settings, prepared)
    return out


def _build(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    """The operation's request, or the plain default.

    The default covers the operations whose body is simply the payload: a
    fixed endpoint and an ``exclude_none`` dump of the one item. Everything
    with a batch envelope, a path id or a translated field name carries a
    ``build_fn``.
    """
    if op.build_fn is not None:
        return op.build_fn(op, items, ctx)
    return WireRequest(op.endpoint, dump(items[0]))


# --- Stage 1 ------------------------------------------------------------- #


def _stage1_lookup(operation: str) -> WriteOperation:
    op = WRITE_REGISTRY.get(operation)
    if op is None:
        raise UnknownOperationError.build(operation, WRITE_OPERATIONS)
    return op


# --- Stage 2 ------------------------------------------------------------- #


def _stage2_validate(op: WriteOperation, data: Any) -> tuple[list[BaseModel], bool]:
    """Validate ``data`` against the operation's Pydantic model.

    Returns ``(validated_models, is_batch)``. Arrays past
    ``BATCH_LIMIT`` raise ``BatchTooLargeError`` before any model parse.
    """
    schema = op.pydantic_model.model_json_schema()
    example = op.example
    if not isinstance(data, dict | list):
        raise ValidationFailedError.build(
            op.name,
            [ValidationIssue("", "data must be an object or array.", "type_mismatch")],
            expected_schema=schema,
            example=example,
        )

    if isinstance(data, list):
        if not op.supports_batch:
            raise ValidationFailedError.build(
                op.name,
                [
                    ValidationIssue(
                        "",
                        (
                            f"operation {op.name!r} does not support batch input — "
                            "pass a single object."
                        ),
                        "batch_unsupported",
                    )
                ],
                expected_schema=schema,
                example=example,
            )
        if len(data) > BATCH_LIMIT:
            raise BatchTooLargeError.build(op.name, len(data), BATCH_LIMIT)
        if len(data) == 0:
            raise ValidationFailedError.build(
                op.name,
                [ValidationIssue("", "batch must contain at least one item.", "empty_batch")],
                expected_schema=schema,
                example=[example],
            )
        items: list[BaseModel] = []
        for idx, raw in enumerate(data):
            try:
                items.append(op.pydantic_model.model_validate(raw))
            except ValidationError as ve:
                issues = _convert_pydantic_errors(ve, index_prefix=f"[{idx}]")
                raise ValidationFailedError.build(
                    op.name, issues, expected_schema=schema, example=example
                ) from ve
        if op.validate_fn is not None:
            op.validate_fn(op, items, is_batch=True, schema=schema, example=example)
        return items, True

    # Single-object branch.
    try:
        model = op.pydantic_model.model_validate(data)
    except ValidationError as ve:
        issues = _convert_pydantic_errors(ve)
        raise ValidationFailedError.build(
            op.name, issues, expected_schema=schema, example=example
        ) from ve

    if op.validate_fn is not None:
        op.validate_fn(op, [model], is_batch=False, schema=schema, example=example)

    return [model], False


def _convert_pydantic_errors(
    ve: ValidationError, *, index_prefix: str = ""
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for err in ve.errors():
        loc = ".".join(str(seg) for seg in err.get("loc", ()))
        if index_prefix:
            loc = f"{index_prefix}.{loc}" if loc else index_prefix
        # ``msg`` from a custom ValueError comes through prefixed with
        # ``"Value error, "`` — strip it so the user sees the raw rule name.
        msg = err.get("msg", "")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        # The error type from a custom validator carrying a code-prefixed
        # message ("project_xor: …") doubles as the rule key for downstream
        # programmatic recovery; preserve it when present.
        code = err.get("type", "")
        if msg and ": " in msg and msg.split(": ", 1)[0].replace("_", "").isalnum():
            code = msg.split(": ", 1)[0]
        issues.append(ValidationIssue(field=loc, message=msg, code=code))
    return issues


# --- Stage 3 ------------------------------------------------------------- #


def _stage3_authorize(op: WriteOperation, scopes: frozenset[str]) -> None:
    if op.oauth_scope not in scopes:
        raise AuthorizationDeniedError.build(op.name, op.oauth_scope)


# --- Stage 4 helpers ----------------------------------------------------- #


# --- Stage 4 finalize ---------------------------------------------------- #


def _stage4_finalize(
    op: WriteOperation,
    resp: httpx.Response,
    items: list[BaseModel],
    *,
    is_batch: bool,
    method: str,
    path: str,
) -> dict[str, Any]:
    status = resp.status_code
    if not (200 <= status < 300):
        if status == 401:
            # Drop the cached OAuth validation so the next request re-validates
            # and meets the 401 that triggers the host's refresh (OPIK-8252).
            note_backend_401()
        raise BackendError.build(op.name, status, safe_body(resp), method=method, path=path)
    body = safe_body(resp)
    return {
        "ok": True,
        "operation": op.name,
        "method": method,
        "path": path,
        "status": status,
        "batch": is_batch,
        "item_count": len(items),
        "backend_body": body,
    }


def _resolve_idempotency_key(
    tool_level: str | None,
    items: list[BaseModel],
) -> str | None:
    """Reconcile tool-level ``idempotency_key`` with item-level ``id`` (spec §3.3).

    Tool-level wins on conflict; we emit a single ``WARNING`` log row when
    both are set and differ so it surfaces in production logs without
    failing the call.
    """
    if tool_level is None:
        return None
    for item in items:
        item_id = getattr(item, "id", None)
        if item_id is not None and str(item_id) != tool_level:
            logger.warning(
                "writes.idempotency_conflict tool_level=%s item_id=%s — using tool_level",
                tool_level,
                item_id,
            )
            break
    return tool_level


__all__ = ["run_write"]
