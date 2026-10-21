---
name: opik-verify
description: Decide ship or hold for a candidate from the compare skill's numbers, against an explicit release policy — regressions, pass rate, safety-tagged cases, subgroup consistency, latency and cost budgets, flakiness, evidence size, and whether the judge is validated. Reads two experiments on an Opik test suite via the SDK (or the MCP when connected) and returns a verdict with every criterion shown pass/fail. Use for "is this safe to ship", "can I merge this", "go/no-go on this change", "gate this release", "should I roll this out". Not for producing the numbers (use compare), building an evaluation (use evaluate), or deploying anything.
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires Opik configured and a test suite with a baseline and a candidate experiment (from the compare skill). Install the `opik` skill alongside this one — it holds the shared test-suite and experiment references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
metadata:
  last_updated: "2026-09-17"
  source_commit: "2.0.0"
  argument-hint: "[suite, or baseline and candidate experiment ids; optional --policy path]"
---

# Verify — Ship or Hold, Against a Policy You Can Read

**Definition of done:** one verdict — **`ship`**, **`hold`**, **`needs_review`**, or **`insufficient_evidence`** — computed from a **declared policy** over the baseline-vs-candidate numbers, with **every criterion listed with its threshold, the observed value, and pass/fail**, the cases behind any failure named, and the compare-view link. The policy is either the repo's `opik-release-policy.yaml` or the documented defaults, and the report says which. If the two runs can't be read or aren't comparable, stop at the **first** genuine blocker and return **exactly one** next step. "Looks good to me" is not a verdict; a verdict without its criteria is not one either.

Operate: **apply the policy mechanically, show your arithmetic, refuse to ship on a judge nobody validated, and change no application code.** The only file this skill may write is the policy file, and only when the user says so. It never deploys.

## Inputs

The entry point is `/opik-verify` right after `/opik-compare` (its baseline and candidate), `/opik-verify <suite>` (the two most recent runs on the suite), or `/opik-verify <baseline-id> <candidate-id>`. Infer the rest; treat these as **optional overrides**:

- policy (default: `opik-release-policy.yaml` at the repo root or under `.opik/`, else the defaults below) · which experiments (default: as above) · `--record` (default: off — write the verdict into the candidate experiment's config).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## The policy

Every key is optional; missing keys take the defaults listed in `references/policy.md`. Say in the report which source applied.

`judge_validated: false` is the **human-review gate**: until someone has confirmed the judge agrees with people (`/opik-evaluate`'s `validate-evaluator` reference), a passing run yields `needs_review`, not `ship`. Flip it to `true` in the file once that is done — deliberately a human edit, never something this skill sets on its own.

## Activation — the only in-scope work

### 1. Load the policy
Look for `opik-release-policy.yaml` at the repo root, then `.opik/`. Parse it; unknown keys → **Blocker** (name the key). No file → defaults, and say so. Never invent thresholds not in the file or the defaults.

### 2. Resolve the two runs
Take them from `/opik-compare`'s output when it just ran. Otherwise, the SDK read in `references/sdk-reads.md` (**Resolve the two runs**).
Skip a **failed-judge run** (a run whose judge had no credential is not a candidate — `/opik-compare` explains how it happens). `scoring_failed` does not survive the read path; the read-back signal is: **every item failed and every assertion `reason` mentions a missing credential or an LLM infrastructure error**. Say which run you skipped and why. When the hosted MCP is connected, `list('experiment', name=…)` shows each run's averages and pass rate to pick from; the item-level read below stays on the SDK.

### 3. Read both runs, item by item
An experiment holds **one item per run**: with `runs_per_item: 3` a dataset item appears three times, same `dataset_item_id`, different `trace_id`. Group — a dict keyed on `dataset_item_id` silently keeps one run and loses the counts.
The SDK read, grouped per item, with the pass thresholds taken from the suite: `references/sdk-reads.md` (**Read both runs**).
**Comparability first:** same `dataset_version_id`, same item set, same judge model (experiment config). Different → **Blocker** ("rerun the candidate on suite version X with judge Y, then `/opik-verify`") — a verdict on non-comparable runs is not a verdict.

### 4. Evaluate every criterion, in this order
Compute all of them even after the first failure — the report shows the whole table.
1. **Evidence size** — scored items (items with assertions) ≥ `min_items`. Below → the verdict is `insufficient_evidence` regardless of the rest, **unless a gate criterion (2–7) also failed — then it is `hold`**: a known safety regression outranks thin evidence. Still report every criterion.
2. **Regressions** — items `passed` in baseline and not in candidate. An item is **flaky** when `runs_passed` is strictly between 0 and `runs_total` in either run (the counts from step 3), or it flips between two runs of the same code if you have them. Under `flaky_policy: exclude` a flaky item is dropped from the regression count and listed separately with its counts; under `count` it stays in. When every item has `runs_total == 1`, flakiness is **not observable** — report the flaky check as `not_evaluated`, state that `exclude` excluded nothing, and suggest `runs_per_item: 3` on the suite if the user wants the protection. Count ≤ `max_regressions`.
3. **Safety** — any regression whose `data.tags` intersects `safety_tags` → fail, no exceptions, no exclusions.
4. **Pass rate** — candidate `pass_rate` vs baseline, or vs the number given.
5. **Subgroups** — when `subgroup_key` is set, pass rate per value of that key must not fall.
6. **Latency** — candidate p90 duration ≤ baseline p90 × (1 + `latency_p90_max_increase`), from the experiments' `duration` percentiles (`p50`/`p90`/`p99` on the experiment record) (or per-item `duration` from the REST experiment items).
7. **Cost** — candidate mean `total_estimated_cost` per item ≤ baseline × (1 + `cost_per_item_max_increase`). Skip and say "no cost data" when neither run carries costs.
   *Aggregates lag.* Right after a run finishes, the experiment record's `duration` can read `0.0` and `total_estimated_cost_avg` `None` for a few seconds while the backend aggregates (observed). A zero or missing aggregate on **one** side is not data — re-read after a short wait, or compute p90 and mean cost from the per-item `duration` / `total_estimated_cost` fields on the REST experiment items; never let a `0.0` pass or fail the gate.
8. **Evidence strength** — a paired sign test on the flips: with `f` fixes and `r` regressions, the two-sided binomial p-value under 50/50. Report it; it is **not** a gate. With `f + r < 6` say "too few flips to call it more than noise".
9. **Judge** — `judge_validated` from the policy. False → cap the verdict at `needs_review`.
10. **Attribution** — flips whose `reason` reads as judge hesitation on an unchanged output (see `/opik-compare` step 5.6) are listed for the human under `needs_review`, never silently counted either way.

### 5. Decide
Precedence, top to bottom — the first line that applies wins:
- Any of criteria 2–7 failed → **`hold`** (even when criterion 1 also failed).
- Criterion 1 failed → **`insufficient_evidence`**.
- All gates pass but `judge_validated: false`, or attribution flagged items → **`needs_review`**, naming exactly what a person should look at.
- Otherwise → **`ship`**.

Never round a `hold` up because the deltas are "mostly positive"; never round a `ship` down because of a hunch. The policy is the judgment; changing it is the user's move.

### 6. Report, and record only on request
The table (criterion · threshold · observed · pass/fail), the regressions named with their assertion and trace link, the compare URL with both ids, the policy source, and one next step. With `--record`, write the verdict into the candidate experiment's config — read the existing config first and merge, `update_experiment` replaces it: `references/sdk-reads.md` (**Record the verdict**).
Offer — do not do — writing `opik-release-policy.yaml` with the defaults when no file existed, so the next verdict is reproducible.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-verify`."
- "Suite `<name>` has fewer than two comparable runs — run `/opik-compare <suite>` first."
- "Baseline and candidate are on different suite versions (v3 vs v4) — rerun the candidate on v3, or re-baseline on v4, then `/opik-verify`."
- "`opik-release-policy.yaml` has an unknown key `<key>` — fix or remove it."
- "The candidate run's judge failed (every item `scoring_failed`) — set the judge's provider key and rerun `/opik-compare`."

## Output

**User-facing:** the verdict in one line, the criteria table, the regressions (case, assertion, why, link), the policy source, the compare link, and the single next step. Not a narrative, not JSON.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (ship, hold, needs review, insufficient evidence): `references/examples.md`.

## Anti-patterns
A verdict without the criteria table; thresholds pulled from thin air rather than the file or the defaults; shipping on an unvalidated judge; treating a flaky item as a regression (or a regression as flaky) without the run data to say so; comparing runs on different suite versions; averaging away a safety regression; a p-value presented as a gate on six flips; rounding `hold` to `ship` because the aggregate went up; writing the policy file or recording the verdict without being asked; **editing application code**; deploying.

## References

Test-suite and experiment detail live in the `opik` skill, installed beside this one — paths relative to this file: `../opik/references/evaluation-test-suites.md` (execution policies, `runs_passed`/`runs_total`, versions, `get_test_suite_experiments`), `../opik/references/evaluation-datasets.md` (experiments, OQL). The numbers this skill judges come from `../opik-compare/SKILL.md`; judge validation is `../opik-evaluate/references/validate-evaluator.md`. If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
