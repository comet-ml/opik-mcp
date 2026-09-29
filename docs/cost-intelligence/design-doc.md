# cost-intelligence

## Purpose

Cost intelligence mode lets an agent read an organization's Claude Code usage
from its Opik records: sessions, turns and tool calls. This doc answers when
the mode turns on, what a host sees in it, and why the guide lives outside the
skills folder.

## What it does now

### When it turns on

The mode is on when the transport is stdio and the workspace name starts with
`__ai_spend_` (`is_cost_intelligence`, `src/opik_mcp/cost_intelligence/__init__.py`).
The hosted HTTP server never enters it, whatever the workspace is called
(`test_the_hosted_transport_never_enters_the_mode`). Every other workspace
keeps the default surface, byte for byte.

### The surface in the mode

- Four tools: `read`, `list`, `schema`, `read_skill`. There is no `write`.
- Visible types: `trace`, `span`, `thread`, `project`, `project_metric`.
  Every other type is refused at call time and left out
  of the advertised enums.
- Every call is confined to one project, `claude-code`. A call with no project
  runs in it; another project name, id or link is refused with "only serves
  the `claude-code` project" (`tests/read_list/test_project_guard.py`).
  `list('project')` shows that one row. A missing `claude-code` project gets
  its own refusal, which names no other project.
- `schema` answers for the visible list types only. `read_skill` serves the
  guide and the `opik` skill; any other skill is refused with what is offered.
- The skills are not registered as MCP resources.
- The instructions are their own short text, not the default one.

### What the types answer

A thread is a session, a trace is a turn (its name starts with the prompt;
`automated…` turns are background calls), and tool spans are named
`tool_use: <Tool>`. Opik's own cost fields on traces, threads and
`read('project')` read $0 in this workspace, and the instructions and the guide
tell the agent not to report them.

### The guide

`read_skill('cost-intelligence')` returns `src/opik_mcp/cost_intelligence/cost-intelligence.md`:
what is logged, how threads and traces map to sessions and turns, and how to
outline a session. It is served only
in this mode and is an unknown skill in the default one.

## How it works

```
list/read → run_list/run_read → mode_of(settings) → visibility (types) + project_scope (project)
          → the entity's own handler → client/ → Opik backend
build_server(settings) → mode_surface.narrow_advertised_schemas → descriptions.py (text)
```

Where to start:

- The mode test and the fixed project: `src/opik_mcp/cost_intelligence/__init__.py`.
- What the host is told (tool and argument text, instructions):
  `src/opik_mcp/cost_intelligence/descriptions.py`.
- Which types each mode shows: `src/opik_mcp/read_list/visibility.py`.
- Narrowing the advertised schemas: `src/opik_mcp/server/tools/mode_surface.py`.
- Project confinement: `src/opik_mcp/read_list/project_scope.py`.

## Decisions

- The mode is picked by the workspace prefix and the stdio transport, and by
  nothing else, so no other user sees a change and the hosted server cannot
  expose this surface.
- One fixed project, refused by name, id, link and fetched record alike. The
  workspace holds one project's data, and an answer from another would be
  taken for it.
- The `claude-code` confinement guides the agent and is not a security boundary: the
  API key can still reach every project in the workspace.
- The surface is narrowed, not a second server: the same tool code runs, and
  the mode changes what is advertised and what is refused. Both surfaces stay
  under the same byte caps (ADR 0001).
- The guide is outside `src/opik_mcp/skills/` on purpose. That folder is
  published as `comet-ml/opik-skills` to all users, and the guide must not
  ship there. It is the one deliberate exception to "skills are authored only
  under `src/opik_mcp/skills/`"; it is never packed or published, so do not
  move it.

### Traps

- A project name given to `list('project')` narrows the listing, so a name that
  is not part of `claude-code` is refused as another project, not reported as
  a missing one.
- A page past the single project row is an empty page, not a missing project.
- Reading a project by a plain name other than `claude-code` is refused before
  any backend call.

## Proven by

- `tests/cost_intelligence/test_mode.py`: when the mode turns on, and never on
  hosted HTTP.
- `tests/conformance/test_cost_intelligence_surface.py`: the four tools, the
  visible enums, the narrowed schemas, the budgets, the schema snapshot.
- `tests/read_list/test_project_guard.py`: one project by name, id, link and
  fetched record; a project listing; a missing project.
- `tests/hermetic/reads/test_cost_intelligence.py`: the real server over stdio
  against a stub backend: `test_the_mode_offers_only_the_read_tools`,
  `test_a_hidden_type_and_another_project_are_refused`,
  `test_the_guide_is_served_and_other_skills_are_not_offered`,
  `test_the_default_workspace_has_no_guide`.
- `tests/hermetic/test_wheel_contents.py`:
  `test_the_cost_intelligence_guide_ships_beside_no_skill_of_that_name`.
- `tests/skills/test_cost_intelligence_skills.py`: the guide served in the
  mode only, hidden skills refused, no resources.
- `tests/server/test_instructions.py`: the mode's instructions name only its
  tools and the guide.

## Log

- 2026-09-29: cost intelligence mode for a local server on an AI Spend workspace: the Opik types trace, span, thread, project and project_metric, one fixed project, its own guide and instructions.
