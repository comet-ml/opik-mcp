# cost-intelligence

## Purpose

A local server pointed at an AI Spend workspace can answer questions about an
organization's Claude Code usage. This doc answers when that turns on, what it
adds to what a host sees, and why the guide lives outside the skills folder.

## What it does now

### When it turns on

The AI Spend feature is on when the transport is stdio and the workspace name
starts with `__ai_spend_` (`Settings.features`, resolved once in
`src/opik_mcp/config.py`). The hosted HTTP server never
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
list/read → run_list/run_read → settings.features → visibility (which types)
          → the entity's own handler → client/ → Opik backend
register_tools → feature_surface.extend_advertised_schemas → features/registry.py
instructions, read_skill → features/registry.py (paragraph, guide)
```

A feature is one `Feature` value in `src/opik_mcp/cost_intelligence/feature.py`
(sentences, paragraph, guide loader), listed in the feature registry.

Where to start:

- The toggle: `Settings.features` in `src/opik_mcp/config.py`.
- The feature registry: `src/opik_mcp/features/registry.py`.
- The added sentences, paragraph and guide: `src/opik_mcp/cost_intelligence/feature.py`.
- Which types a feature set shows: `src/opik_mcp/read_list/visibility.py`.
- Extending the advertised schemas: `src/opik_mcp/server/tools/feature_surface.py`.

## Decisions

- The feature is picked by the workspace prefix and the stdio transport, and by
  nothing else, so no other user sees a change and the hosted server cannot
  expose it. A later dual mode changes only `Settings.features`.
- The toggle is resolved once, in settings, and has no environment variable.
- Feature specifics reach the framework only through the feature registry, as
  entities do through the entity registry (`tests/repo/test_feature_boundary.py`).
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
- `tests/features/test_registry.py`: the registry is a table and every feature
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
