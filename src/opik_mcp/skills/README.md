# Opik coding-agent skills — canonical home

This directory is the **single source of truth** for the shared Opik coding-agent
skills. It replaces the standalone `comet-ml/opik-skills` pack (retired) and the
per-consumer forks that used to drift (Claude Code plugin, Ollie).

Decision: OPIK-7471 (Option 2 — simplified, MCP-only). `opik-mcp` is the one home
for both the **tools** (`read`/`list`/`write`/`schema`/`run_experiment`/`ask_ollie`)
and the **skills** that teach an agent how to use them. Skills are served over MCP
by `read_skill` (OPIK-7472).

## Layout

Each skill is a directory with a `SKILL.md` entry point and an optional
`references/` folder for progressive-disclosure detail:

```
skills/
  opik/         SDK reference — tracing, span types, integrations, threads, prompts
  agent-ops/    Agent architecture, evaluation, production monitoring, anti-patterns
  opik-sdk/     Imperative Python SDK API reference
  evaluation/   LLM evaluation workflows — judges, RAG, synthetic data, error analysis
```

## Ownership & provenance

These skills are **owned here**. Consumers vendor them from `opik-mcp` — do not
hand-edit vendored copies elsewhere; edit here and re-sync.

- `opik/` was reconciled into a pure SDK reference (superseded opik-skills#16).
- `agent-ops/`, `opik-sdk/`, `evaluation/` were seeded from Ollie (`ollie_assist/skills`).

Every `SKILL.md` carries `last_updated` and `source_commit` frontmatter. Pin
`source_commit` to the Opik release each skill is verified against.

Host-specific skills stay with their host: Ollie keeps `navigation`,
`local-runners`, `instrumentation-*`; the plugin keeps `commands/`, its reviewer
agent, hooks, and logger.
