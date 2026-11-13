"""``workflow`` — a recipe's state for a project, with what it would create."""

from __future__ import annotations

from typing import Final

from opik_mcp.client.protocols import AutomationClient
from opik_mcp.read_list.entities.automations._shared import (
    child,
    compact,
    page_of,
    rows_of,
    text,
)
from opik_mcp.read_list.entities.automations.availability import missing_feature_refused
from opik_mcp.read_list.handler import EntityHandler, ListProjection
from opik_mcp.read_list.project_scope import scope_of
from opik_mcp.read_list.unsupported import unsupported_fetch

#: The overrides cell is a JSON object naming the suite and the queue, which
#: the table's default width would cut in the middle of a name.
_CELL_LIMIT: Final = 400


async def list_page(client: AutomationClient, **kw: object) -> dict[str, object]:
    project_id = await scope_of(client, kw, caller="list('workflow')")
    with missing_feature_refused():
        recipes = await client.list_workflows(project_id=project_id)
    return page_of(rows_of(recipes), kw)


def derive_columns(record: dict[str, object]) -> dict[str, object]:
    estimate = child(record, "estimate")
    names = record.get("available_score_names")
    return {
        **record,
        "automation_names": "; ".join(
            text(automation, "name") for automation in rows_of(record.get("automations"))
        ),
        "estimate_14d": (
            f"fired {text(estimate, 'fired')}, items {text(estimate, 'items')}, "
            f"cost ${text(estimate, 'cost_usd')}"
            if estimate
            else ""
        ),
        "overrides_json": compact(record.get("overrides")),
        "score_names": ", ".join(str(name) for name in names) if isinstance(names, list) else "",
    }


def project_workflows(_items: list[dict[str, object]]) -> ListProjection:
    return ListProjection(
        columns=(
            "recipe",
            "state",
            "prerequisites_met",
            "blocked_reason",
            "automation_names",
            "estimate_14d",
            "overrides_json",
            "score_names",
        ),
        cell_limit=_CELL_LIMIT,
        note=(
            "Columns summarise each recipe; omitted: automations, estimate, overrides and "
            "available_score_names as objects. write('workflow.enable', data=…, "
            "dry_run=true) returns the definitions a recipe would create, whole."
        ),
    )


HANDLER = EntityHandler(
    entity_type="workflow",
    fetch_fn=unsupported_fetch,
    list_fn=list_page,
    list_row_fn=derive_columns,
    list_projection_fn=project_workflows,
    list_required_kwargs=("project_id",),
    list_has_id=False,
    description=(
        "A recipe's state for a project — collect, gate, align or improve, on, partly_on or off "
        "— with "
        "the automations it would create filled in for this project and a 14-day "
        "estimate. List-only, by project; blocked_reason says what a recipe is waiting for."
    ),
)

__all__ = ["HANDLER"]
