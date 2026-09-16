"""``experiment`` — one evaluation run over a test suite."""

from __future__ import annotations

from typing import Any

from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import name_candidates


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    record = await client.get_experiment(entity_id)
    return _with_next_step(record, entity_id)


def _with_next_step(record: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """Point at the per-case view from the averages.

    A read of an experiment answers "how did this run do" with means. The next
    question is always "on which cases", and the call that answers it needs
    nothing but this id and another run's — not the suite, which it resolves
    itself. An agent that does not know the call falls back to reading every
    trace of both runs, which is what this feature exists to stop.
    """
    if not record.get("dataset_id"):
        return record
    experiment_id = record.get("id") or entity_id
    return {
        **record,
        "comparePerCase": (
            "Which cases differ, rather than these averages: "
            f"list('test_suite_item', experiment_ids=['{experiment_id}', "
            "'<other experiment id>'])"
        ),
    }


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
    no_window_reason="experiments have no time window on the backend.",
    description="Experiment status + summary scores.",
)
