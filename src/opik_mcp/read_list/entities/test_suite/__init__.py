"""``test_suite`` and its items — a dataset of cases, and the cases.

Two entities in one namespace because the second exists only under the first.
opik-backend addresses items as ``/datasets/{id}/items``, so an item has no
read of its own and is only ever listed under its suite.

The item list answers two different questions. Without ``experiment_ids`` it
is the suite's cases. With them it is the same cases with each experiment's
run attached — the comparison, which needs several backend calls and columns
computed from the runs, so it answers the whole call through ``run_fn``
rather than through the shared collection path.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.entities.test_suite.compare import run_compare
from opik_mcp.read_list.entities.test_suite.items import list_items, project_items
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.unsupported import unsupported_fetch

__all__ = ["HANDLER", "ITEM_HANDLER", "list_items", "project_items"]


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_test_suite(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_test_suites(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_test_suites(**kw)


HANDLER = EntityHandler(
    entity_type="test_suite",
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("created_at",),
    description=(
        "Opik 2.0 test suite (evaluation dataset). REST path is /datasets/{id} — "
        "test_suite is the conceptual name for the same backing entity."
    ),
)


ITEM_HANDLER = EntityHandler(
    entity_type="test_suite_item",
    fetch_fn=unsupported_fetch,
    list_fn=list_items,
    list_projection_fn=project_items,
    list_required_kwargs=("test_suite_id",),
    list_has_name=False,
    id_only=True,
    run_fn=run_compare,
    run_verb="compare",
    run_timeout_hint=(
        "Opik did not answer in time for list('test_suite_item', experiment_ids=…). "
        "Retry with a smaller page (size=…), or fewer experiments."
    ),
    # Comparison is a different question with a different answer shape, so it
    # takes the whole call. ``filters`` and ``sort`` are in the list because
    # they are only meaningful with runs attached: the runner is where that
    # refusal can be written, and the collection path stays free of it.
    run_when_kwargs=("experiment_ids", "filters", "sort"),
    description=(
        "Test suite item. List-only — pass test_suite_id to enumerate; there is no read "
        "of an item, and read('test_suite') returns the suite record without its items. "
        "Columns are the items' data keys, discovered from each page. With experiment_ids "
        "the same list compares those experiments case by case."
    ),
)
