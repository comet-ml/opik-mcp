# cost-intelligence

## Purpose

Cost intelligence lets an agent answer an organization's Claude Code spend
questions from an AI Spend workspace: who spends the most, what the spend is
made of, and what happened in the most expensive session. This doc says when
the spend types appear, what they answer, and why the guide lives outside the
skills folder.

## What it does now

### When it turns on

The spend feature is on when the transport is stdio and the workspace name
starts with `__ai_spend_` (`is_cost_intelligence` and `enabled_features`,
`src/opik_mcp/cost_intelligence/__init__.py`). The hosted HTTP server never
turns it on, whatever the workspace is called. Every other workspace keeps the
default surface, byte for byte.

### What the feature adds

Nothing is hidden or removed. The default tools, types, arguments, skills and
project handling work as in any workspace. In an AI Spend workspace the
server adds:

- Five entity types, `spend_summary`, `spend_lane`, `spend_user`,
  `spend_session` and `spend_agent`, to the `read` and `list` enums, and their
  `list.spend_*` keys to `schema`.
- One sentence at the front of the `read`, `list` and `read_skill`
  descriptions, saying the spend types and the guide are available.
- The `cost-intelligence` guide as a `read_skill` name next to the bundled
  skills.
- One paragraph in the instructions: usage is in project `claude-code`,
  dollars come only from the spend types, rank by tokens, read the guide first.

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
list/read → run_list/run_read → enabled_features(settings) → visibility (types)
          → the entity's handler (entities/spend/*) → client/ai_spend.py → AI Spend endpoints
build_server(settings) → register_tools → feature_surface.extend_advertised_schemas
instructions.py → {ai_spend_clause}; skills_catalog.run_read_skill(name, features)
```

Where to start:

- The switch: `src/opik_mcp/cost_intelligence/__init__.py`.
- The added sentences and paragraph: `src/opik_mcp/cost_intelligence/descriptions.py`.
- Which types a feature set shows: `src/opik_mcp/read_list/visibility.py`;
  a handler opts in with `EntityHandler.feature`.
- Extending the advertised schemas: `src/opik_mcp/server/tools/feature_surface.py`.
- The spend types: `src/opik_mcp/read_list/entities/spend/`, one module per
  type and a shared `_backend.py`.
- The client: `src/opik_mcp/client/ai_spend.py`.

## Decisions

- The feature is picked by the workspace prefix and the stdio transport, and
  by nothing else, so no other user sees a change and the hosted server cannot
  expose it.
- Additive, not a second surface. An earlier design hid the Opik types,
  arguments, `write` and skills and pinned every call to `claude-code`; that
  needed a refusal and a test on every hidden path and would block a later
  dual mode. Now a handler carries a `feature` and everything else is untouched.
- The spend types query `claude-code` themselves. The Opik types take any
  project, as elsewhere; the instructions name the project.
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

- The host cuts each tool description at 2,048 characters, so the added
  sentence goes first, not last.
- `cost_usd` is billed money on the leaderboard and list price on lanes; the
  types label each figure.
- `list('spend_lane')` and `read('spend_lane', key)` come from two backend
  views and can differ by a few percent for the same lane.

## Proven by

- `tests/cost_intelligence/test_mode.py`: when the feature turns on, and never
  on hosted HTTP.
- `tests/read_list/test_visibility.py`: the default views are unchanged; a
  feature type appears only with its feature.
- `tests/conformance/test_cost_intelligence_surface.py`: the five tools, the
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
