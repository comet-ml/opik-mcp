---
name: opik-compare
description: Run a candidate against the baseline over an Opik test suite and read the numbers back — which cases broke, which got fixed, the per-metric deltas, worst rows, and whether the two runs are comparable — with the Opik compare-view link. Runs via the SDK; reads results via the MCP when connected. Does not issue a ship/no-ship verdict. Use for "did my fix work", "compare against the baseline", "run the regression suite", "why did quality drop", "which cases regressed", "compare these two experiments". Not for the ship/hold decision itself (use verify), live production triage (use diagnose), building an evaluation from scratch (use evaluate), or capturing a single case (use test).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured and a test suite (from the test or evaluate skill) or two existing experiments. Install the `opik` skill alongside this one — it holds the shared test-suite and experiment references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
metadata:
  last_updated: "2026-09-15"
  source_commit: "2.0.0"
  argument-hint: "[suite name, or two experiment ids/names]"
---

# Compare — Candidate vs Baseline Over a Suite

**Definition of done:** two experiments on the same suite — a **baseline** and a **candidate** — read back item by item, with the **per-metric deltas**, the cases that went **pass → fail** (regressions) and **fail → pass** (fixes), the worst rows, a note on whether the runs are comparable, and the **Opik compare-view link**. If only one run exists, the done state is "baseline created — rerun after the change". If the suite can't be run or read, stop at the **first** genuine blocker and return **exactly one** next step. Aggregate scores alone are not a comparison; a verdict is not this skill's job.

Operate: **run the candidate the same way the baseline was run, read both back from Opik rather than from the run's console output, name the specific cases that changed — and change no application code.** The only file this skill writes is a throwaway runner outside the repo.

## Inputs

The entry point is `/opik-compare <suite>` (run the suite now as the candidate, compare against the latest prior run), `/opik-compare <experiment-A> <experiment-B>` (read two existing runs, no new run), or `/opik-compare` right after `/opik-test` (that suite). Infer the rest; treat these as **optional overrides**:

- suite (default: `<project>-regressions`, or the one `/opik-test` just wrote to) · baseline (default: the most recent earlier experiment on the suite) · candidate name (default: `candidate-<short git sha>`) · judge model for assertions (default: the suite's / the baseline's) · runs per item (default: the suite's policy).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve the suite and the baseline
The SDK calls: `references/sdk-snippets.md` (**Resolve the suite and the baseline**).
Baseline = the most recent prior experiment on this suite, unless the user names one. **No prior experiment → this run *is* the baseline** (step 3 still runs; status `baseline_created`). Two explicit experiments → skip step 3, go to step 4.

Confirm Opik is reachable: if `~/.opik.config` exists or `OPIK_API_KEY` is set, use it. Otherwise → **Blocker**.

### 2. Build the task adapter (code candidates)
The suite's items say what to call: each item `description` written by `/opik-test` ends in `Entrypoint: <root span name>`. Grep the repo for that function, import it, and wrap it:

```python
def task(item: dict) -> dict:
    return {"input": item["input"], "output": str(entrypoint(item["input"]))}
```
Write the runner as a **temp file outside the repo** (or a scratch path the user names) — never into the codebase, never committed. It needs the app's provider credentials; if they're absent → **Blocker** (the app can't answer the items). Never point the runner at a production entrypoint that writes, sends, or spends.

**Prompt candidates** (the change is a prompt version, not code): there is no adapter — run server-side instead: `references/sdk-snippets.md` (**Prompt candidates**).

### 3. Run the candidate
Same suite, same version, same judge model, same runs-per-item as the baseline — vary **only** the thing under test.
The `run_tests` call, and the guard for a judge that fails without raising: `references/sdk-snippets.md` (**Run the candidate**).
Do not read scores off `result` and stop — step 4 reads both runs from Opik so baseline and candidate go through the same path.

### 4. Read both runs back (SDK-first, MCP when connected)
The SDK read of both runs, and the side-by-side join: `references/sdk-snippets.md` (**Read both runs back**).
`get_experiment_by_name` is deprecated — resolve names with `get_experiments_by_name` and pick by id. When the MCP is connected it does the whole of this step in one call: `list('dataset_item', experiment_ids=['<baseline_id>', '<candidate_id>'])` returns the cases side by side with each run's score, the per-case delta, the worst trace to open, and a warning when the two runs covered different cases or different dataset versions. Prefer it over the SDK join in `references/sdk-snippets.md`, which stays here for runs the MCP cannot reach. `list('experiment', …)` and `read('experiment', id)` still find and sanity-check the two runs.

### 5. Answer the questions, in this order
1. **Comparable?** Same suite version (`dataset_version` / item count), same judge model, same runs-per-item. If not, say so first — the deltas below are then indicative, not measured.
2. **Which cases broke** — `passed` in baseline, not in candidate. Each with its input (one line), the failing assertion, its `reason`, and the candidate trace link.
3. **Which cases got fixed** — the reverse.
4. **Net effect per metric** — mean of each `feedback_scores` name in both runs, and the pass rate; report `baseline → candidate (delta)`.
5. **Worst rows** — lowest candidate scores; are they the same rows as the baseline's worst?
6. **App or judge?** A flip whose `reason` cites the output is the app; a flip on an unchanged output with a wavering `reason` is the judge — flag it, and suggest `runs_per_item: 3` for that item rather than trusting one run.
7. **Cost vs quality** — `total_estimated_cost` / duration on the candidate traces vs baseline, when the traces carry them.
8. **Flaky cases** — items that flip across repeated runs of the *same* code (the suite's execution policy exposes `runs_passed`/`runs_total`).
9. **Trend** — when more than two runs exist, the pass rate across the last few, so a one-step delta has context.

Report what the numbers say. **Do not decide ship or hold** — that is `/opik-verify`, which applies an explicit release policy to these numbers.

### 6. Link and hand off
Build the compare link with **both** ids — a URL-encoded JSON array — so the user lands on the side-by-side view:
```python
import json, urllib.parse

ids = urllib.parse.quote(json.dumps(["<baseline_id>", candidate_id]))
compare_url = f"{ui_base}/{workspace}/experiments/{suite.id}/compare?experiments={ids}"
```
`ui_base` is the Opik UI origin (the configured URL minus `/api`; `result.experiment_url` shows the exact host and workspace to reuse). Then one next step (see **Output**) — when the user's question is whether to ship, that step is `/opik-verify`.

### 7. Record (only when asked)
If the user wants the finding kept beside the data: a comment on a regressed case's trace via `client.rest_client.traces.add_trace_comment(trace_id, text=…)`, or a human score beside the judge's via `client.log_traces_feedback_scores([...])`. Never by default.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-compare <suite>`."
- "No test suite named `<suite>` in project `<name>` — run `/opik-test <trace-id>` to create one, or name the suite."
- "The suite's items don't say which function to call — pass the entrypoint (`/opik-compare <suite> --entrypoint answer`) and I'll build the runner."
- "The runner needs a provider credential — set `OPENAI_API_KEY` (or the relevant key) and rerun."
- "The assertion judge has no credential (every item reports `scoring_failed`, 'Missing credentials') — set the judge's provider key and rerun; the run `<name>` is not a comparable candidate."
- "Experiment `<id>` has no items yet (server-side run still processing) — rerun in a minute."

## Output

**User-facing:** a short human message — comparability note (if any), regressions first (case, assertion, why), then fixes, then the per-metric table (`baseline → candidate (delta)`), the compare link, and the single next step. No verdict. Not a raw dump of every item.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (fix verified, regression surfaced, first run, two existing runs): `references/examples.md`.

## Anti-patterns
Reading scores from the run's console output instead of from Opik; comparing runs on different suite versions or judge models without saying so; reporting only aggregates ("accuracy 0.82 → 0.85") without naming the cases that flipped; writing the runner into the repo or committing it; changing app code to make the suite pass; trusting a single run on a flaky item; **issuing a ship/no-ship verdict** (out of scope by design); using deprecated `get_experiment_by_name`; building the compare link with one id.

## References

Test-suite, experiment, and metric detail live in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/references/evaluation-test-suites.md` (`run_tests`, `TestSuiteResult`, execution policies, versions, `get_test_suite_experiments`), `../opik/references/evaluation-datasets.md` (`evaluate()`, experiments, OQL), `../opik/references/production.md` (cost and latency fields on traces). If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
