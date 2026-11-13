"""``automation`` — when an event fires on a project, run its steps in order."""

from __future__ import annotations

from typing import Final

from opik_mcp.client.protocols import AutomationClient
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
from opik_mcp.read_list.handler import EntityHandler, ParamField, Vocabulary
from opik_mcp.read_list.project_scope import scope_of

#: What a step writes to: the first of these a step names is its target. A
#: check's target is its repository and a notification's its URL.
_TARGET_NAMES: Final = (
    "test_suite_name",
    "dataset_name",
    "annotation_queue_name",
    "repository",
    "url",
)
#: The steps whose target is not the first name they carry: the optimizer
#: names the suite it runs on but writes the prompt, and a judge step names
#: the queue but is about the score.
_TARGET_OF: Final = {
    "optimizer.run": "prompt_name",
    "prompt_version.save": "prompt_name",
    "judge.agreement": "score_name",
    "judge.revise": "score_name",
}


async def fetch(client: AutomationClient, entity_id: str) -> dict[str, object]:
    return await client.get_automation(entity_id)


async def list_page(client: AutomationClient, **kw: object) -> dict[str, object]:
    project_id = await scope_of(client, kw, caller="list('automation')")
    with missing_feature_refused():
        return await client.list_automations(
            project_id=project_id,
            enabled=optional_text(kw, "enabled"),
            recipe=optional_text(kw, "recipe"),
            event=optional_text(kw, "event"),
            page=positive_int(kw, "page", 1),
            size=positive_int(kw, "size", 10),
        )


def derive_columns(record: dict[str, object]) -> dict[str, object]:
    """The cells a row needs out of the nested definition and stats.

    New names rather than overwritten ones: ``steps`` stays the list the
    record carries, so ``fields=["steps"]`` still returns it.
    """
    steps = rows_of(record.get("steps"))
    stats = child(record, "stats")
    paused = child(record, "paused_reason")
    return {
        **record,
        "recipe_id": text(child(record, "recipe"), "id"),
        "event": ", ".join(text(trigger, "event") for trigger in rows_of(record.get("triggers"))),
        "step_targets": "; ".join(f"{text(step, 'type')}→{_target(step)}" for step in steps),
        "fired_7d": count(stats, "fired"),
        "items_7d": count(stats, "items_written"),
        "paused": f"{text(paused, 'limit')} until {text(paused, 'until')}" if paused else "",
    }


def _target(step: dict[str, object]) -> str:
    own = _TARGET_OF.get(text(step, "type"))
    if own is not None and step.get(own):
        return text(step, own)
    return next((text(step, key) for key in _TARGET_NAMES if step.get(key)), "")


VOCABULARY = Vocabulary(
    name="automation",
    # Each is a query parameter of its own on the backend, so a clause can be
    # one exact value and nothing else; ``split_param_clauses`` lifts them.
    filter_fields={"enabled": "enum", "recipe": "string", "event": "string"},
    enum_values={"enabled": ("true", "false")},
    param_fields={
        "enabled": ParamField(
            param="enabled",
            operators=("=",),
            encoding="single",
            why="the backend takes one on/off value",
        ),
        "recipe": ParamField(
            param="recipe",
            operators=("=",),
            encoding="single",
            why="the backend takes one recipe id",
        ),
        "event": ParamField(
            param="event",
            operators=("=",),
            encoding="single",
            why="the backend takes one event name",
        ),
    },
    filter_examples=('recipe = "collect"', 'enabled = "true" AND event = "feedback_score:created"'),
    unsortable_why="the backend lists automations newest first and takes no sort.",
)


HANDLER = EntityHandler(
    entity_type="automation",
    vocabularies=(VOCABULARY,),
    fetch_fn=fetch,
    link_fn=no_page,
    list_fn=list_page,
    list_row_fn=derive_columns,
    list_extra_fields=(
        "enabled",
        "recipe_id",
        "event",
        "step_targets",
        "fired_7d",
        "items_7d",
        "paused",
    ),
    list_required_kwargs=("project_id",),
    list_optional_kwargs=("enabled", "recipe", "event"),
    id_only=True,
    no_window_reason="automations have no time window; their runs do, under automation_run.",
    description=(
        "An automation on a project: when an event fires on a trace or thread, a "
        "prompt, a suite, a queue or a CI run, run its steps in order. "
        "list('automation', project_id=… | project_name=…) "
        "returns the project's automations with their recipe, event, step targets "
        "and 7-day stats; filters narrow by enabled, recipe or event. "
        "read('automation', id) returns the definition with its 7-day stats and "
        "paused_reason. Online evaluation rules are listed under online_rule, not here."
    ),
)

__all__ = ["HANDLER", "VOCABULARY"]
