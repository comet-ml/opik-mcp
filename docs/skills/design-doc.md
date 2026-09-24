# skills

## Purpose

This repo authors the Opik agent skills. The server serves them to a connected
agent through the `read_skill` tool and as MCP resources, and CI publishes the
same files as the pack that `npx skills add comet-ml/opik-skills` installs.
Open this doc to find out how an agent gets a skill, how the pack is built and
published, or what stops a skill from shipping from the wrong place.

## What it does now

### The skills

Each skill is a directory under `src/opik_mcp/skills/` that holds a `SKILL.md`.
A directory without one is ignored by both the server and the pack builder
(`src/opik_mcp/skills_catalog.py` `iter_skill_files`,
`scripts/build_skills_pack.py` `_discover_skills`). The current set is
whatever that directory holds. The tool description and the `initialize`
instructions list the names, rendered from the same tree.

`opik` is a reference for the Opik SDK (tracing, integrations, threads, the
prompt library). It needs no connection and carries most of the `references/`
documents. The `opik-<verb>` skills each do one task:

- `opik-instrument` adds tracing to an app and checks that a real trace lands.
- `opik-evaluate` builds an evaluation and runs it, returning an experiment.
- `opik-diagnose` ranks the traces worth attention, Diagnostics issues first.
- `opik-explain` root-causes one trace or a pattern across traces.
- `opik-test` turns a failing trace into a test-suite item.
- `opik-compare` runs a candidate against a baseline and reports deltas.
- `opik-verify` gives a ship or hold verdict against a release policy.
- `opik-online-eval` creates an online evaluation rule on a project.
- `opik-optimize` improves a prompt with the Opik Agent Optimizer.

Every task skill's `SKILL.md` uses the same headings: Inputs, Activation,
Blockers, Output, Examples, Anti-patterns, References. Frontmatter carries
`name`, `description`, `compatibility`, `allowed-tools` and a `metadata` block
with `last_updated` and `source_commit`. The description is the trigger: it
lists the user phrasings the skill owns and names the neighbouring skill for
the phrasings it does not (`.claude/rules/skills.md`).

Next to `SKILL.md`, a skill may hold `references/` (documents the skill tells
the agent to open) and `evals/`. An `evals/` folder has `cases.yaml` for
trigger and functional cases, fixture apps with seed scripts, a grader,
`metrics.py`, `run_evals.py` and a `HARNESS.md` that says how to run it
(for example `src/opik_mcp/skills/opik-compare/evals/HARNESS.md`). Evals are
run by hand and never ship: the server, the pack and the wheel all leave them
out.

### read_skill

`read_skill(skill_name)` returns one document. `skill_name` accepts these forms
(`src/opik_mcp/skills_catalog.py` `resolve`):

- a skill name, `opik-instrument`, which returns its `SKILL.md`;
- a path inside a skill, `opik/references/tracing-python.md`;
- a resource URI, `opik://skills/opik/SKILL.md`;
- a sibling path as a `SKILL.md` writes it, `../opik/references/integrations.md`
  (leading `../` is stripped);
- a bare reference name, `opik/tracing-python`, where the `references/`
  folder and the `.md` suffix are dropped. The description does not advertise
  this form; the resolver tolerates it.

Resolving a reference file goes like this. The resolver trims whitespace and
slashes, strips any leading `../` and the `opik://skills/` prefix, and splits
the rest into skill and relative path. An unknown skill raises
`UnknownSkillError` listing every skill name. A known skill with no path means
`SKILL.md`. The pair is then looked up as a URI in the enumerated file set. If
that misses, the relative path is tried as a bare reference name. If that also
misses, the error lists that skill's readable documents, spelled as URIs when
the caller used a URI and as paths otherwise. The resolver never joins the
caller's string onto a directory, so `..` and absolute paths find nothing
and cannot leave the tree. `read_skill_file` checks the entry against the
enumerated set again before it touches the disk.

`UnknownSkillError` has error kind `validation` and HTTP status 400
(`src/opik_mcp/skills_catalog.py` `UnknownSkillError`).

The answer (`run_read_skill`) is a one-line header, then the file byte for
byte:

```
[read_skill: opik path=SKILL.md bytes=<n> uri=opik://skills/opik/SKILL.md]
```

The header names the resolved file whatever form the caller used. When the
answer is a `SKILL.md` and the skill has references, a footer lists their
paths in the form `read_skill` accepts. A single reference read has no footer.

The tool description is rendered from the tree
(`read_skill_tool_description`). It tells the agent to fetch a skill only when
it is not already in context, and gives one routing line per skill from
`SKILL_SUMMARIES`, keyed on what a user says ("did my fix work", "why did
quality drop" route to `opik-compare`). It lists the accepted forms and every
readable path. `SKILL_SUMMARIES` is MCP-only copy and may differ from the
frontmatter descriptions. The `skill_name` parameter has no `enum`, because it
accepts paths and URIs as well as names (comment above `read_skill` in
`src/opik_mcp/server.py`).

The tool is annotated read-only. Its analytics labels come from
`_read_skill_props` in `src/opik_mcp/server.py`; see
[analytics](../analytics/design-doc.md).

### Skills as MCP resources

The same files are served over `resources/list`, `resources/read` and
`resources/templates/list` (`src/opik_mcp/skills_resources.py`
`install_skill_resources`). Each file is one resource at
`opik://skills/<skill>/<path>`, with a MIME type chosen by suffix
(`text/markdown` for Markdown). One template, `opik://skills/{skill}/{path}`,
describes the URI shape.

Listings and reads carry cache metadata, `ttlMs` of one day and `cacheScope`
`public`, both on the result and in each content's `_meta`. Skill content is
the same for every caller and holds no workspace data, so a host may share one
cached copy (`SKILLS_TTL_MS`, `SKILLS_CACHE_SCOPE`). The installed SDK does not
declare these fields, so they travel as model extras.

A read of an `opik://skills/` URI that names nothing returns an error listing
the skill's URIs, or every skill name and a pointer to `resources/list`
(`unknown_skill_uri_message`). URIs outside that prefix go to FastMCP's own
handlers.

The listing is sorted by skill name, `SKILL.md` first, then path, and is cached
for the life of the process (`iter_skill_files`).

The resources exist because hosts that support them can cache the tree. The
tool exists because many hosts show resources to the user and never to the
model (comment above `read_skill` in `src/opik_mcp/server.py`).

### What is served and what is published

Both the server and the pack skip any directory named in
`EXCLUDED_DIRS` (`evals`, `__pycache__`) and any dotfile. The pack builder
imports `EXCLUDED_DIRS` from `skills_catalog`, so the two cannot disagree. The
wheel build excludes `src/opik_mcp/skills/*/evals` in `pyproject.toml`.

### The published pack

`scripts/build_skills_pack.py` builds `dist/opik-skills/` (`make skills-pack`):

- It validates every source skill with `skills_ref`, the agentskills.io
  reference implementation, and fails if a `name` differs from its directory.
- It copies each skill byte for byte, `SKILL.md` first, and never rewrites
  frontmatter.
- It validates each emitted skill again after copying.
- It checks that every Markdown path a `SKILL.md` links or backticks resolves
  inside the pack. Cross-skill paths such as `../opik/references/…` are
  allowed. A dangling path or one that escapes the pack fails the build.
- It renders `README.md` from `scripts/skills_pack_readme.md.tmpl` with a
  skills table and a tree that lists skills only, so renaming a reference does
  not change the README.
- It writes `index.json` with `schema_version`, `pack_version`, `source`,
  `source_commit`, `content_digest` and per-file SHA-256 checksums.

`content_digest` covers the skill files and the README, and excludes
`pack_version` and `source_commit`. The consumer compares the digest to decide
whether to open a pull request, so a merge that changes no skill produces no
pull request. An empty source or an invalid skill aborts the build. The output
directory is replaced on each build, so a deleted skill disappears.

In CI (`.github/workflows/ci.yaml`), the `skills-pack` job runs on every PR and
push. It runs `make skills-verify`, which builds the pack and installs it with
the pinned `skills` CLI the way the product's onboarding does (`-g --all`) into
a throwaway `HOME`, then diffs the installed tree against the pack. It then
runs `make skills-verify-source`, packs a reproducible tarball and uploads it
with `index.json` as an artifact. On a push to `main`, `publish-skills-pack`
needs `python-checks` and `skills-pack`. It downloads that artifact and
uploads it to a GitHub pre-release with the fixed tag `skills-pack`, replacing
the previous assets. `comet-ml/opik-skills` pulls from that release; this repo
holds no credentials for it.

Unverified: how often `comet-ml/opik-skills` pulls. The CI comment says "on a
schedule"; that repo's workflow is not in this one.

### Keeping skills in one place

`npx skills add` resolves skills from `.claude/skills/` and `.agents/skills/`
before anything else. A skill committed there would install instead of the
authored ones. Four things guard against it:

- `.gitignore` ignores both directories and `skills-lock.json`.
- `.claude/hooks/protect_paths.py`, a PreToolUse hook, blocks Claude Code's
  Write, Edit and NotebookEdit tools there with exit code 2. The match ignores
  case and uses the worktree's own root. `.claude/settings.json` also denies
  `Edit` on both paths. Neither stops `cp` or a script.
- `.claude-plugin/marketplace.json` names `./src/opik_mcp` as the plugin root,
  so the installer run against this repo finds the authored skills directly.
- `make skills-verify-source` runs the pinned installer against the repo
  checkout into a throwaway `HOME` and compares the skill names it installed
  with the directories under `src/opik_mcp/skills/` that hold a `SKILL.md`.
  Any difference fails the `skills-pack` job, and `publish-skills-pack` then
  does not run.

That last check is the one that fails CI when a skill is authored somewhere
else. A skill committed under `.claude/skills/` (with `git add -f`) or any
other path the installer searches shows up in the installed set and not in the
authored set.

### Naming and provenance

A skill's name is `opik` or starts with `opik-`, and matches its directory.
The installer overwrites a same-named skill on the user's machine without
asking, so an unprefixed name could replace someone else's skill
(`tests/test_skills_spec_compliance.py`
`test_skill_name_is_namespaced_to_opik`). Provenance (`last_updated`,
`source_commit`) sits under `metadata:`, because spec readers drop unknown
top-level keys.

### Development scripts

- `scripts/skills_trigger_eval.py` gives an LLM judge every skill's name and
  frontmatter description, then asks it to route each phrase from every
  `evals/cases.yaml`. It reports misroutes and phrases two skills both claim.
  It needs a judge API key, is not run in CI, and is meant to be run when a
  description changes.
- `scripts/smoke_skills_mcp.py` starts `python -m opik_mcp` over stdio, lists
  the skill resources, reads one, and calls `read_skill` in each documented
  form. It needs no Opik credentials.

Each skill's own `evals/run_evals.py` has its own `prepare` and `grade`
steps; there is no shared runner.

Unverified: a ticket to unify the per-skill harnesses is filed (OPIK_8494).

## How it works

```
read_skill(name)  -> server.read_skill -> skills_catalog.run_read_skill
                     -> resolve -> read_skill_file -> importlib.resources
resources/*       -> skills_resources handlers -> skills_catalog.resolve_uri
                     -> read_skill_file
make skills-pack  -> scripts/build_skills_pack.py -> dist/opik-skills/
```

- `src/opik_mcp/skills_catalog.py` is the only runtime reader of the skills
  tree. It enumerates files, maps them to URIs, resolves names, reads
  content, and renders the tool description. It reads through
  `importlib.resources`, so it works from an installed wheel.
- `src/opik_mcp/skills_resources.py` `install_skill_resources` replaces the
  low-level `resources/*` handlers in place. Each new handler delegates to the
  one it replaced for anything outside `opik://skills/`. A marker on the
  handlers makes a second install a no-op. A failure logs and leaves the
  original handlers, so the tools still work.
- The HTTP app installs the resources in `build_app`; the stdio path installs
  them in `src/opik_mcp/__main__.py` `_run_transport`, since `build_app` never
  runs there.
- The `read_skill` registration and `_read_skill_props` are in
  `src/opik_mcp/server.py`. The `read_skill` line in the `initialize`
  instructions (`src/opik_mcp/instructions.py`) takes its skill names from
  `skill_names()`. The instructions as a whole, the tool inventory and the
  surface byte budget belong to [tool-surface](../tool-surface/design-doc.md).
- The pack's CI jobs run beside the release pipeline and do not feed it; see
  [release](../release/design-doc.md).
- Running an evaluation is a skill's job, and the reads it relies on are
  [experiment-flows](../experiment-flows/design-doc.md).
  `opik-diagnose` starts from [diagnostics](../diagnostics/design-doc.md).

## Decisions

- Skills are authored only in `src/opik_mcp/skills/`, and the pack and the
  MCP serve the same bytes, so a skill read over MCP matches the installed one
  (#163, OPIK_7471).
- One tool, `read_skill`, joins `read`, `list`, `write` and `schema`
  ([ADR 0003](../decisions/0003-five-tool-surface.md), #175). Its description
  lists every readable path, which costs context in every session but lets an
  agent fetch a reference without first reading the `SKILL.md`
  ([ADR 0001](../decisions/0001-context-budget-first.md)).
- Skills are also MCP resources with public, day-long cache metadata, because
  the content is static per build and the same for every caller (#175).
- Names resolve by lookup in the enumerated set with no path join, so there
  is no sanitiser to get wrong (`skills_catalog.py` docstring).
- Exclusion is by rule (`EXCLUDED_DIRS`, dotfiles), so a new `scripts/` or
  `assets/` folder ships without anyone editing an allow-list.
- `evals/` stays out of the wheel, the pack and the MCP. The pack installs
  globally with `--all`, so fixtures would land on every user's machine. The
  wheel rule was added when a check found the next release would have
  shipped them (#176).
- Frontmatter is validated and read with `skills_ref`, the spec's reference
  implementation, so the build reads a skill the same way an agent's tooling does
  (#163).
- `content_digest` leaves out build identity so the consumer opens no empty
  pull requests (#163).
- The pack is published as a rolling pre-release on a fixed tag, which gives
  consumers a stable URL, and the published bytes are the ones the
  `skills-pack` job verified (#163).
- Routing between skills lives in `SKILL_SUMMARIES` and the descriptions. A
  skill never describes another skill (`.claude/rules/skills.md`).
  Quality-drop questions route to `opik-compare` (#198).
- No test ties `SKILL_SUMMARIES` to the frontmatter. Two drift guards were
  tried and removed because they either forbade the intended wording
  differences or passed by accident (comment in
  `tests/test_skills_catalog.py`).

## Proven by

- `tests/test_skills_catalog.py`: deterministic listing with the entry point
  first, excluded folders never served, verbatim content, every accepted form
  reaching the same document, errors naming the alternatives in the caller's
  form, the header and footer, a hand-built entry outside the tree refused,
  and the tool description listing every skill and path within its budget.
- `tests/conformance/test_skill_resources.py`: over a real session, every
  skill file is listed and reads back verbatim, cache metadata arrives on list
  and read, the template is advertised, an unknown URI is an error, and
  excluded folders are unreachable.
- `tests/e2e/test_stdio_session.py`: `test_the_stdio_path_installs_the_skill_resources`
  and `test_a_skill_reads_back_over_the_wire` prove the stdio path serves
  skills.
- `tests/conformance/test_tool_annotations.py`:
  `test_the_description_arrives_whole` marks `read_skill` as a strict expected
  failure while its description is over the host's cut-off.
- `tests/test_skills_spec_compliance.py`: every skill passes the reference
  validator, its name matches its directory and carries the `opik` prefix, and
  provenance survives the reference reader.
- `tests/test_skills_pack_build.py`: against fixture trees, the pack is
  verbatim, excludes evals and dotfiles, validates emitted skills, fails on
  dangling or escaping references, and is deterministic. Its digest ignores
  build identity, and a rebuild drops removed skills.
- `tests/test_skills_packaged.py`: the skills directory resolves as a package
  resource.
- `tests/e2e/test_wheel_contents.py`: the built wheel holds every served
  document and nothing the server would not serve, no excluded folder, and
  serves skills after a real install.
- `tests/test_agent_hooks.py`: `test_protect_blocks_with_a_reason`,
  `test_protect_ignores_letter_case`,
  `test_protect_judges_a_worktree_by_its_own_root` and
  `test_the_configured_pre_hook_blocks_a_skill_write` prove the hook blocks
  writes to the skill folders.
- `make skills-verify` and `make skills-verify-source` in the `skills-pack` CI
  job prove the installer reproduces the pack and resolves exactly the
  authored skills from the repo.

## Log

- 2026-09-24: hook and `Edit` deny rules added to block writes to `.claude/skills/` and `.agents/skills/` (#202).
- 2026-09-23: `opik-instrument` checks span coverage and warns about common ingestion traps (#173).
- 2026-09-23: skill `evals/` excluded from the wheel, with a test on the built wheel (#176).
- 2026-09-21: eval fixtures for `opik-evaluate`, `opik-online-eval` and `opik-optimize`, and the fleet trigger eval (#200).
- 2026-09-21: `opik-verify` added (#193).
- 2026-09-21: quality-drop questions routed to `opik-compare`; descriptions made to match behaviour (#198).
- 2026-09-17: `opik-test`, `opik-compare`, `opik-online-eval` and `opik-optimize` added; `opik-evaluate` made task-shaped (#191).
- 2026-09-01: skills served over MCP as resources and through `read_skill` (#175).
- 2026-08-28: `opik-diagnose` and `opik-explain` added (#167, #168); published skills renamed with the `opik-` prefix.
- 2026-08-25: the instrument skill added (#156).
- 2026-08-19: pack generated from this repo and published for `comet-ml/opik-skills` (#163).
