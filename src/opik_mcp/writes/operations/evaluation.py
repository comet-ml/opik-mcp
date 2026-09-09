"""Prompts, test suites and experiments.

What these have in common is a name the wire does not use. Opik 2.0 renamed
the dataset to a test suite everywhere a user can see, and the backend kept
``dataset_*`` in its request bodies for back-compat, so every operation here
translates on the way out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from opik_mcp.writes.wire import (
    BuildContext,
    WireRequest,
    dump,
    rename_test_suite_to_dataset,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


def build_prompt_version_save(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    d = dump(items[0])
    version_keys = ("template", "commit", "tags", "metadata")
    version = {k: d[k] for k in version_keys if k in d and d[k] is not None}
    body: dict[str, Any] = {"name": d["name"], "version": version}
    if d.get("change_description") is not None:
        body["change_description"] = d["change_description"]
    return WireRequest(op.endpoint, body)


def build_test_suite_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Opik 2.0: the BE accepts the same /v1/private/datasets endpoint for both
    classic datasets and evaluation suites; the discriminator is the ``type``
    field. We always create the evaluation_suite variant — the classic
    ``dataset`` flavor is not exposed via MCP."""
    body = dump(items[0])
    body["type"] = "evaluation_suite"
    return WireRequest(op.endpoint, body)


def build_test_suite_item_upsert(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Single-envelope shape (``supports_batch=False`` enforces this in Stage
    2). Translate MCP-facing test_suite_* fields to the wire's dataset_*
    fields, and re-shape each item into the BE's
    ``{source, data: {input, expected_output, metadata}}`` envelope."""
    body = dump(items[0])
    rename_test_suite_to_dataset(body)
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
    return WireRequest(op.endpoint, body)


def build_experiment_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    body = dump(items[0])
    rename_test_suite_to_dataset(body)
    return WireRequest(op.endpoint, body)


def build_experiment_item_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Always an envelope. Each item's MCP-facing ``test_suite_item_id``
    translates to the BE's ``dataset_item_id`` field.

    There is no singleton route (spec §3.2): ``{experiment_items: [...]}`` is
    the only valid shape, which the model itself enforces, so a bare object
    never reaches here — Pydantic rejects it with a missing-field error on
    ``experiment_items``.
    """
    body = dump(items[0])
    for item in body.get("experiment_items", []):
        if isinstance(item, dict) and "test_suite_item_id" in item:
            item["dataset_item_id"] = item.pop("test_suite_item_id")
    return WireRequest(op.endpoint, body)


__all__ = [
    "build_experiment_create",
    "build_experiment_item_create",
    "build_prompt_version_save",
    "build_test_suite_create",
    "build_test_suite_item_upsert",
]
