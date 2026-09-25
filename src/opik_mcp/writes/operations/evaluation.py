"""Prompts, datasets and experiments.

What these have in common is a payload the wire does not take verbatim: a
prompt version nests under ``version``, a dataset's ``type`` is a backend
enum whose test-suite value is spelled differently, and a dataset item is
re-shaped into the backend's ``{source, data}`` envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from opik_mcp.writes.wire import BuildContext, WireRequest, dump

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation


#: MCP ``dataset.create`` type → the backend's ``DatasetType`` value. The two
#: names agree except for the test suite, whose DB value is still the older
#: ``evaluation_suite`` (opik-backend's DatasetType carries a TODO, OPIK-5795,
#: to migrate it to ``test_suite``); when it moves, only this table changes.
DATASET_TYPE_TO_WIRE: Final[dict[str, str]] = {
    "dataset": "dataset",
    "test_suite": "evaluation_suite",
}


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


def build_dataset_create(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """One endpoint, two flavors: /v1/private/datasets creates a plain dataset
    or a test suite, and ``type`` is the discriminator. Ours is spelled
    ``test_suite``; the backend's DatasetType still calls that value
    ``evaluation_suite``, so it is translated here. The type is always sent —
    before this, the operation named ``test_suite.create`` hard-coded
    ``evaluation_suite``, which left no way to create a plain dataset at all.
    """
    body = dump(items[0])
    body["type"] = DATASET_TYPE_TO_WIRE[body.get("type", "dataset")]
    return WireRequest(op.endpoint, body)


def build_dataset_item_upsert(
    op: WriteOperation, items: list[BaseModel], ctx: BuildContext
) -> WireRequest:
    """Single-envelope shape (``supports_batch=False`` enforces this in Stage
    2). Re-shape each item into the BE's
    ``{source, data: {input, expected_output, metadata}}`` envelope."""
    body = dump(items[0])
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


__all__ = [
    "build_dataset_create",
    "build_dataset_item_upsert",
    "build_prompt_version_save",
]
