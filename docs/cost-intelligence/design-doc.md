# cost-intelligence

## Purpose

A local server pointed at an AI Spend workspace can answer questions about an
organization's Claude Code usage. This doc answers when that turns on, what it
adds to what a host sees, and why the guide lives outside the skills folder.

## What it does now

### When it turns on

The AI Spend feature is on when the transport is stdio and the workspace name
starts with `__ai_spend_`. That predicate is the feature's own
(`is_cost_intelligence_enabled` in `src/opik_mcp/cost_intelligence/__init__.py`);
the toggle config calls it once at startup and stores the answer as a named
boolean, `FeatureToggles.cost_intelligence_enabled`. The hosted HTTP server never
turns it on, whatever the workspace is called
(`test_the_hosted_transport_never_turns_the_feature_on`). Every other workspace
keeps the default surface, byte for byte.

### What it adds

Nothing is hidden or replaced: the default tools, every argument, every skill and
the project handling are the default ones. The feature only adds:

- Entity types whose handler sets `feature` to the AI Spend feature, in the
  `entity_type` enums of `read` and `list`, and their `list.<type>` keys in the
  `schema` operation enum. A workspace without the feature refuses such a type
  with the same wording as an unknown one.
- One sentence at the front of the `read` and `list` descriptions. The
  `read_skill` description stays as it is, because the host cuts a description
  at a limit it already fills (`tests/conformance/test_tool_annotations.py`);
  the instructions name the guide instead.
- The `cost-intelligence` guide, as an extra name `read_skill` serves beside
  every bundled skill.
- One paragraph in the `initialize` instructions, before `Tool selection:`.

The skill resources stay installed on stdio, and never carry the guide.

### The guide

`read_skill('cost-intelligence')` returns `src/opik_mcp/cost_intelligence/cost-intelligence.md`:
what is logged, how threads and traces map to sessions and turns, and how to
outline a session. It is an unknown skill without the feature.

## How it works

```
list/read → run_list/run_read → FeatureToggles.resolve(settings) → visibility (which types)
          → the entity's own handler → client/ → Opik backend
register_tools → feature_surface.extend_advertised_schemas → toggles.tool_sentences
instructions → toggles.instructions_paragraphs; read_skill → toggles.extra_skills
```

A feature contributes through accessors on the toggle config. There is no "what a
feature adds" contract: a toggle touches the concerns it happens to touch, so a
later one that has nothing to do with tools or skills adds a boolean and nothing
else.

Where to start:

- The toggle config: `FeatureToggles` in `src/opik_mcp/features/toggles.py` —
  one named boolean per feature, resolved once by `FeatureToggles.resolve`, plus
  one accessor per concern for what the on toggles contribute. Shaped after
  opik-backend's `ServiceTogglesConfig`, whose matching toggle is
  `serviceToggles.costIntelligenceEnabled`.
- What turns this feature on: `is_cost_intelligence_enabled` in
  `src/opik_mcp/cost_intelligence/__init__.py`. `config.py` names no feature.
- What this feature contributes: `src/opik_mcp/cost_intelligence/feature.py`.

- Which types a feature set shows: `src/opik_mcp/read_list/visibility.py`.
- Extending the advertised schemas: `src/opik_mcp/server/tools/feature_surface.py`.

## Decisions

- The feature is picked by the workspace prefix and the stdio transport, and by
  nothing else, so no other user sees a change and the hosted server cannot
  expose it. A later dual mode changes only `is_cost_intelligence_enabled`.
- The toggle is resolved once, from settings, and has no environment variable of
  its own: the workspace the caller already points at is what selects it.
- A feature owns its own name and its own predicate. A root module that spells
  either one out is a finding
  (`test_no_root_module_spells_a_feature_name_out`), because the import guard
  above cannot see a name that is never imported.
- Feature specifics reach the framework only through the toggle config, as
  entities do through the entity registry (`tests/repo/test_feature_boundary.py`).
- An entity behind a toggle declares `shown_when`, a predicate over the toggles,
  the way it already declares `fetch_fn` and `list_fn`. No feature name is matched.
- The surface is extended, never narrowed: the same tool code runs, so the
  default surface and its byte budgets stay as they are (ADR 0001). What the
  workspace pays is measured in `tests/conformance/test_cost_intelligence_surface.py`.
- The guide is outside `src/opik_mcp/skills/` on purpose. That folder is
  published as `comet-ml/opik-skills` to all users, and the guide must not
  ship there. It is the one deliberate exception to "skills are authored only
  under `src/opik_mcp/skills/`"; it is never packed or published, so do not
  move it.

## Proven by

- `tests/cost_intelligence/test_features.py`: when the feature turns on, and never
  on hosted HTTP.
- `tests/repo/test_feature_boundary.py`: the registry is a table and every feature
  declares what the framework reads.
- `tests/repo/test_feature_boundary.py`: no generic module imports the feature package.
- `tests/read_list/test_visibility.py`: the default views, the types a feature
  adds, and the refusal of a feature type without it.
- `tests/conformance/test_cost_intelligence_surface.py`: the default tools,
  the enums as default plus additions, the sentences, the budgets.
- `tests/hermetic/reads/test_cost_intelligence.py`: the real server over stdio
  against a stub backend: `test_the_workspace_still_offers_every_default_tool`,
  `test_the_guide_is_served_beside_every_bundled_skill`,
  `test_the_default_workspace_has_no_guide`.
- `tests/hermetic/test_wheel_contents.py`:
  `test_the_cost_intelligence_guide_ships_beside_no_skill_of_that_name`.
- `tests/skills/test_cost_intelligence_skills.py`: the guide served with the
  feature only, every bundled skill still served, no resource for the guide.
- `tests/server/test_instructions.py`: the paragraph in a spend workspace only.

## Log

- 2026-09-29: AI Spend feature for a local server on an AI Spend workspace: entity types behind a handler `feature`, one sentence on `read` and `list`, the guide as an extra skill, one instructions paragraph (#237).
