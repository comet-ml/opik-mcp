"""Automations: creating and tending one, turning a recipe on, dismissing a recommendation.

Nine operations over the automations routes, and the three answers the
backend gives that mean more than their status: a 400 whose first error
string carries one of the definition codes, a 404 on a route that names no
record (the backend predates the feature), and a 201 whose only content is
the created id in ``Location``. A create, an update and a recipe enable also
ask the backend to validate and estimate on a dry run, through ``dry_run_fn``.

The models are ``create_model`` calls rather than ``class`` statements; the
comment above them says why.
"""

from __future__ import annotations

from types import GenericAlias
from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, create_model

from opik_mcp.client.errors import backend_reason, note_backend_401
from opik_mcp.client.opik import OpikClient
from opik_mcp.config import Settings
from opik_mcp.read_list.entities.automations.availability import (
    UNAVAILABLE_CODE,
    UNAVAILABLE_SENTENCE,
)
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.project_scope import resolve_project_id
from opik_mcp.writes.errors import BackendError
from opik_mcp.writes.models import _RequiredProjectMixin, _StrictBase, example_uuid
from opik_mcp.writes.wire import BuildContext, WireRequest, dump, refuse, safe_body

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opik_mcp.writes.registry import WriteOperation

Operator = Literal[">", "<", "=", ">=", "<="]
Recipe = Literal["collect", "gate", "improve", "align"]

# The models are ``create_model`` calls rather than ``class`` statements: mypy
# reads every ``class X(BaseModel)`` as explicit Any (pydantic's metaclass
# synthesizes an ``__init__`` from the base's ``dict[str, Any]`` attributes),
# and the explicit-Any count only shrinks. A call makes the same model.
# ``_PROJECT_SCOPED`` adds the ``project_id``-or-``project_name`` requirement
# every project-scoped write shares. A list of a model is spelled through
# ``GenericAlias`` because ``list[Model]`` is a type application to mypy and
# the model is a variable.
_PROJECT_SCOPED: Final = (_StrictBase, _RequiredProjectMixin)


def _list_of(model: type[BaseModel]) -> GenericAlias:
    return GenericAlias(list, (model,))


ScoreCondition = create_model(
    "ScoreCondition",
    __base__=_StrictBase,
    __doc__="One comparison on the score that fired the trigger.",
    __module__=__name__,
    score_name=(str, Field(min_length=1, max_length=200)),
    operator=(Operator, Field()),
    value=(float, Field()),
)
ConditionGroup = create_model(
    "ConditionGroup",
    __base__=_StrictBase,
    __doc__="Conditions that must all hold.",
    __module__=__name__,
    conditions=(_list_of(ScoreCondition), Field(min_length=1)),
)
Conditions = create_model(
    "Conditions",
    __base__=_StrictBase,
    __doc__="Any group can match, and every condition in a group must.",
    __module__=__name__,
    groups=(_list_of(ConditionGroup), Field(min_length=1)),
)
StepCondition = create_model(
    "StepCondition",
    __base__=_StrictBase,
    __doc__="One comparison on an earlier step's declared output.",
    __module__=__name__,
    step_output=(str, Field(min_length=1, max_length=200, description="`<step id>.<output>`.")),
    operator=(Operator, Field()),
    value=(float, Field()),
)
StepConditionGroup = create_model(
    "StepConditionGroup",
    __base__=_StrictBase,
    __doc__="Step conditions that must all hold.",
    __module__=__name__,
    conditions=(_list_of(StepCondition), Field(min_length=1)),
)
OnlyIf = create_model(
    "OnlyIf",
    __base__=_StrictBase,
    __doc__="Run the step only if any group of conditions on earlier outputs holds.",
    __module__=__name__,
    groups=(_list_of(StepConditionGroup), Field(min_length=1)),
)
Trigger = create_model(
    "Trigger",
    __base__=_StrictBase,
    __doc__="What fires the automation; triggers are joined by OR.",
    __module__=__name__,
    event=(
        str,
        Field(
            min_length=1,
            max_length=100,
            description=(
                "feedback_score:created (scope, conditions, per_day), prompt:committed "
                "(prompt_id or prompt_name; any prompt when absent), experiment:finished "
                "(test_suite_id or test_suite_name) or ci:run (repository, branch)."
            ),
        ),
    ),
    scope=(Literal["trace", "thread"], Field(default="trace")),
    conditions=(Conditions | None, Field(default=None)),
    per_day=(
        int | None,
        Field(default=None, ge=1, description="At most this many matches a day, at random."),
    ),
    prompt_id=(UUID | None, Field(default=None)),
    prompt_name=(str | None, Field(default=None, max_length=200)),
    test_suite_id=(UUID | None, Field(default=None)),
    test_suite_name=(str | None, Field(default=None, max_length=200)),
    repository=(str | None, Field(default=None, max_length=200, description="owner/name.")),
    branch=(str | None, Field(default=None, max_length=200)),
)
Step = create_model(
    "Step",
    __base__=_StrictBase,
    __doc__=(
        "One ordered step: add the item to a suite, dataset or queue (by id or name), "
        "run a suite, post a GitHub commit status, or notify."
    ),
    __module__=__name__,
    id=(str, Field(min_length=1, max_length=100)),
    type=(
        str,
        Field(
            min_length=1,
            max_length=100,
            description=(
                "test_suite.add, dataset.add, annotation_queue.add (score events); "
                "test_suite.run, github.check, notify (prompt, experiment and CI events)."
            ),
        ),
    ),
    test_suite_id=(UUID | None, Field(default=None)),
    test_suite_name=(str | None, Field(default=None, max_length=200)),
    dataset_id=(UUID | None, Field(default=None)),
    dataset_name=(str | None, Field(default=None, max_length=200)),
    annotation_queue_id=(UUID | None, Field(default=None)),
    annotation_queue_name=(str | None, Field(default=None, max_length=200)),
    assertion=(str | None, Field(default=None, max_length=10_000)),
    model=(
        str | None,
        Field(default=None, max_length=200, description="test_suite.run: required."),
    ),
    prompt_id=(UUID | None, Field(default=None)),
    prompt_name=(
        str | None,
        Field(
            default=None,
            max_length=200,
            description="test_suite.run: default the prompt version the event names.",
        ),
    ),
    repository=(
        str | None,
        Field(default=None, max_length=200, description="github.check: default the event's."),
    ),
    context=(
        str | None,
        Field(
            default=None,
            max_length=200,
            description="github.check: the status context, default opik/<automation name>.",
        ),
    ),
    url=(str | None, Field(default=None, max_length=2000, description="notify: required.")),
    kind=(
        Literal["webhook", "slack"] | None,
        Field(
            default=None,
            description="notify: a webhook gets the run summary JSON, slack gets {text}.",
        ),
    ),
    message=(str | None, Field(default=None, max_length=10_000, description="notify.")),
    only_if=(
        OnlyIf | None,
        Field(
            default=None,
            description=(
                "On an earlier step's outputs; test_suite.run declares pass_rate, "
                "baseline_pass_rate, regressions and experiment_id."
            ),
        ),
    ),
)
Limits = create_model(
    "Limits",
    __base__=_StrictBase,
    __doc__="Daily ceilings; hitting one pauses the automation until midnight UTC.",
    __module__=__name__,
    max_items_per_day=(int | None, Field(default=None, ge=1)),
    max_runs_per_day=(int | None, Field(default=None, ge=1)),
    max_cost_usd_per_day=(float | None, Field(default=None, ge=0)),
)
WorkflowOverrides = create_model(
    "WorkflowOverrides",
    __base__=_StrictBase,
    __doc__="What a recipe lets the caller choose; the rest are its defaults.",
    __module__=__name__,
    score_name=(str | None, Field(default=None, max_length=200)),
    threshold=(float | None, Field(default=None)),
    test_suite_name=(str | None, Field(default=None, max_length=200)),
    per_day=(int | None, Field(default=None, ge=1)),
    annotation_queue_name=(str | None, Field(default=None, max_length=200)),
    max_items_per_day=(int | None, Field(default=None, ge=1)),
    prompt_name=(
        str | None,
        Field(default=None, max_length=200, description="gate: the prompt it runs; asked."),
    ),
    model=(
        str | None,
        Field(default=None, max_length=200, description="gate: the model it runs with; asked."),
    ),
    repository=(
        str | None,
        Field(default=None, max_length=200, description="gate: owner/name adds the GitHub check."),
    ),
    notify_url=(
        str | None,
        Field(
            default=None,
            max_length=2000,
            description="gate: adds a notify step on regressions; Slack for a hooks.slack.com URL.",
        ),
    ),
)

AutomationCreate = create_model(
    "AutomationCreate",
    __base__=_PROJECT_SCOPED,
    __doc__="``POST /v1/private/automations`` — a custom automation on one project.",
    __module__=__name__,
    name=(str, Field(min_length=1, max_length=200)),
    triggers=(_list_of(Trigger), Field(min_length=1, description="Joined by OR.")),
    steps=(_list_of(Step), Field(min_length=1, description="Run in this order.")),
    limits=(Limits | None, Field(default=None)),
    enabled=(bool, Field(default=True)),
)
AutomationUpdate = create_model(
    "AutomationUpdate",
    __base__=_StrictBase,
    __doc__="``PATCH /v1/private/automations/{id}`` — change what is passed, keep the rest.",
    __module__=__name__,
    automation_id=(UUID, Field()),
    name=(str | None, Field(default=None, min_length=1, max_length=200)),
    enabled=(bool | None, Field(default=None)),
    triggers=(_list_of(Trigger) | None, Field(default=None, min_length=1)),
    steps=(_list_of(Step) | None, Field(default=None, min_length=1)),
    limits=(Limits | None, Field(default=None)),
)
AutomationEnable = create_model(
    "AutomationEnable",
    __base__=_StrictBase,
    __doc__="``PATCH …/{id}`` with ``enabled: true``.",
    __module__=__name__,
    automation_id=(UUID, Field()),
)
AutomationDisable = create_model(
    "AutomationDisable",
    __base__=_StrictBase,
    __doc__="``PATCH …/{id}`` with ``enabled: false``; items it already added stay.",
    __module__=__name__,
    automation_id=(UUID, Field()),
)
AutomationDelete = create_model(
    "AutomationDelete",
    __base__=_StrictBase,
    __doc__="``POST /v1/private/automations/delete`` with ``{ids: [id]}``; runs kept 30 days.",
    __module__=__name__,
    automation_id=(UUID, Field()),
)
AutomationTest = create_model(
    "AutomationTest",
    __base__=_StrictBase,
    __doc__="``POST …/{id}/test`` — the steps on one trace or thread, in shadow mode.",
    __module__=__name__,
    automation_id=(UUID, Field()),
    trace_id=(UUID | None, Field(default=None)),
    thread_id=(str | None, Field(default=None, max_length=200)),
)
WorkflowEnable = create_model(
    "WorkflowEnable",
    __base__=_PROJECT_SCOPED,
    __doc__="``POST …/workflows/enable`` — a recipe's automations, its missing targets created.",
    __module__=__name__,
    recipe=(Recipe, Field()),
    overrides=(WorkflowOverrides | None, Field(default=None)),
)
WorkflowDisable = create_model(
    "WorkflowDisable",
    __base__=_PROJECT_SCOPED,
    __doc__="``POST …/workflows/disable`` — every automation the recipe created, turned off.",
    __module__=__name__,
    recipe=(Recipe, Field()),
)
RecommendationDismiss = create_model(
    "RecommendationDismiss",
    __base__=_PROJECT_SCOPED,
    __doc__="``POST …/recommendations/{id}/dismiss`` — hidden for 30 days, per project.",
    __module__=__name__,
    recommendation_id=(UUID, Field()),
)

#: The fields an update may change; passing none of them is not an update.
_UPDATE_FIELDS: Final = ("name", "enabled", "triggers", "steps", "limits")


def validate_update(op: WriteOperation, items: list[BaseModel], **_hooks: object) -> None:
    """An update that changes nothing is refused before anything is sent."""
    if all(getattr(items[0], field, None) is None for field in _UPDATE_FIELDS):
        raise refuse(
            op,
            "",
            f"Pass at least one of {', '.join(_UPDATE_FIELDS)} to change.",
            "nothing_to_update",
        )


def validate_test_subject(op: WriteOperation, items: list[BaseModel], **_hooks: object) -> None:
    """One subject: a trace or a thread, not neither and not both."""
    trace_id = getattr(items[0], "trace_id", None)
    thread_id = getattr(items[0], "thread_id", None)
    if trace_id is None and thread_id is None:
        raise refuse(op, "", "Pass trace_id or thread_id to run it on.", "test_subject_missing")
    if trace_id is not None and thread_id is not None:
        raise refuse(
            op, "", "Pass either trace_id or thread_id, not both.", "test_subject_conflict"
        )


_AUTOMATION: Final = example_uuid("a1")

AUTOMATION_CREATE_EXAMPLE: Final[dict[str, object]] = {
    "project_name": "demo",
    "name": "Low-resolution traces to the suite",
    "triggers": [
        {
            "event": "feedback_score:created",
            "scope": "trace",
            "conditions": {
                "groups": [
                    {"conditions": [{"score_name": "resolution", "operator": "<", "value": 0.5}]}
                ]
            },
        }
    ],
    "steps": [{"id": "keep", "type": "test_suite.add", "test_suite_name": "demo-regressions"}],
    "limits": {"max_items_per_day": 200},
}
AUTOMATION_UPDATE_EXAMPLE: Final[dict[str, object]] = {
    "automation_id": _AUTOMATION,
    "limits": {"max_items_per_day": 100},
}
AUTOMATION_ENABLE_EXAMPLE: Final[dict[str, object]] = {"automation_id": _AUTOMATION}
AUTOMATION_DISABLE_EXAMPLE: Final[dict[str, object]] = {"automation_id": _AUTOMATION}
AUTOMATION_DELETE_EXAMPLE: Final[dict[str, object]] = {"automation_id": _AUTOMATION}
AUTOMATION_TEST_EXAMPLE: Final[dict[str, object]] = {
    "automation_id": _AUTOMATION,
    "trace_id": example_uuid("e1"),
}
WORKFLOW_ENABLE_EXAMPLE: Final[dict[str, object]] = {
    "recipe": "collect",
    "project_name": "demo",
    "overrides": {"score_name": "resolution", "threshold": 0.5},
}
WORKFLOW_DISABLE_EXAMPLE: Final[dict[str, object]] = {"recipe": "collect", "project_name": "demo"}
RECOMMENDATION_DISMISS_EXAMPLE: Final[dict[str, object]] = {
    "recommendation_id": example_uuid("c1"),
    "project_name": "demo",
}

_PREFIX: Final = "/v1/private/automations"

#: Routes that name no record, so a 404 from one can only mean the backend
#: predates the feature. An id route's 404 keeps its usual meaning.
_COLLECTION_ROUTES: Final = frozenset(
    {_PREFIX, f"{_PREFIX}/delete", f"{_PREFIX}/workflows/enable", f"{_PREFIX}/workflows/disable"}
)

#: The definition codes the backend answers a 400 with, each as our own
#: sentence; the backend's words follow it, capped, the way a read quotes them.
_DEFINITION_CODES: Final[dict[str, tuple[str, str]]] = {
    "unknown_event": ("triggers", "A trigger names an event this backend does not fire."),
    "unknown_step": ("steps", "A step has a type this backend cannot run."),
    "condition_unknown_step": ("steps", "An only_if names a step that is not earlier in steps."),
    "condition_unknown_output": (
        "steps",
        "An only_if names an output no earlier step declares; test_suite.run declares "
        "pass_rate, baseline_pass_rate, regressions and experiment_id, the add steps none.",
    ),
    "step_scope_mismatch": (
        "steps",
        "An automation listens to score events with add steps, or to prompt, experiment and "
        "CI events with run, check and notify steps; not both.",
    ),
    "target_not_found": (
        "steps",
        "A step names a suite, dataset or queue this project does not have; create it or "
        "name an existing one.",
    ),
    "limit_required": (
        "limits",
        "A step that costs money needs max_cost_usd_per_day in limits.",
    ),
    "score_name_required": (
        "overrides.score_name",
        "A recipe never picks the judge: ask the user which score to act on (the workflow row's "
        "available_score_names lists what the project writes) and pass it in overrides.score_name.",
    ),
    "prompt_name_required": (
        "overrides.prompt_name",
        "A recipe never picks the prompt: ask the user which prompt Gate runs the suite "
        "against and pass it in overrides.prompt_name.",
    ),
    "model_required": (
        "overrides.model",
        "A recipe never picks the model: ask the user which model runs the suite and pass it "
        "in overrides.model.",
    ),
    "integration_required": (
        "steps",
        "github.check posts with the workspace's GitHub token, which is not set. The user sets "
        'it themselves with PUT /v1/private/automations/integrations/github {"token": …}; '
        "it never goes through the MCP, so do not ask for it.",
    ),
}

#: What to do with the CI step a recipe hands back: it is the user's repo
#: that changes, as a change they review, and nothing here merges it.
_SETUP_NOTE: Final = (
    "setup.github_action is the step a GitHub workflow in the user's repo adds, with the "
    "OPIK_API_KEY, OPIK_WORKSPACE and OPIK_URL placeholders filled from the repo's secrets: "
    "write it into .github/workflows/ as a change for review; never merge it."
)

_SWITCH: Final = {"automation.enable": True, "automation.disable": False}
_CREATED_VIA: Final = "mcp"


async def prepare_scope(
    op: WriteOperation, items: list[BaseModel], client: OpikClient
) -> str | None:
    """The project's UUID for the operations that carry one; ``None`` for the rest."""
    item = items[0]
    project_id = getattr(item, "project_id", None)
    if project_id is not None:
        return str(project_id)
    project_name = getattr(item, "project_name", None)
    if project_name is None:
        return None
    try:
        return await resolve_project_id(client, str(project_name))
    except EntityArgValidationError as e:
        raise refuse(op, "project_name", str(e), "project_scope_missing") from e


def _scope(items: list[BaseModel], ctx: BuildContext) -> str:
    """The project id the body carries: resolved, passed, or a template on a plain dry run."""
    passed = getattr(items[0], "project_id", None)
    return ctx.prepared or (str(passed) if passed is not None else "{project_id}")


def _body(items: list[BaseModel]) -> dict[str, object]:
    body: dict[str, object] = dump(items[0])
    return body


def build_create(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    body = _body(items)
    body.pop("project_name", None)
    return WireRequest(
        op.endpoint, {**body, "project_id": _scope(items, ctx), "created_via": _CREATED_VIA}
    )


def build_update(op: WriteOperation, items: list[BaseModel], _ctx: BuildContext) -> WireRequest:
    body = _body(items)
    return WireRequest(op.endpoint.format(automation_id=body.pop("automation_id")), body)


def build_switch(op: WriteOperation, items: list[BaseModel], _ctx: BuildContext) -> WireRequest:
    body = _body(items)
    path = op.endpoint.format(automation_id=body["automation_id"])
    return WireRequest(path, {"enabled": _SWITCH[op.name]})


def build_delete(op: WriteOperation, items: list[BaseModel], _ctx: BuildContext) -> WireRequest:
    return WireRequest(op.endpoint, {"ids": [_body(items)["automation_id"]]})


def build_test(op: WriteOperation, items: list[BaseModel], _ctx: BuildContext) -> WireRequest:
    body = _body(items)
    return WireRequest(op.endpoint.format(automation_id=body.pop("automation_id")), body)


def build_workflow(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    body = _body(items)
    body.pop("project_name", None)
    body["project_id"] = _scope(items, ctx)
    if op.name == "workflow.enable":
        body["created_via"] = _CREATED_VIA
    return WireRequest(op.endpoint, body)


def build_dismiss(op: WriteOperation, items: list[BaseModel], ctx: BuildContext) -> WireRequest:
    body = _body(items)
    path = op.endpoint.format(recommendation_id=body["recommendation_id"])
    return WireRequest(path, {"project_id": _scope(items, ctx)})


def _definition_code(resp: httpx.Response) -> str | None:
    """The definition code a 400 carries, from its ``code`` or its first error's prefix."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if not isinstance(code, str):
        errors = body.get("errors")
        first = (
            next((e for e in errors if isinstance(e, str)), "") if isinstance(errors, list) else ""
        )
        code = first.partition(":")[0]
    code = code.strip()
    return code if code in _DEFINITION_CODES else None


def _raise_for_definition(op: WriteOperation, request: WireRequest, resp: httpx.Response) -> None:
    """A 400 with a definition code, or a 404 that means no backend feature, as a refusal."""
    if resp.status_code == 400:
        code = _definition_code(resp)
        if code is not None:
            field, sentence = _DEFINITION_CODES[code]
            reason = backend_reason(resp)
            said = f' Backend said: "{reason}"' if reason else ""
            raise refuse(op, field, f"{sentence}{said}", code)
    if resp.status_code == 404 and request.path in _COLLECTION_ROUTES:
        raise refuse(op, "", UNAVAILABLE_SENTENCE, UNAVAILABLE_CODE)


async def retry(
    op: WriteOperation,
    _client: OpikClient,
    request: WireRequest,
    resp: httpx.Response,
) -> tuple[WireRequest, httpx.Response]:
    """The three backend answers that say more than their status.

    A 400 with a definition code is the caller's to fix, so it is a refusal
    naming the field. A 404 on a route that names no record is the feature
    missing. A 201 carries the created id in ``Location`` and nothing in the
    body, and the id is what the caller needs next, so it is put in the body.
    """
    _raise_for_definition(op, request, resp)
    location = resp.headers.get("location") if resp.status_code == 201 else None
    if location:
        # Beside whatever the body already says (a cycle warning), not instead.
        body = safe_body(resp)
        said = body if isinstance(body, dict) else {}
        created = {**said, "id": location.rstrip("/").rsplit("/", 1)[-1]}
        return request, httpx.Response(201, json=created, request=resp.request)
    return request, resp


async def dry_run(
    op: WriteOperation, _items: list[BaseModel], client: OpikClient, request: WireRequest
) -> dict[str, object]:
    """What the backend says it would do: the validated definition and a 14-day estimate.

    The flag is a query parameter on every route, a recipe enable included: a
    ``dry_run`` field in the body is ignored there and the enable runs for real.
    The backend validates as it would on the live call, so a refusal here is
    the one the live call would give.
    """
    path = f"{request.path}?dry_run=true"
    resp = await client.write_json(request.method or op.method, path, request.body)
    if not (200 <= resp.status_code < 300):
        _raise_for_definition(op, request, resp)
        if resp.status_code == 401:
            note_backend_401()
        raise BackendError.build(op.name, resp.status_code, backend_message=backend_reason(resp))
    answer = safe_body(resp)
    if not isinstance(answer, dict):
        return {}
    shaped: dict[str, object] = {}
    if op.name == "workflow.enable":
        # The backend names what it would create; `created` is empty on a dry run.
        shaped["would_create"] = answer.get("would_create", [])
    elif "automation" in answer:
        shaped["automation"] = answer["automation"]
    for key in ("estimate", "setup", "warnings"):
        if answer.get(key) is not None:
            shaped[key] = answer[key]
    if "setup" in shaped:
        shaped["note"] = _SETUP_NOTE
    return shaped


def decorate(
    op: WriteOperation,
    _items: list[BaseModel],
    out: dict[str, object],
    _settings: Settings,
    _prepared: str | None,
) -> None:
    """What to do next with what was created."""
    if op.name == "automation.create":
        out["note"] = (
            "backend_body carries the new automation's id: read('automation', id) shows it "
            "with its stats, and automation.test runs it on one trace with nothing written."
        )
    elif op.name == "workflow.enable":
        note = (
            "backend_body carries the automations created and the 14-day estimate; "
            "list('automation', project_id=…) lists them."
        )
        body = out.get("backend_body")
        setup = body.get("setup") if isinstance(body, dict) else None
        if setup is not None:
            # Beside the body as the dry run shows it, so the two answers read alike.
            out["setup"] = setup
            note = f"{note} {_SETUP_NOTE}"
        out["note"] = note


def dry_run_note(op: WriteOperation, items: list[BaseModel], _prepared: str | None) -> str | None:
    """Only a plain dry run leaves the project unresolved; one the backend
    answers has already resolved it, and a UUID in hand needs no resolving."""
    if op.dry_run_fn is not None or getattr(items[0], "project_name", None) is None:
        return None
    return (
        "project_name is resolved to the project's UUID at execution; the live body "
        "will carry that id."
    )


__all__ = [
    "AutomationCreate",
    "AutomationDelete",
    "AutomationDisable",
    "AutomationEnable",
    "AutomationTest",
    "AutomationUpdate",
    "RecommendationDismiss",
    "WorkflowDisable",
    "WorkflowEnable",
    "build_create",
    "build_delete",
    "build_dismiss",
    "build_switch",
    "build_test",
    "build_update",
    "build_workflow",
    "decorate",
    "dry_run",
    "dry_run_note",
    "prepare_scope",
    "retry",
    "validate_test_subject",
    "validate_update",
]
