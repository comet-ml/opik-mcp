"""``test_suite`` and its items — a dataset of cases, and the cases.

Two entities in one namespace because the second exists only under the first.
opik-backend addresses items as ``/datasets/{id}/items``, so an item has no
read of its own and is only ever listed under its suite.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.unsupported import unsupported_fetch


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_test_suite(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_test_suites(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_test_suites(**kw)


async def list_items(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    # opik-backend's items endpoint is ``/datasets/{id}/items`` — the suite id
    # is in the path, not a query param. Pull it out before forwarding.
    suite_id = kw.pop("test_suite_id", None)
    if not suite_id:
        raise ValueError("list test_suite_item requires test_suite_id")
    kw.pop("name", None)
    return await client.list_test_suite_items(suite_id, **kw)


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
    list_extra_fields=("input", "expected_output"),
    list_required_kwargs=("test_suite_id",),
    id_only=True,
    description=(
        "Test suite item. Currently list-only — pass test_suite_id to enumerate. "
        "For full details, the parent test_suite read returns up to 200 items inline."
    ),
)
