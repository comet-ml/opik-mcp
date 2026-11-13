---
name: opik-loop
description: Set up and tend a project's improvement loop over the Opik MCP — the Collect and Align recipes, or a custom automation — when a score lands on a production trace, add it to the regression suite or send it to review. Reads the recommendations, the recipe states, the automations and their runs; every change is a write the user agreed to, after a dry run that showed the 14-day estimate. Use for "set up Opik for this agent", "gate my PRs on the regression suite", "what should I do next", "is Collect still worth it", "why did this automation fail", "send refund answers below 0.5 to review". Not for building the judge that writes the score (use online-eval), capturing one case by hand (use test), or reading the experiments of a failed run (use compare or explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires the Opik MCP connected to a backend that ships automations; the automations API has no SDK yet, so without the MCP this skill stops and says so. Install the `opik` skill alongside this one for the shared production and test-suite references.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-11-13"
  source_commit: "3.0.6"
  argument-hint: "[a recipe (collect, align), a question about the loop, or a custom automation to build]"
---

# Loop — Set Up and Tend the Improvement Loop

**Definition of done:** the project's loop is one step further than it was, and the user agreed to that step after seeing what it would do: a recipe turned on (its automations exist, `list('automation', …)` shows them enabled), a custom automation created and tested on a real trace, an upkeep recommendation applied, or a failed run explained with its parent chain. Every write was preceded by a `dry_run=true` that showed the validated definition and the 14-day estimate, and the answer names the recipe and the objects it writes to. If the loop cannot move, stop at the **first** genuine blocker and return **exactly one** next step.

Operate: **read before you propose, dry-run before you write, ask before every write, never turn on a step that costs money without a daily limit — and change no application code.** The one exception is a `setup` block a recipe hands back (a CI step for Gate): that goes into the user's repo as a change for review, never merged by this skill.

## Inputs

The entry point is `/opik-loop` right after `/opik-instrument` (set the project up), `/opik-loop <recipe>` (`collect`, `align`; `gate` and `improve` once the backend lists them), `/opik-loop <question>` ("what should I do next", "is Collect still worth it", "why did this run fail"), or `/opik-loop <a custom automation in words>` ("send refund answers below 0.5 to review"). Infer the rest; treat these as **optional overrides**:

- project (default: the configured one, else the project of the trace in hand) · score name (default: the recipe's, from `available_score_names` on the workflow row) · threshold (default: the recipe's, 0.5) · suite or queue name (default: the recipe's; it creates a missing one) · per_day cap (default: the recipe's) · daily limits (default: ask; required for any step that costs money).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Confirm the MCP and the backend
Call `list('recommendation', project_name=…)`. It answers, never empty, on a backend that ships automations; a refusal saying this Opik has no automations yet is a **Blocker** (nothing in the SDK does this), and `list('online_rule', …)` still shows the judges that exist.

### 2. Read the loop as it is
- `list('workflow', project_name=…)`: each recipe's state (`on`, `partly_on`, `off`), its `blocked_reason`, the automations it would create and the 14-day estimate.
- `list('automation', project_name=…)`: what runs today, with `recipe_id`, `event`, `step_targets`, `fired_7d`, `items_7d` and `paused`.
- `list('recommendation', project_name=…)`: the ranked next steps, each with its `apply_call` ready for `write`.

Say what is already on before proposing anything: a recipe that is on is not a recommendation.

### 3. Pick the step, by what the user said

| User says | Do |
|---|---|
| "Set up Opik for this agent" (right after instrument) | Take the top recommendation (Collect, on a project with no suite being filled). Show its reason, then `write('workflow.enable', data=…, dry_run=true)` and show the automation it would create and the estimate. On yes, the same call without `dry_run`. |
| "Gate my PRs on the regression suite" | `list('workflow', …)`: if `gate` is not a row, this backend does not run it yet — say so and offer Collect. If it is, dry-run `workflow.enable` with `recipe: "gate"`; the answer's `setup` holds the CI step. Write it into `.github/workflows/` as a change for review, then enable on yes. |
| "What should I do next?" | The top recommendation's `reason`, its evidence, and its `apply_call`. On yes, send the call verbatim to `write` — with `dry_run=true` first when it creates or changes anything. |
| "Is Collect still worth it?" | `read('automation', id)` for the Collect automation: `stats` (fired, sampled, items_written, cost) and `paused_reason`; plus any recommendation whose `automation_id` is it. Answer from the numbers, not from the fact that it is on. |
| "Why did this automation fail?" | `list('automation_run', automation_id=…, status='failed')`, then `read('automation_run', id)`: the step that failed, its `last_error`, and `parent_run_id` for the run that fired it. For a gate run's experiments, hand off to `/opik-compare` with the two experiment ids. |
| "Send refund answers below 0.5 to review" | A custom automation: `write('automation.create', data=…, dry_run=true)` with a `feedback_score:created` trigger on the score and an `annotation_queue.add` step (the queue by name; it is created when missing). Then `automation.test` on one recent matching trace (`list('trace', …, filters=…)`), show each step's outcome, and create for real on yes. |

The judge that writes the score is not this skill's to build: when the score the recipe needs is not in `available_score_names`, hand off to `/opik-online-eval` and come back.

### 4. Dry run, show, ask, write
Every create, update or enable goes out with `dry_run=true` first. Show, in the user's words:
- the recipe or automation name, the event it listens to, and **the objects it writes to** (the suite, dataset or queue, by name);
- the 14-day estimate: how often it would have fired, the items it would have written, the cost;
- `warnings` when present (a cycle is written, but flagged).

A step that costs money (a judge, an optimizer) needs `limits.max_cost_usd_per_day`; the backend refuses it otherwise (`limit_required`), and so does this skill: propose a limit rather than dropping the step. Then ask. Only a yes sends the live call. A `validation_failed` names the field and the code (`target_not_found`, `unknown_event`, …): fix the definition, dry-run again, ask again.

### 5. Verify
Read it back: `list('automation', project_name=…)` shows the new automation enabled; for a custom one, `automation.test` on a real trace returned each step as `done`. Do not report success from the write's status alone.

### 6. Report, and suggest the check-in once
Return what is on now, what it writes to, the estimate, and one next step (see **Output**). After the **first** recipe goes on, suggest the weekly check-in once — "what changed in my loop this week": `list('automation_run', project_name=…)` and `list('recommendation', …)` read together — and do not repeat the suggestion.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "The Opik MCP is not connected — connect it (hosted or local) and rerun; the automations API has no SDK path."
- "This Opik has no automations yet — it needs a backend version that ships them; `list('online_rule', …)` still shows the judges."
- "No score named `<name>` has landed in `<project>` in 14 days — run `/opik-online-eval` to take a judge live, then rerun `/opik-loop collect`."
- "Which project? None is configured and no trace is in hand."
- "That step costs money and has no daily limit — name a `max_cost_usd_per_day` and I'll dry-run it again."

## Output

**User-facing:** a short human message — the recipe or automation by name, the event and the objects it writes to, the 14-day estimate it was enabled on, whether it is **on, created, applied, explained or blocked**, and the single next step. Not a dump of the definition.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (set up after instrument, next step applied, upkeep answered from stats, a failed run explained, a custom automation tested first, blocked without the backend): `references/examples.md`.

## Anti-patterns
Writing without a dry run, or without showing the estimate; enabling anything the user did not say yes to; turning on a step that costs money with no daily limit; building the judge here instead of handing off to `/opik-online-eval`; recommending a recipe that is already on; answering "is it worth it" from the enabled flag instead of the stats; reporting success from the write's status instead of reading the automation back; merging a `setup` CI step instead of opening it for review; **editing application code**; suggesting the weekly check-in more than once.

## References

Automations have no SDK yet; everything here is the MCP's `read`, `list` and `write`, and `schema('automation.create')` returns the definition's full shape. Production traces and test suites are the `opik` skill's: `../opik/references/production.md` (`search_traces`, scores on traces), `../opik/references/evaluation-test-suites.md` (the suite a recipe fills). The neighbours this skill hands off to: `../opik-online-eval/SKILL.md` (the judge), `../opik-test/SKILL.md` (one case by hand), `../opik-compare/SKILL.md` and `../opik-explain/SKILL.md` (a failed run's experiments and traces).

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
