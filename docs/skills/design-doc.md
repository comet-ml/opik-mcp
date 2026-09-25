# skills

## Purpose

This repo authors the Opik agent skills. The server serves them through the
`read_skill` tool and as MCP resources, and CI publishes the same bytes as the
pack that `npx skills add comet-ml/opik-skills` installs. This doc answers how
an agent gets a skill, how the pack is built, and how to add a skill.

## What it does now

### The tree

- A skill is a directory under `src/opik_mcp/skills/` that holds a `SKILL.md`.
  Its `name` is `opik` or starts with `opik-`, and matches the directory.
- `opik` is the SDK reference. Each `opik-<verb>` skill does one task. Rules for
  the frontmatter `description` are in `.claude/rules/skills.md`.
- Only this tree is served (`_skills_root`) and packed (`DEFAULT_SRC`). A skill
  authored elsewhere is in neither, but can still ship; see Traps.
- `evals/` inside a skill is run by hand (see its `HARNESS.md`) and never
  ships. Exclusion is one rule, `EXCLUDED_DIRS` plus dotfiles, in
  `src/opik_mcp/skills_catalog.py`; the pack builder imports it. The wheel
  rule is in [release](../release/design-doc.md).

### read_skill

`read_skill(skill_name)` returns one document. `resolve` accepts a skill name
(`opik-instrument`, meaning its `SKILL.md`), a path (`opik/references/tracing-python.md`)
or a URI (`opik://skills/opik/SKILL.md`). These three are advertised. It also
tolerates a sibling path as a `SKILL.md` cites it (`../opik/references/integrations.md`)
and a bare reference name: `opik/tracing-python` drops the `references/` folder
and the `.md` suffix, and resolves to `opik/references/tracing-python.md`.

The answer is a header, then the file byte for byte:

```
[read_skill: opik path=SKILL.md bytes=<n> uri=opik://skills/opik/SKILL.md]
```

The header names the resolved file, whatever form the caller used. A
`SKILL.md` with references ends with a footer listing their paths.

An unknown skill raises `UnknownSkillError` (kind `validation`) listing every
skill; an unknown document lists that skill's documents, in the caller's form.

`read_skill_tool_description` renders the tool description: a routing line
per skill from `SKILL_SUMMARIES`, the accepted forms, every readable path.

### Resources

Each served file is a resource at `opik://skills/<skill>/<path>`, with one
template. List and read results carry `ttlMs` (`SKILLS_TTL_MS`) and
`cacheScope` (`SKILLS_CACHE_SCOPE`, `public`) on the result and in each
content's `_meta`. An unknown `opik://skills/` URI is an error listing what exists.

### The pack

`make skills-pack` builds `dist/opik-skills/` with `README.md` and `index.json`.
It copies files byte for byte and validates each skill with `skills_ref` (the
spec's reference implementation) before and after. The build fails on an empty
source, an invalid skill, or a `SKILL.md` path that dangles or escapes the pack.

In CI the `skills-pack` job runs `make skills-verify` and
`make skills-verify-source`. On a push to `main`, `publish-skills-pack` uploads
the verified artifact to the pre-release tag `skills-pack`, where
`comet-ml/opik-skills` pulls it. This repo holds no credentials for that repo.

### Adding a skill

1. Create `src/opik_mcp/skills/opik-<verb>/SKILL.md` with `last_updated` and
   `source_commit` under `metadata:`. `tests/skills/test_spec_compliance.py`
   checks the name and the provenance.
2. Add a line to `SKILL_SUMMARIES`, or the skill is missing from the routing
   list and `test_every_bundled_skill_has_a_summary` fails.
3. The budget tests fail only if the new lines push a total over its cap:
   `test_tool_description_stays_within_a_sane_budget`, `SURFACE_BUDGET_BYTES`
   and `INSTRUCTIONS_BUDGET_BYTES`. Trim the summary, or raise the budget and
   record why in the comment next to it (AGENTS.md).
4. Run `scripts/skills_trigger_eval.py` (needs a judge API key, not in CI) and
   `make skills-verify-source`.
5. Restart the server to see the new files. The listing is cached per process,
   and hosts may keep the old set for `SKILLS_TTL_MS`.

## How it works

```
read_skill(name)  -> server.read_skill -> skills_catalog.run_read_skill
                     -> resolve -> read_skill_file (importlib.resources)
resources/*       -> skills_resources handlers -> skills_catalog.resolve_uri
                     -> read_skill_file
make skills-pack  -> scripts/build_skills_pack.py -> dist/opik-skills/
```

- Resolution, errors, the answer and the description: start in
  `src/opik_mcp/skills_catalog.py`, the only runtime reader of the tree.
- Resource handlers and cache metadata: `install_skill_resources` in
  `src/opik_mcp/skills_resources.py`. HTTP installs it in `build_app`, stdio in
  `_run_transport` in `src/opik_mcp/__main__.py`.
- The pack: `scripts/build_skills_pack.py` and the `skills-*` targets in
  `Makefile`. `tests/hermetic/test_stdio_session.py` checks the forms over stdio.
- Budgets: [tool-surface](../tool-surface/design-doc.md). Analytics:
  [analytics](../analytics/design-doc.md). Evaluation reads: [experiment-flows](../experiment-flows/design-doc.md).

## Decisions

- One source for the MCP and the pack, so a skill read over MCP matches the
  installed one byte for byte (OPIK-7471, #163).
- Resources let a host cache the tree. The tool exists because many hosts
  show resources to the user and never to the model (#175).
- `skill_name` has no `enum` because it accepts paths and URIs; an enum would
  reject valid calls at the host's schema check (#175).
- The description lists every readable path so an agent can fetch a reference
  without reading the `SKILL.md` first. That costs context in every session
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- Names resolve by lookup in the enumerated file set, never by a path join, so
  `..` and absolute paths find nothing. `read_skill_file` rechecks the set.
- Exclusion is by rule, so a new `scripts/` folder ships without editing an
  allow-list. `evals/` stays out because the pack installs with `--all` and
  fixtures would land on every user's machine (#176).
- `content_digest` leaves out `pack_version` and `source_commit`, so a merge
  that changes no skill opens no pull request in the consumer (#163).
- The `opik-` prefix exists because the installer overwrites a same-named
  skill without asking (#171).

### Traps

- `SKILL_SUMMARIES` may differ from the frontmatter on purpose. No test ties
  them; see the comment in `tests/skills/test_catalog.py` for why.
- The `read_skill` description is over the host cut-off, so some hosts drop
  the tail, which is the path inventory (`test_the_description_arrives_whole`).
- `install_skill_resources` catches only a missing `_mcp_server`; other errors
  while installing raise. A second install is a no-op.
- `npx skills add` run against this repo resolves `.claude/skills/` and
  `.agents/skills/` first, so a skill committed there, or at any other path the
  installer searches, ships instead of the authored ones. `.gitignore`,
  `.claude/hooks/protect_paths.py` and deny rules block the usual ways in, but
  not `cp` or a script. `make skills-verify-source` compares the installed
  names with `src/opik_mcp/skills/` and fails CI on any difference.

## Proven by

- Resolution, errors, header and footer, ordering, the description and the
  summaries: `tests/skills/test_catalog.py`.
- Resources over a real session, and over stdio:
  `tests/conformance/test_skill_resources.py`, `tests/hermetic/test_stdio_session.py`.
- The pack is verbatim, excludes evals, fails on bad references and has a
  stable digest: `tests/skills/test_pack_build.py`. Each skill passes the
  reference validator: `tests/skills/test_spec_compliance.py`.
- The hook blocks writes to the skill folders: `tests/repo/test_agent_hooks.py`.
- The installer reproduces the pack and finds only the authored skills:
  `make skills-verify`, `make skills-verify-source`.

## Log

- 2026-09-24: a hook and deny rules block writes to `.claude/skills/` and `.agents/skills/`, which would ship to users (#202).
- 2026-09-23: skill `evals/` excluded from the wheel so eval fixtures do not ship (#176).
- 2026-09-21: quality-drop questions routed to `opik-compare` (#198).
- 2026-09-01: skills served as MCP resources and through `read_skill`, so a host without the pack can read them (#175).
- 2026-08-28: skills renamed with the `opik-` prefix, so the installer cannot overwrite another skill (#171).
- 2026-08-19: pack generated from this repo and published for `comet-ml/opik-skills` (#163).
