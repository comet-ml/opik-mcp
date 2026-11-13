"""``recommendation`` — the ranked next steps for a project, each with its fix."""

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

#: The apply call is sent verbatim to ``write``, so it is never cut.
_CELL_LIMIT: Final = 2000


async def list_page(client: AutomationClient, **kw: object) -> dict[str, object]:
    project_id = await scope_of(client, kw, caller="list('recommendation')")
    with missing_feature_refused():
        ranked = await client.list_recommendations(project_id=project_id)
    return page_of(rows_of(ranked), kw)


def derive_columns(record: dict[str, object]) -> dict[str, object]:
    """The ``write`` call that applies the recommendation, ready to send."""
    apply = child(record, "apply")
    call = (
        f"write({text(apply, 'operation')!r}, data={compact(apply.get('data'))})" if apply else ""
    )
    return {**record, "apply_call": call}


def project_recommendations(_items: list[dict[str, object]]) -> ListProjection:
    return ListProjection(
        columns=("kind", "workflow", "automation_id", "reason", "blocked_until", "apply_call"),
        cell_limit=_CELL_LIMIT,
        note=(
            "Ranked as the backend ranks them: setup with prerequisites met, then upkeep, "
            "then blocked setup; omitted: evidence, apply as an object — fields=['evidence'] "
            "returns it."
        ),
    )


HANDLER = EntityHandler(
    entity_type="recommendation",
    fetch_fn=unsupported_fetch,
    list_fn=list_page,
    list_row_fn=derive_columns,
    list_projection_fn=project_recommendations,
    list_required_kwargs=("project_id",),
    list_has_name=False,
    description=(
        "Ranked next steps for a project, never empty: each row's kind, workflow or "
        "automation_id, reason and the write call that applies it, verbatim. "
        "List-only, by project; dismiss one with recommendation.dismiss."
    ),
)

__all__ = ["HANDLER"]
