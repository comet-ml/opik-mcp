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
from typing import Any, Final
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from opik_mcp.config import Settings, get_settings
from opik_mcp.opik_client import OpikClient, make_opik_client
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

logger = logging.getLogger("opik_mcp.writes.dispatch")


# Map ``target`` discriminator → URL path segment used by the Opik BE for
# score/comment endpoints. Centralizing the table prevents drift between
# Stage 2 and Stage 4.
_TARGET_PATH: Final[dict[str, str]] = {
    "trace": "traces",
    "span": "spans",
    "thread": "traces/threads",
}


# --- public API ---------------------------------------------------------- #


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
    """Execute a write. Returns the success envelope; raises ``WriteError`` on failure."""
    op = _stage1_lookup(operation)
    items, is_batch = _stage2_validate(op, data)
    _stage3_authorize(op, scopes)

    effective_idem = _resolve_idempotency_key(idempotency_key, items)

    method, path, body = _build_request_with_method(op, items, is_batch=is_batch)

    if dry_run:
        return {
            "dry_run": True,
            "would_call": {
                "method": method,
                "path": path,
                "body_size": len(json.dumps(body)),
                "batch": is_batch,
                "item_count": len(items),
                # Echoing the body lets the caller verify wire translations
                # (e.g. ``test_suite_*`` → ``dataset_*``, items wrapped in
                # ``{source, data: …}``) before committing the live call.
                "body": body,
            },
        }

    http_client = client if client is not None else make_opik_client(settings or get_settings())
    resp = await http_client.write_json(method, path, body, idempotency_key=effective_idem)
    return _stage4_finalize(op, resp, items, is_batch=is_batch, method=method, path=path)


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
    if not isinstance(data, (dict, list)):
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
        # Score-create has the extra rule that a batch must be homogeneous
        # by ``target`` — separate BE routes per target make heterogeneous
        # batches structurally impossible to honor in one HTTP call.
        if op.name == "score.create":
            _enforce_homogeneous_target(op, items, schema=schema, example=example)
        return items, True

    # Single-object branch.
    try:
        model = op.pydantic_model.model_validate(data)
    except ValidationError as ve:
        issues = _convert_pydantic_errors(ve)
        raise ValidationFailedError.build(
            op.name, issues, expected_schema=schema, example=example
        ) from ve

    # ``score.create`` with target=thread has no singleton BE route; the spec
    # mandates an early validation failure pointing the model to the array
    # form so recovery happens in one extra turn. The example is rebuilt
    # from the caller's own validated fields (not the registry placeholder)
    # so the recovery payload carries the actual thread id and score — one
    # extra turn truly recovers without further substitution.
    if op.name == "score.create" and getattr(model, "target", None) == "thread":
        from opik_mcp.writes.models import ScoreCreate

        assert isinstance(model, ScoreCreate)
        thread_example: dict[str, Any] = {
            "target": "thread",
            "target_id": str(model.target_id),
            "name": model.name,
            "value": model.value,
        }
        if model.reason is not None:
            thread_example["reason"] = model.reason
        if model.project_name is not None:
            thread_example["project_name"] = model.project_name
        if model.category_name is not None:
            thread_example["category_name"] = model.category_name
        raise ValidationFailedError.build(
            op.name,
            [
                ValidationIssue(
                    "target",
                    "target='thread' requires the array form — wrap this object in a list.",
                    "thread_requires_batch",
                )
            ],
            expected_schema=schema,
            example=[thread_example],
        )

    # ``experiment_item.create`` has no singleton route either; spec §3.2
    # — the envelope ``{experiment_items: [...]}`` is the only valid shape,
    # which the model already enforces. The bare-object case is caught by
    # Pydantic above with a clear ``experiment_items`` missing-field error.
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


def _enforce_homogeneous_target(
    op: WriteOperation,
    items: list[BaseModel],
    *,
    schema: dict[str, Any],
    example: dict[str, Any],
) -> None:
    targets = {getattr(it, "target", None) for it in items}
    if len(targets) > 1:
        raise ValidationFailedError.build(
            op.name,
            [
                ValidationIssue(
                    "target",
                    (
                        f"score.create batches must use a single target; got "
                        f"{sorted(str(t) for t in targets)}. "
                        "Split into one batch per target."
                    ),
                    "heterogeneous_targets",
                )
            ],
            expected_schema=schema,
            example=[example],
        )


# --- Stage 3 ------------------------------------------------------------- #


def _stage3_authorize(op: WriteOperation, scopes: frozenset[str]) -> None:
    if op.oauth_scope not in scopes:
        raise AuthorizationDeniedError.build(op.name, op.oauth_scope)


# --- Stage 4 helpers ----------------------------------------------------- #


def _build_request_with_method(
    op: WriteOperation, items: list[BaseModel], *, is_batch: bool
) -> tuple[str, str, dict[str, Any] | list[Any]]:
    """Wrap ``_build_request`` and apply per-batch method overrides.

    The singleton ``trace.update`` route is PATCH ``/v1/private/traces/{id}``,
    but ``/v1/private/traces/batch`` is a POST-only upsert that handles
    create + update by matching on ``id``. Sending PATCH there returns 405.
    We coerce PATCH → POST in the batch branch so callers don't have to
    know about the per-route method asymmetry. Score's PUT batch endpoint
    is left alone (PUT is the correct method for the feedback-scores route).
    """
    path, body = _build_request(op, items, is_batch=is_batch)
    method = op.method
    if is_batch and method == "PATCH" and op.batch_endpoint is not None:
        method = "POST"
    return method, path, body


def _build_request(
    op: WriteOperation, items: list[BaseModel], *, is_batch: bool
) -> tuple[str, dict[str, Any] | list[Any]]:
    """Translate validated models into ``(path, body)`` for the BE.

    Per-operation body shapes are encoded in dedicated branches rather than
    a generic dump because the BE's batch envelopes ("traces", "spans",
    "scores", "experiment_items") and path-encoded routes are operation-
    specific and trying to abstract them produces brittle indirection.
    """
    name = op.name

    if name == "trace.create":
        if is_batch:
            return op.batch_endpoint or op.endpoint, {"traces": [_dump(m) for m in items]}
        return op.endpoint, _dump(items[0])

    if name == "trace.update":
        if is_batch:
            return op.batch_endpoint or op.endpoint, {"traces": [_dump(m) for m in items]}
        single = _dump(items[0])
        trace_id = single.pop("id")
        path = op.endpoint.format(id=trace_id)
        return path, single

    if name == "span.create":
        if is_batch:
            return op.batch_endpoint or op.endpoint, {"spans": [_dump(m) for m in items]}
        return op.endpoint, _dump(items[0])

    if name == "score.create":
        from opik_mcp.writes.models import ScoreCreate

        # All items share a target (enforced in Stage 2) so we read from item 0.
        first = items[0]
        assert isinstance(first, ScoreCreate)
        target = first.target
        target_path = _TARGET_PATH[target]
        if is_batch:
            scores = [_score_batch_item(_dump(m), target) for m in items]
            return f"/v1/private/{target_path}/feedback-scores", {"scores": scores}
        single = _dump(first)
        target_id = single.pop("target_id")
        single.pop("target", None)
        # ``project_name`` is only consulted by the thread route; the spec
        # leaves the single-trace/span routes free to ignore it but we
        # strip it here to keep the body minimal.
        if target != "thread":
            single.pop("project_name", None)
        return f"/v1/private/{target_path}/{target_id}/feedback-scores", single

    if name == "comment.create":
        single = _dump(items[0])
        target = single.pop("target")
        target_id = single.pop("target_id")
        target_path = _TARGET_PATH[target]
        return f"/v1/private/{target_path}/{target_id}/comments", {"text": single["text"]}

    if name == "prompt_version.save":
        d = _dump(items[0])
        version_keys = ("template", "commit", "tags", "metadata")
        version = {k: d[k] for k in version_keys if k in d and d[k] is not None}
        body: dict[str, Any] = {"name": d["name"], "version": version}
        if d.get("change_description") is not None:
            body["change_description"] = d["change_description"]
        return op.endpoint, body

    if name == "test_suite.create":
        # Opik 2.0: the BE accepts the same /v1/private/datasets endpoint
        # for both classic datasets and evaluation suites; the discriminator
        # is the ``type`` field. We always create the evaluation_suite
        # variant — the classic ``dataset`` flavor is not exposed via MCP.
        body = _dump(items[0])
        body["type"] = "evaluation_suite"
        return op.endpoint, body

    if name == "test_suite_item.upsert":
        # Single-envelope shape (supports_batch=False enforces this in
        # Stage 2). Translate MCP-facing test_suite_* fields to the wire's
        # dataset_* fields the BE expects, and re-shape each item into the
        # BE's {source, data: {input, expected_output, metadata}} envelope.
        body = _dump(items[0])
        _rename_test_suite_to_dataset(body)
        for item in body.get("items", []):
            if not isinstance(item, dict):
                continue
            inner: dict[str, Any] = item.pop("data", None) or {}
            for k in ("input", "expected_output", "metadata"):
                if k in item:
                    inner.setdefault(k, item.pop(k))
            if inner:
                item["data"] = inner
            item.setdefault("source", "sdk")
        return op.endpoint, body

    if name == "experiment.create":
        body = _dump(items[0])
        _rename_test_suite_to_dataset(body)
        return op.endpoint, body

    if name == "experiment_item.create":
        # Always envelope. Each item's MCP-facing ``test_suite_item_id``
        # translates to the BE's ``dataset_item_id`` field.
        body = _dump(items[0])
        for item in body.get("experiment_items", []):
            if isinstance(item, dict) and "test_suite_item_id" in item:
                item["dataset_item_id"] = item.pop("test_suite_item_id")
        return op.endpoint, body

    # Belt-and-braces — every registry entry is matched above. If a new
    # operation lands without a branch here, fail loudly.
    raise RuntimeError(f"dispatch: unhandled operation {name!r}")  # pragma: no cover


def _dump(model: BaseModel) -> dict[str, Any]:
    """Strip ``None`` from the model dump using JSON-mode serialization.

    ``mode='json'`` is essential here: it serializes ``datetime`` as ISO-8601
    with the ``T`` separator (and offset suffix) which the Opik Java BE
    requires — Pydantic's default Python-mode dump keeps ``datetime`` objects
    and lets ``json.dumps(default=str)`` stringify them with a space (e.g.
    ``"2026-05-18 18:00:00+00:00"``), which the BE rejects as
    ``DateTimeParseException``.
    """
    dumped: dict[str, Any] = _stringify_uuids(
        model.model_dump(exclude_none=True, mode="json")
    )
    return dumped


def _rename_test_suite_to_dataset(body: dict[str, Any]) -> None:
    """Translate MCP-facing ``test_suite_{name,id}`` to the BE's ``dataset_{name,id}``.

    Opik 2.0 renamed the entity in the FE / public surface, but the BE
    request body fields kept the legacy ``dataset_*`` names for back-compat
    (see /v1/private/datasets/items, /v1/private/experiments). Centralising
    the rename here prevents wire-shape drift between operations.
    """
    if "test_suite_name" in body:
        body["dataset_name"] = body.pop("test_suite_name")
    if "test_suite_id" in body:
        body["dataset_id"] = body.pop("test_suite_id")


def _stringify_uuids(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _stringify_uuids(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_uuids(v) for v in obj]
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def _score_batch_item(item: dict[str, Any], target: str) -> dict[str, Any]:
    """Reshape a per-target batch item into the BE's expected per-target body.

    The trace/span batch endpoints take ``id`` (the trace/span id); the
    thread batch endpoint takes ``thread_id``. The discriminator
    ``target`` is dropped from the wire body — it's encoded in the path
    segment instead.
    """
    target_id = item.pop("target_id")
    item.pop("target", None)
    if target == "thread":
        item["thread_id"] = target_id
    else:
        item["id"] = target_id
        item.pop("project_name", None)
    return item


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
        raise BackendError.build(op.name, status, _safe_body(resp), method=method, path=path)
    body = _safe_body(resp)
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


def _safe_body(resp: httpx.Response) -> Any:
    """Best-effort JSON decode; fall back to text. Empty body → None."""
    try:
        text = resp.text
    except Exception:  # pragma: no cover — httpx text access is in-memory
        return None
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text[:2000]


# --- idempotency-key resolution ------------------------------------------ #


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
