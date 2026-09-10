"""``experiment`` — one evaluation run over a test suite."""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import name_candidates


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_experiment(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_experiments(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_experiments(**kw)


HANDLER = EntityHandler(
    entity_type="experiment",
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    # feedback_scores is the experiment's per-metric averages, rendered as
    # ``name=value`` pairs so a comparison list reads without a read() per row.
    list_extra_fields=("dataset_name", "created_at", "feedback_scores"),
    description="Experiment status + summary scores.",
)
