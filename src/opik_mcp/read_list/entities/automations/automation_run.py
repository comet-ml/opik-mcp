"""``automation_run`` — one firing of an automation, or an hour of them rolled up."""

from __future__ import annotations

from opik_mcp.client.protocols import AutomationClient, OpikListClient
from opik_mcp.config import Settings
from opik_mcp.read_list.entities.automations._shared import (
    child,
    count,
    no_page,
    optional_text,
    positive_int,
    rows_of,
    text,
)
from opik_mcp.read_list.entities.automations.availability import missing_feature_refused
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.handler import EntityHandler, PageContext
from opik_mcp.read_list.project_scope import scope_of


async def fetch(client: AutomationClient, entity_id: str) -> dict[str, object]:
    return await client.get_automation_run(entity_id)


async def list_page(client: AutomationClient, **kw: object) -> dict[str, object]:
    # The backend scopes runs by automation or by project; an automation id
    # names its project, so only a listing without one has to resolve a name.
    automation_id = optional_text(kw, "automation_id")
    if automation_id is None and kw.get("project_id") is None and kw.get("project_name") is None:
        raise EntityArgValidationError(
            "list('automation_run') requires automation_id, project_id or project_name. "
            "E.g. list('automation_run', automation_id='<uuid>', status='failed')."
        )
    project_id = (
        None
        if automation_id is not None
        else await scope_of(client, kw, caller="list('automation_run')")
    )
    with missing_feature_refused():
        return await client.list_automation_runs(
            project_id=project_id,
            automation_id=automation_id,
            status=optional_text(kw, "status"),
            page=positive_int(kw, "page", 1),
            size=positive_int(kw, "size", 10),
        )


def derive_columns(record: dict[str, object]) -> dict[str, object]:
    """The trigger's counts and each step's outcome, flattened into cells."""
    trigger = child(record, "trigger")
    return {
        **record,
        "fired": count(trigger, "fired"),
        "matched": count(trigger, "matched"),
        "sampled": count(trigger, "sampled"),
        "step_outcomes": "; ".join(_outcome(step) for step in rows_of(record.get("steps"))),
    }


def _outcome(step: dict[str, object]) -> str:
    """``keep: 10 done/0 skipped/1 failed`` for a rollup, ``keep: done`` for a single run."""
    if "status" in step:
        return f"{text(step, 'id')}: {text(step, 'status')}"
    return (
        f"{text(step, 'id')}: {count(step, 'done') or 0} done/"
        f"{count(step, 'skipped') or 0} skipped/{count(step, 'failed') or 0} failed"
    )


async def status_note(_client: OpikListClient, _settings: Settings, ctx: PageContext) -> str | None:
    """The backend applies ``status`` to the page it returns and not to the
    count, so a narrowed page's total still counts runs of every status."""
    if ctx.status is None:
        return None
    return (
        f"The total counts runs of every status; only this page was narrowed to "
        f"status={ctx.status!r}, so it may hold fewer rows than size."
    )


HANDLER = EntityHandler(
    entity_type="automation_run",
    fetch_fn=fetch,
    link_fn=no_page,
    list_fn=list_page,
    list_row_fn=derive_columns,
    list_extra_fields=(
        "automation_id",
        "kind",
        "status",
        "started_at",
        "fired",
        "matched",
        "sampled",
        "step_outcomes",
        "cost_usd",
        "parent_run_id",
    ),
    list_optional_kwargs=("project_id", "automation_id", "status"),
    page_note_fn=status_note,
    list_has_name=False,
    id_only=True,
    no_window_reason=(
        "runs come back newest first, so page through them until you pass the time you care about."
    ),
    description=(
        "One firing of an automation — score-triggered ones are rolled up per UTC hour — "
        "with the trigger's fired/matched/sampled counts, each step's done, skipped and "
        "failed counts, and cost. list('automation_run', automation_id=… | project_id=… | "
        "project_name=…) narrows by status (done, partial, failed). "
        "read('automation_run', id) adds parent_run_id and child_run_ids."
    ),
)

__all__ = ["HANDLER"]
