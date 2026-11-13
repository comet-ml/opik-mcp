# automations

## Purpose

Automations are Opik's "when an event happens on an object, run ordered
steps" primitive, and this feature is their MCP surface: four readable entity
types, nine write operations and the `opik-loop` skill, so a coding agent can
set up and tend a project's improvement loop without the UI. This doc answers
what each read and write promises, how a dry run differs here from everywhere
else, and what an older backend answers.

Phase 1 of the product spec plus the Gate increment: the `feedback_score:created`,
`prompt:committed`, `experiment:finished` and `ci:run` events; the
`test_suite.add`, `dataset.add` and `annotation_queue.add` steps on score
events, and `test_suite.run`, `github.check` and `notify` (with `only_if` on a
run's outputs) on the others; the `collect`, `align` and `gate` recipes; setup
recommendations. The `loop` read, `improve`, the remaining events and steps,
and upkeep recommendations are later phases and are not here.

## What it does now

### Reads

| Entity | list | read |
|---|---|---|
| `automation` | by project; `filters` on `enabled`, `recipe`, `event`, each lifted to its own query parameter (`schema('list.automation')`) | the definition with its 7-day `stats` and `paused_reason` |
| `automation_run` | by `automation_id` (a new `list` argument, no project needed) or by project; `status` is `pending`, `done`, `partial` or `failed` | the run with `parent_run_id` and `child_run_ids`; a single run also carries `item_id`, `outputs`, `waiting_on`, `depth` |
| `workflow` | by project: each recipe's state, what it would create, the 14-day estimate, `available_score_names`; `gate`'s overrides (`prompt_name`, `model`, the suite name, `repository`, `notify_url`) | list-only |
| `recommendation` | by project, ranked by the backend, never empty; each row carries `apply_call`, the `write` call that applies it, verbatim | list-only |

- The entity descriptions in `src/opik_mcp/read_list/entities/automations/`
  are the promises; every sentence has a probe in
  `tests/hermetic/test_description_claims.py`.
- The backend applies `status` to the page it returns and not to the total,
  so a narrowed `automation_run` page says so (`status_note`).
- An `automation_run` row renders `item_id` (the prompt version, the experiment,
  or `<repository>@<commit>`), `outputs` (the flat `<step id>.<output>` map) and
  `waiting_on`; its cells are cut at 200 characters rather than the table's 60,
  so the outputs read whole up to the regressions count (`project_runs`).
- An automation row's `step_targets` names a check's repository and a
  notification's URL as the step's target, beside the suites, datasets and queues.
- `workflow` and `recommendation` come back as arrays, so their pages are cut
  here (`page_of`), as `score_name`'s are.
- Rows derive their cells under new names (`recipe_id`, `step_targets`,
  `apply_call`, …) and keep the record's own keys, so `fields=` still reaches
  the objects: `fields=["recipe", "estimate.fired"]` works on a workflow.
- No record has a page in the UI yet, so a read carries `url_absent` saying so
  rather than a guessed link.
- Online evaluation rules stay under `online_rule`; the `automation`
  description says so.

### Writes

| Operation | Backend call | Scope |
|---|---|---|
| `workflow.enable` | `POST …/workflows/enable` with `recipe`, `project_id`, `overrides`, `created_via: "mcp"` | `automation_manage` |
| `workflow.disable` | `POST …/workflows/disable` | `automation_manage` |
| `automation.create` | `POST /v1/private/automations` with the definition, `project_id`, `created_via: "mcp"` | `automation_manage` |
| `automation.update` | `PATCH …/{id}` with the fields passed; none passed is `nothing_to_update` | `automation_manage` |
| `automation.enable` / `.disable` | `PATCH …/{id}` with `{enabled}` | `automation_manage` |
| `automation.delete` | `POST …/delete` with `{ids: [id]}` | `automation_manage` |
| `automation.test` | `POST …/{id}/test` with `trace_id` or `thread_id` (one, not both) | `project_data_view` |
| `recommendation.dismiss` | `POST …/recommendations/{id}/dismiss` with `{project_id}` | `automation_manage` |

- `project_name` resolves to the id in `prepare_scope`, as the Diagnostics
  writes do; a name that matches no project is `project_scope_missing`.
- **Dry run asks the backend.** `automation.create`, `automation.update` and
  `workflow.enable` carry a `dry_run_fn`: the dispatcher then resolves the
  project on the dry run too and the hook sends the real request with
  `?dry_run=true` on every route (the backend ignores a `dry_run` field in a
  recipe enable's body and would enable it for real). The preview adds the backend's
  answer: `automation` or `would_create`, `estimate`, and `setup` or
  `warnings` when present. Every other operation's dry run is unchanged.
- A recipe never picks the judge, the prompt or the model. `workflow.enable` without
  `overrides.score_name` is refused by the backend with `score_name_required`, whose
  sentence lists the scores the project writes; `gate` without `overrides.prompt_name`
  or `overrides.model` is refused with `prompt_name_required` / `model_required`. The
  skill asks the user before the dry run.
- Gate's `setup.github_action` (the YAML step a GitHub workflow adds) comes back
  on the dry run and, lifted to `setup` beside `backend_body`, on the live
  answer; both carry a `note` saying it goes into `.github/workflows/` as a
  change for review and is never merged here. The `OPIK_API_KEY`,
  `OPIK_WORKSPACE` and `OPIK_URL` placeholders are the agent's to fill.
- The GitHub token a `github.check` step posts with never passes through the
  MCP: there is no write for `PUT /v1/private/automations/integrations/github`,
  and the `integration_required` sentence tells the user to run that PUT
  themselves.
- A 400 whose first error string starts with a definition code
  (`unknown_event`, `unknown_step`, `step_scope_mismatch`, `condition_unknown_step`,
  `condition_unknown_output`, `target_not_found`, `limit_required`,
  `integration_required`, and on a recipe enable `score_name_required`,
  `prompt_name_required`, `model_required`) is a `validation_failed` with that
  code, our sentence for it, the field it is about, and the backend's words
  after `Backend said:`, capped. Any other 400 stays a `backend_error`.
- A 404 on a route that names no record (the collection, `delete`, the two
  workflow routes) means the backend predates the feature: refused as
  `automations_unavailable` with the sentence the reads use
  (`UNAVAILABLE_SENTENCE`). A 404 on an id route keeps its meaning.
- A create answers 201 with the id in `Location` only; `retry` puts it in the
  body, so `backend_body` is `{"id": …}` and the note says what to do with it.

### The skill

`opik-loop` (`src/opik_mcp/skills/opik-loop/`) owns the flows: set up after
instrument, gate PRs on the suite (ask the prompt and the model, dry run, write
the CI step for review, enable), apply the top recommendation, answer "is it
worth it" from the stats, explain a failed run or a gate run from its
`outputs` and hand the two experiments to `opik-compare`, build and
shadow-test a custom automation. Its rules: dry run first and show the
estimate, ask before every write, never a paying step without
`max_cost_usd_per_day`, name the recipe and what it writes to, never ask for
or echo the GitHub token, hand off to `opik-online-eval`, `opik-test`,
`opik-compare` and `opik-explain`, and suggest the weekly check-in once. For
a code agent Opik cannot run, the skill builds the custom `experiment:finished`
automation instead of the `gate` recipe.

## How it works

```
list/read  run_list / run_read -> entities/automations/<entity>.py -> AutomationClient
                                 (client/automations.py) -> /v1/private/automations…
write      dispatch.py -> hooks in writes/operations/automations.py
           dry run with dry_run_fn: prepare_fn, build, then the hook calls the backend
```

- Entities: `src/opik_mcp/read_list/entities/automations/`, one module each,
  `_shared.py` for the typed JSON helpers and `availability.py` for the
  missing-backend sentence, which the writes import too.
- Client: `AutomationEndpoints` in `src/opik_mcp/client/automations.py`, typed
  for the entities through `AutomationClient` in `src/opik_mcp/client/protocols.py`,
  a protocol of its own so the shared ones and their test fakes stay as they were.
- The `automation_id` argument: `src/opik_mcp/server/tools/list.py`,
  forwarded by `src/opik_mcp/read_list/list_args.py` to the entities that declare it.
- Writes: `src/opik_mcp/writes/operations/automations.py`, registered in
  `src/opik_mcp/writes/registry.py`; the hook type `DryRunFn` is in
  `src/opik_mcp/writes/wire.py` and the dry-run branch in `src/opik_mcp/writes/dispatch.py`.
- The write pipeline: [writes](../writes/design-doc.md). Reads and the
  budget: [tool-surface](../tool-surface/design-doc.md). The skill and the
  pack: [skills](../skills/design-doc.md).

## Decisions

- The models are `create_model` calls, not `class` statements. mypy reads
  every `class X(BaseModel)` as explicit `Any` (pydantic's metaclass
  synthesizes an `__init__` from the base's `dict[str, Any]` attributes) and
  the explicit-Any count in `tests/repo/ratchets.json` only shrinks; a call
  builds the same model. Cross-field rules (`nothing_to_update`, one test
  subject) live in `validate_fn` hooks, which is what the hook is for.
- The dry run asks the backend because the spec's estimate is the point of
  the dry run: an agent shows it before asking for a yes. One generic hook,
  so the dispatcher names no operation (`test_the_dispatcher_names_no_operation`).
- `enabled`, `recipe` and `event` are OQL filters lifted to query
  parameters rather than three new `list` arguments: the mechanism exists,
  `schema('list.automation')` documents it, and it costs the surface one enum entry.
- `since` is not taken on `automation_run` in phase 1: the list tool's
  window travels with free-text search and `should_truncate`, which the runs
  route has no use for. The refusal says to page newest-first instead.
- `recipe` is the four names the spec gives; a backend lists and accepts the
  ones it runs (`collect`, `align`, `gate`) and answers the others itself.
- The write models accept every trigger parameter and step field of every
  event and step type on one `Trigger` and one `Step` model, as optional
  fields, and the backend decides which combination is valid (`unknown_step`,
  `step_scope_mismatch`, `integration_required`): one model is what
  `schema('automation.create')` can show, and the rules live in one place.
- No UI link: there is no automations page yet, and a guessed link opens the
  wrong page under the reader's last project.

### Traps

- A recipe enable's `setup` block (Gate's CI step) is only ever written as a
  change for review; nothing here merges it.
- `AutomationUpdate` with only `automation_id` validates (every field is
  optional) and is refused by `validate_update`, not by the model.
- The hermetic stub answers a create with 201 and no `Location`, so the
  Location-to-id path is proven by `test_a_create_hands_back_the_id_from_location` only.
- The hermetic stub enables `gate` with or without `prompt_name` and `model`;
  the refusal is proven against a mocked 400 in `tests/writes/test_automations.py`.
- The contract lists `test_suite.run`'s outputs as `pass_rate`,
  `baseline_pass_rate`, `regressions` and `experiment_id`; the compare hand-off
  also wants `baseline_experiment_id`, which the fixtures carry and the skill
  reads when present, falling back to the suite's previous finished experiment.

## Proven by

- The four entities over stdio and HTTP, the missing-backend sentence, the
  status note, the cut page: `tests/hermetic/reads/test_automation.py`,
  `tests/hermetic/reads/test_automation_run.py`, `tests/hermetic/reads/test_workflow.py`,
  `tests/hermetic/reads/test_recommendation.py`; every description sentence:
  `tests/hermetic/test_description_claims.py`.
- Every operation's request, the backend dry run, a coded 400, the missing
  backend, a shadow test: `tests/hermetic/writes/test_automations.py`.
- The hooks against a mocked backend — what a dry run sends and merges, the
  404 on each kind of route, the id from `Location`, the scopes:
  `tests/writes/test_automations.py`.
- The registry, enum and description agree, and the dispatcher stays generic:
  `tests/writes/test_registry.py`, `tests/writes/test_dispatch_stays_generic.py`.
- The entity enums, the optional kwargs and the list views:
  `tests/conformance/test_tool_inventory.py`, `tests/read_list/test_registry.py`,
  `tests/read_list/test_visibility.py`.
- The skill passes the reference validator and has a summary:
  `tests/skills/test_spec_compliance.py`, `tests/skills/test_catalog.py`.

## Log

- 2026-11-13: phase 1 — the four reads, the nine writes with a backend-answered dry run, the `opik-loop` skill (NA).
- 2026-11-13: Gate increment — the three event triggers and their params, the run, check and notify steps with `only_if`, the `gate` recipe and its overrides, single runs with `item_id`, `outputs`, `waiting_on` and `depth`, the `pending` status, the setup step lifted beside the note, the `integration_required`, `prompt_name_required`, `model_required` and `step_scope_mismatch` codes, the skill's Gate flows (NA).
