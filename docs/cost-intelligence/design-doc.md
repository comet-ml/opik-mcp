# cost-intelligence

## Purpose

Cost intelligence lets an agent answer an organization's Claude Code spend
questions from an AI Spend workspace: who spends the most, what the spend is
made of, and what happened in the most expensive session. This doc says when
the spend types appear, what they answer, and why the guide lives outside the
skills folder.

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

### What the feature adds

Nothing is hidden or removed. The default tools, types, arguments, skills and
project handling work as in any workspace. In an AI Spend workspace the
server adds:

- The spend types, `spend_summary`, `spend_lane`, `spend_user`,
  `spend_session` and `spend_agent`, to the `read` and `list` enums, and their
  `list.spend_*` keys to `schema`.
- One sentence at the front of the `read` and `list` descriptions, saying the
  spend types are available.
- The `cost-intelligence` guide as a `read_skill` name next to the bundled
  skills.
- One paragraph in the instructions: usage is in project `claude-code`,
  dollars come from the spend types ranked by tokens, read the guide first.

### What the spend types answer

Every spend type queries the AI Spend endpoints for project `claude-code`
over a window (`since`/`until`, 30 days by default) and links the matching
AI Spend page. Each answer states its size and labels its dollars as billed or
list price, because the backend uses one field name for both.

| Call | Answers |
|---|---|
| `list('spend_summary')` | Headline figures for the window against the window before |
| `list('spend_lane')` | Where the tokens go: one row per lane, by side |
| `read('spend_lane', <lane>)` | The lane's top items with tokens, dollars and users, plus how many more exist |
| `list('spend_user')` | The leaderboard by total tokens; `filters` on `mcp_server`, `skill` or `built_in_tool` gives who uses one item |
| `list('spend_session')` | Sessions by total tokens, with analysis status and summary |
| `read('spend_session', <id>)` | The session narrative when analysis is ready; otherwise the status and the thread outline call to make |
| `list('spend_agent')` | Subagent usage and how much is attributed |

Spend data needs an organization admin's key; a 403 becomes one sentence
saying so on the first spend call. Opik's own cost fields on traces, threads
and `read('project')` are empty in this workspace, and the instructions and
the guide say not to report them.

### The guide

`read_skill('cost-intelligence')` returns
`src/opik_mcp/cost_intelligence/cost-intelligence.md`: what the lanes mean,
what is logged, how threads and traces map to sessions and turns, how to
outline a session, and recipes for the three questions. It is an unknown
skill in every other workspace.

## How it works

```
list/read → run_list/run_read → FeatureToggles.resolve(settings) → visibility (which types)
          → the entity's handler (entities/spend/*) → client/ai_spend.py → AI Spend endpoints
build_server(settings) → register_tools → feature_surface → toggles.tool_sentences
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
- Which types a feature set shows: `src/opik_mcp/read_list/visibility.py`;
  a handler opts in with `EntityHandler.shown_when`.
- Extending the advertised schemas: `src/opik_mcp/server/tools/feature_surface.py`.
- The spend types: `src/opik_mcp/read_list/entities/spend/`, one module per
  type and a shared `_backend.py`.
- The client: `src/opik_mcp/client/ai_spend.py`.

## Decisions

- The feature is picked by the workspace prefix and the stdio transport, and by
  nothing else, so no other user sees a change and the hosted server cannot
  expose it. A later dual mode changes only `is_cost_intelligence_enabled`.
- Additive, not a second surface. A handler carries `shown_when`, the surface
  gains its names and sentences, and everything else is untouched. Hiding types,
  arguments, `write` or skills would need a refusal and a test on every hidden
  path.
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
- The spend types query `claude-code` themselves. The Opik types take any
  project, as elsewhere; the instructions name the project.
- A figure the backend did not send is absent or `-`, never `0` (ADR 0002's
  silence over false data). `number`/`counted` in `entities/spend/_backend.py`
  carry that; `whole` returns `0` and is for sorting and arithmetic only.
- Neither the lane breakdown nor the who-uses endpoint pages, so the two answers
  that cut say there is no call for the rest and name the page that shows them,
  rather than implying a `page=` that does not exist.
- A trace inside a session narrative is named as the `read('trace', …)` call that
  opens it: a UI link would need the project's UUID, which that path never
  resolves, and a bare id is not something the reader can act on.
- Rankings are by total tokens, which the backend can sort on; dollars are
  shown, not sorted.
- The guide is outside `src/opik_mcp/skills/` on purpose. That folder is
  published as `comet-ml/opik-skills` to all users, and the guide must not
  ship there. It is the one deliberate exception to "skills are authored only
  under `src/opik_mcp/skills/`"; it is never packed or published, so do not
  move it.
- The default byte caps also cover the AI Spend surface, which the added
  sentences and names keep under them (ADR 0001).

### Traps

- The host cuts a tool description at the host's description limit
  (`tests/conformance/test_tool_annotations.py`), so the added sentence goes
  first, not last. `list` stays under it in this workspace; `read` is over it
  on the default surface already.
- `cost_usd` is billed money on the leaderboard and list price on lanes; the
  types label each figure.
- `list('spend_lane')` and `read('spend_lane', key)` come from two backend
  views and can differ by a few percent for the same lane.

## Proven by

- `tests/cost_intelligence/test_features.py`: when the feature turns on, and never
  on hosted HTTP.
- `tests/repo/test_feature_boundary.py`: the registry is a table and every feature
  declares what the framework reads.
- `tests/repo/test_feature_boundary.py`: no generic module imports the feature package.
- `tests/read_list/test_visibility.py`: the default views are unchanged; a
  feature type appears only with its feature.
- `tests/conformance/test_cost_intelligence_surface.py`: the default tools, the
  default schemas plus the added names, the budgets.
- `tests/read_list/test_spend.py`: each spend type's answer, dollar labels,
  filter routing, the admin error, the window default.
- `tests/hermetic/reads/test_cost_intelligence.py`: the real server over
  stdio against a stub backend, one call per spend type and the 403 path.
- `tests/hermetic/test_description_claims.py`: every sentence of a spend
  type's description has a probe.
- `tests/hermetic/test_wheel_contents.py`: the guide ships beside no skill of
  that name.
- `tests/skills/test_cost_intelligence_skills.py`: the guide served in the
  AI Spend workspace only, never as a resource or in the pack.
- `tests/server/test_instructions.py`: the paragraph appears only in the AI
  Spend workspace, before the tool guide.

## Log

- 2026-09-29: spend types, the guide and one instructions paragraph added in AI Spend workspaces; nothing hidden (#237, #238).
