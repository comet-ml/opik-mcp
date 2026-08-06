# `/instrument` evals

Test cases, automation, and success metrics for the `instrument` skill —
structured on Anthropic's *Testing and iteration* guidance (triggering /
functional / performance) and OPIK-7800.

## Layout

```
evals/
  cases.yaml        # declarative test cases (triggering + functional + edge)
  fixtures/         # uninstrumented apps of different shapes
    manual/           python, no LLM framework   -> manual @opik.track spans
    openai/           python + OpenAI            -> track_openai integration
    already_instrumented/  partly instrumented   -> audit only
    unsupported/      Go program                 -> unsupported, no changes
  grader.py         # deterministic scoring (ast + file-diff), no agent/network
  metrics.py        # aggregate -> the OPIK-7800 metrics
  run_evals.py      # orchestrator (manual two-phase, or best-effort auto)
  _work/            # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). Grading also imports `ast` (stdlib).

**Manual (always works — the "scripted testing in Claude Code" level):**
```bash
uv run --with pyyaml python run_evals.py prepare     # stage fixture workdirs under _work/
#  ... run the /instrument skill on each _work/<case>/ (Claude Code / an agent).
#      When it finishes, it should write result.json = {"status": "..."} there.
uv run --with pyyaml python run_evals.py grade       # score + emit metrics + report.md/json
```

**Automatic (best-effort — needs `claude` on PATH):**
```bash
uv run --with pyyaml python run_evals.py run --runner claude-code
```
(The `claude -p` flags / skill discovery vary by install; adjust `claude_code_run`
in `run_evals.py`. Failures are non-fatal — the grader scores whatever was produced.)

## What each case checks

- **triggering** — prompts that *should* and *should NOT* load the skill (feeds
  `selection_accuracy`; requires a runner that reports which skill loaded).
- **functional** — `manual` (manual spans, `general → tool → llm`, flush, `opik`
  installed, minimal diff, config untouched) and `openai` (native `track_openai`,
  the LLM call left undecorated, no double-wrap).
- **edge** — `already_instrumented` (audit only — don't re-instrument; add the
  missing entrypoint + flush) and `unsupported` (Go — return `unsupported`, change nothing).

## Metrics (from OPIK-7800)

`selection_accuracy` · `verification_rate` · `instrumentation_correctness` ·
`minimal_diff_rate` · `double_instrumentation_rate` (target 0) ·
`secret_mutation_rate` (target 0) · `no_prompt_migration_rate` ·
`avg_tool_calls` / `avg_tokens` (when the runner supplies cost data).

The grader is deterministic; `verified` status (a live trace check) comes from
the runner via `result.json`. Absent that, cases are graded on static checks and
status is left unknown.

## Note

`evals/` is **development tooling** — the pack CI (OPIK-7621) should exclude it
from the published `npx` pack. It travels with the skill in-repo per OPIK-7471.
