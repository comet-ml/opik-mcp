"""``dataset`` and its items — a collection of cases, and the cases.

Two entities in one namespace because the second exists only under the first.
opik-backend addresses items as ``/datasets/{id}/items``, so an item has no
read of its own and is only ever listed under its dataset.

The item list answers two different questions. Without ``experiment_ids`` it
is the dataset's cases. With them it is the same cases with each experiment's
run attached — the comparison, which needs several backend calls and columns
computed from the runs, so it answers the whole call through ``run_fn``
rather than through the shared collection path.

The two questions are asked of two endpoints, which filter on two sets of
fields: the case's own keys and provenance here (``dataset_item_case``), the
runs' scores and outputs there (``dataset_item``). Neither endpoint takes the
other's fields, so the vocabulary is declared per call rather than per entity.
"""

from __future__ import annotations

from typing import Any

from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient, OpikReadClient
from opik_mcp.read_list.decorations import link_note_for
from opik_mcp.read_list.entities.dataset.compare import run_compare
from opik_mcp.read_list.entities.dataset.items import fetch_item, list_items, project_items
from opik_mcp.read_list.handler import EntityHandler
from opik_mcp.read_list.paging import name_candidates
from opik_mcp.read_list.ui_links import scoped_entity_links

__all__ = ["HANDLER", "ITEM_HANDLER", "dataset_links", "fetch_item", "list_items", "project_items"]


async def fetch(client: OpikReadClient, entity_id: str) -> dict[str, Any]:
    return await client.get_dataset(entity_id)


async def search_by_name(client: OpikReadClient, name: str) -> list[dict[str, Any]]:
    return name_candidates(await client.list_datasets(name=name, size=5))


async def list_page(client: OpikListClient, **kw: Any) -> dict[str, Any]:
    return await client.list_datasets(**kw)


def dataset_links(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """The dataset's page under its project, or why there is none.

    A dataset may carry a project or may have been created at workspace level
    with none. The first has a page. The second has no page anywhere: v2
    serves no workspace-level route and the backend filters these listings
    strictly on project id, so it appears in no project's list either. That is
    a gap in the product, not a link we can synthesise, and saying so beats a
    reader wondering why this record alone came back bare.
    """
    return scoped_entity_links(settings, data, area="datasets", noun="dataset")


HANDLER = EntityHandler(
    entity_type="dataset",
    link_fn=dataset_links,
    fetch_fn=fetch,
    search_by_name_fn=search_by_name,
    list_fn=list_page,
    list_extra_fields=("created_at",),
    description=(
        "Opik dataset (REST /datasets/{id}). A test suite is a dataset whose "
        "experiments carry evaluation_method = evaluation_suite; it is the same record."
    ),
)


ITEM_HANDLER = EntityHandler(
    entity_type="dataset_item",
    fetch_fn=fetch_item,
    page_note_fn=link_note_for("dataset_item"),
    list_fn=list_items,
    list_projection_fn=project_items,
    list_required_kwargs=("dataset_id",),
    list_vocabulary="dataset_item_case",
    # "case to trace" is the first hop the ``fields`` ticket names: a case
    # built from a traced run carries the trace it came from, and a projected
    # row without it is a question and an answer with no way back to the call
    # that produced them. Only on rows that have one — a hand-authored case
    # has no trace, and an empty column there would read as a lost link.
    list_identity_fields=("trace_id",),
    list_has_name=False,
    id_only=True,
    run_fn=run_compare,
    run_verb="compare",
    run_timeout_hint=(
        "Opik did not answer in time for list('dataset_item', experiment_ids=…). "
        "Retry with a smaller page (size=…), or fewer experiments."
    ),
    # Comparison is a different question with a different answer shape, so it
    # takes the whole call. Only the experiments switch to it: ``filters`` and
    # ``sort`` used to, back when the items endpoint could do neither, and a
    # filter meant for the cases was answered by refusing it.
    run_when_kwargs=("experiment_ids",),
    description=(
        "Dataset item. Pass dataset_id to enumerate the dataset's cases, filtered on the "
        "case itself (data.<key>, full_data, id, tags, source, trace_id, span_id); the "
        "endpoint has no sorting. read('dataset_item', id) is one case whole, which is how "
        "a value the table cut is read, while read('dataset') returns the dataset record "
        "without its items. Columns are the items' data keys, discovered from each page. "
        "With experiment_ids the same list compares those experiments case by case, on "
        "the runs' fields instead."
    ),
)
