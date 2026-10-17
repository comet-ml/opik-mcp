# `/opik-test` evals

Test cases, automation, and success metrics for the `opik-test` skill —
structured on Anthropic's *Testing and iteration* guidance and OPIK-7650.

## Layout

```
evals/
  cases.yaml          # triggering + functional (capture) + edge (duplicate)
  fixtures/toolbug/   # the explain skill's buggy support agent — emits a wrong-refund trace
  grader.py           # deterministic: item vs emitted trace id, assertion bounds, read-only
  metrics.py          # aggregate -> the OPIK-7650 metrics
  run_evals.py        # prepare (emit traces) / grade (offline) / verify (network) / trigger-*
  _work/              # staged run dirs + reports (gitignored)
```

## Run it

Deps: `pyyaml` (via `uv run --with pyyaml`). `prepare` needs **Opik configured**
(`~/.opik.config` or `OPIK_API_KEY`) and network, because it emits a real trace.
Grading is offline; `verify` reads the suite back.

```bash
uv run --with pyyaml python run_evals.py prepare
#  ... run /opik-test in _work/capture/ with PROMPT.txt, then in _work/duplicate/.
#      Each should write result.json = {status, suite, item, source, next_step}.
uv run --with pyyaml python run_evals.py grade
uv run --with pyyaml python run_evals.py verify     # the item is really in the suite, once
```

**Triggering (`selection_accuracy`):**
```bash
uv run --with pyyaml python run_evals.py trigger-prepare
#  ... a judge panel classifies each phrase (descriptions only) into verdicts.json ...
uv run --with pyyaml python run_evals.py trigger-grade
```
The menu presents the real `opik-test` description alongside decoys — especially
`opik-compare` (RUN the suite) vs `opik-test` (ADD a case to it), the pair most
likely to be confused.

## What the functional case checks

`capture` runs the toolbug agent once (input "what is your refund window?", output a
wrong 24-hour promise) and hands the trace id to the skill. The grader scores
`result.json`:

- **source_trace** — `item.source_trace_id` is the emitted id (dedupe key).
- **assertion_count** — 1 or 2 assertions, never a checklist.
- **assertion_names_failure** — an assertion mentions the refund window / business
  days / the wrong 24-hour claim.
- **suite_named**, **input_verbatim** — the suite has a name; the input is stored as
  the trace recorded it.
- **one_next_step** + **next_step_handoff** — exactly one next step, pointing at
  `/opik-compare`.
- **no_modifications** — the fixture files are unchanged (the skill writes to Opik,
  not the repo).

`duplicate` reuses the same trace id; the skill must find the existing item and
return `exists` without inserting a second one — `verify` checks the suite holds
exactly one item for that trace.

## Metrics (OPIK-7650)

`selection_accuracy` · `captured_rate` · `trace_link_rate` · `assertion_bound_rate` ·
`handoff_rate` · `read_only_rate` (target 1.0) · `schema_compliance`.

## Note

`evals/` is **development tooling** — `build_skills_pack.py` excludes it
(`EXCLUDED_DIRS`), so it never ships in the public pack. Matches the
`/opik-instrument`, `/opik-diagnose`, and `/opik-explain` harnesses.
