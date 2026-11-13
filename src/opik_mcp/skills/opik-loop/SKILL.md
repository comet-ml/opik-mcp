---
name: opik-loop
description: Set up and tend a project's improvement loop over the Opik MCP — the Collect, Align and Gate recipes, or a custom automation — when a score lands on a production trace, add it to the regression suite or send it to review; when a prompt version is saved or CI runs, run the suite, post the GitHub check and notify on regressions. Reads the recommendations, the recipe states, the automations and their runs; every change is a write the user agreed to, after a dry run that showed the 14-day estimate. Use for "set up Opik for this agent", "gate my PRs on the regression suite", "what should I do next", "is Collect still worth it", "why did this automation fail", "why did Gate fail on this PR", "send refund answers below 0.5 to review". Not for building the judge that writes the score (use online-eval), capturing one case by hand (use test), or reading the experiments of a failed run (use compare or explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires the Opik MCP connected to a backend that ships automations; the automations API has no SDK yet, so without the MCP this skill stops and says so. Install the `opik` skill alongside this one for the shared production and test-suite references.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-11-13"
  source_commit: "3.0.6"
  argument-hint: "[a recipe (collect, align, gate), a question about the loop, or a custom automation to build]"
---

# Loop — Set Up and Tend the Improvement Loop

**Definition of done:** the project's loop is one step further than it was, and the user agreed to that step after seeing what it would do: a recipe turned on (its automations exist, `list('automation', …)` shows them enabled), a custom automation created and tested on a real trace, an upkeep recommendation applied, or a failed run explained with its parent chain. Every write was preceded by a `dry_run=true` that showed the validated definition and the 14-day estimate, and the answer names the recipe and the objects it writes to. If the loop cannot move, stop at the **first** genuine blocker and return **exactly one** next step.

Operate: **read before you propose, dry-run before you write, ask before every write, never turn on a step that costs money without a daily limit — and change no application code.** The one exception is the `setup` block Gate hands back (a CI step): that goes into the user's repo under `.github/workflows/` as a change for review, never merged by this skill. Secrets never pass through this conversation: the CI step reads `OPIK_API_KEY` from the repo's secrets, and the GitHub token the check posts with is set by the user, not by you.

## Inputs

The entry point is `/opik-loop` right after `/opik-instrument` (set the project up), `/opik-loop <recipe>` (`collect`, `align`, `gate`; `improve` once the backend lists it), `/opik-loop <question>` ("what should I do next", "is Collect still worth it", "why did this run fail", "why did Gate fail on PR 482"), or `/opik-loop <a custom automation in words>` ("send refund answers below 0.5 to review"). Infer the rest; treat these as **optional overrides**:

- project (default: the configured one, else the project of the trace in hand) · **score name: always asked, never inferred** — a recipe has no default judge, so show `available_score_names` from the workflow row and let the user choose (the backend refuses an enable without one: `score_name_required`) · **for Gate, the prompt and the model: always asked, never inferred** — the backend refuses an enable without them (`prompt_name_required`, `model_required`); `list('prompt', …)` shows the prompts to choose from · Gate's `repository` (`owner/name`, adds the GitHub check) and `notify_url` (adds a notify step that fires on regressions; Slack for a `hooks.slack.com` URL): optional, asked once · threshold (default: the recipe's, 0.5) · suite or queue name (default: the recipe's; it creates a missing one) · per_day cap (default: the recipe's) · daily limits (default: ask; required for any step that costs money — Gate's suite run does).

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
| "Set up Opik for this agent" (right after instrument) | Take the top recommendation (Collect, on a project with no suite being filled). Show its reason and **ask which judge score to act on** from the workflow row's `available_score_names` (none listed: hand off to `/opik-online-eval` first). Then `write('workflow.enable', data=…, dry_run=true)` with that `overrides.score_name` and show the automation it would create and the estimate. On yes, the same call without `dry_run`. |
| "Gate my PRs on the regression suite" | `list('workflow', …)`: if `gate` is not a row, this backend does not run it yet — say so and offer Collect. If its `blocked_reason` is set, the suite is empty: say what fills it (Collect, or one case with `/opik-test`) and stop. Otherwise **ask which prompt and which model** run the suite (never infer them), and whether to post a GitHub check (`repository`) and notify (`notify_url`). Dry-run `workflow.enable` with `recipe: "gate"` and those `overrides`; show `would_create`, the estimate and `setup.github_action`. Write that step into `.github/workflows/` in the user's repo as a change for review — `OPIK_API_KEY` from the repo's secrets, `OPIK_WORKSPACE` and `OPIK_URL` from the MCP's configuration — never merged. Then enable on yes, and read it back. A `github.check` step needs the workspace's GitHub token: an enable refused with `integration_required` is a **Blocker** the user clears themselves. |
| "Why did Gate fail on PR 482?" | `list('automation_run', automation_id=<the gate automation>, status='done')` (a failed check is a run that finished with regressions; `status='failed'` is the suite run itself failing) and find the row whose `item_id` is `<repository>@<commit>`. `read('automation_run', id)`: `outputs.run.regressions`, `run.pass_rate` against `run.baseline_pass_rate`, each step's status and `reason`, `parent_run_id` for the run that fired it. A run still `pending` carries `waiting_on` (`experiment:finished:<id>`): nothing failed yet, the suite run has not finished. For the cases that regressed, hand off to `/opik-compare` with `outputs.run.experiment_id` and `outputs.run.baseline_experiment_id` (absent: the previous finished experiment on the suite). |
| "What should I do next?" | The top recommendation's `reason`, its evidence, and its `apply_call`. On yes, send the call verbatim to `write` — with `dry_run=true` first when it creates or changes anything. |
| "Is Collect still worth it?" | `read('automation', id)` for the Collect automation: `stats` (fired, sampled, items_written, cost) and `paused_reason`; plus any recommendation whose `automation_id` is it. Answer from the numbers, not from the fact that it is on. |
| "Why did this automation fail?" | `list('automation_run', automation_id=…, status='failed')`, then `read('automation_run', id)`: the step that failed, its `last_error`, and `parent_run_id` for the run that fired it. For a gate run's experiments, hand off to `/opik-compare` with the two experiment ids. |
| "Send refund answers below 0.5 to review" | A custom automation: `write('automation.create', data=…, dry_run=true)` with a `feedback_score:created` trigger on the score and an `annotation_queue.add` step (the queue by name; it is created when missing). Then `automation.test` on one recent matching trace (`list('trace', …, filters=…)`), show each step's outcome, and create for real on yes. |

The judge that writes the score is not this skill's to build: when the score the recipe needs is not in `available_score_names`, hand off to `/opik-online-eval` and come back.

**Where the agent runs decides how Gate runs the suite.** A prompt-only agent is run by Opik itself: `test_suite.run` executes the suite server-side against the prompt version and the run waits (`pending`) for `experiment:finished`. A code agent cannot be run by Opik: CI runs the suite with the SDK (the `/opik-compare` run), and the gate automation triggers on `experiment:finished` for that suite, compares the experiment with the baseline and posts the check — so for a code agent the automation is a custom one: trigger `{"event": "experiment:finished", "test_suite_name": …}`, steps `github.check` and `notify`, no `test_suite.run`; the CI change is the SDK run plus the `ci:run` call, and the dry run says which.

### 4. Dry run, show, ask, write
Every create, update or enable goes out with `dry_run=true` first. Show, in the user's words:
- the recipe or automation name, the event it listens to, and **the objects it writes to** (the suite, dataset or queue, by name);
- the 14-day estimate: how often it would have fired, the items it would have written, the cost;
- `warnings` when present (a cycle is written, but flagged).

A step that costs money (a judge, an optimizer, a suite run) needs `limits.max_cost_usd_per_day`; the backend refuses it otherwise (`limit_required`), and so does this skill: propose a limit rather than dropping the step. Then ask. Only a yes sends the live call. A `validation_failed` names the field and the code (`target_not_found`, `unknown_event`, `step_scope_mismatch` — score events take add steps, prompt, experiment and CI events take run, check and notify steps, never both in one automation): fix the definition, dry-run again, ask again.

### 5. Verify
Read it back: `list('automation', project_name=…)` shows the new automation enabled; for a custom one, `automation.test` on a real trace returned each step as `done`. For Gate, the first run arrives as `pending` on `list('automation_run', automation_id=…)` after the next prompt commit or CI call, and finishes with `outputs.run.regressions`; the CI change is open for review, not merged. Do not report success from the write's status alone.

### 6. Report, and suggest the check-in once
Return what is on now, what it writes to, the estimate, and one next step (see **Output**). After the **first** recipe goes on, suggest the weekly check-in once — "what changed in my loop this week": `list('automation_run', project_name=…)` and `list('recommendation', …)` read together — and do not repeat the suggestion.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "The Opik MCP is not connected — connect it (hosted or local) and rerun; the automations API has no SDK path."
- "This Opik has no automations yet — it needs a backend version that ships them; `list('online_rule', …)` still shows the judges."
- "Which judge score should this act on? `<project>` writes: `<available_score_names>`." (always asked; a recipe never chooses)
- "Which prompt and which model should Gate run the suite with?" (always asked; a recipe never chooses — `list('prompt', …)` shows the prompts)
- "The regression suite is empty, so Gate has nothing to run — turn on Collect or add one case with `/opik-test`, then rerun `/opik-loop gate`."
- "The GitHub check needs the workspace's GitHub token, which is not set. Set it yourself with `curl -X PUT "$OPIK_URL/v1/private/automations/integrations/github" -H "Authorization: $OPIK_API_KEY" -H "Comet-Workspace: $OPIK_WORKSPACE" -d '{"token": "<github token>"}'`; it never goes through this conversation. Then rerun." (never ask for the token, never echo it)
- "No score named `<name>` has landed in `<project>` in 14 days — run `/opik-online-eval` to take a judge live, then rerun `/opik-loop collect`."
- "Which project? None is configured and no trace is in hand."
- "That step costs money and has no daily limit — name a `max_cost_usd_per_day` and I'll dry-run it again."

## Output

**User-facing:** a short human message — the recipe or automation by name, the event and the objects it writes to, the 14-day estimate it was enabled on, whether it is **on, created, applied, explained or blocked**, and the single next step. Not a dump of the definition.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (set up after instrument, Gate on PRs with the CI step written for review, a Gate run explained from its outputs, next step applied, upkeep answered from stats, a failed run explained, a custom automation tested first, blocked without the backend or the token): `references/examples.md`.

## Anti-patterns
Choosing the judge score, the prompt or the model for the user instead of asking; writing without a dry run, or without showing the estimate; enabling anything the user did not say yes to; turning on a step that costs money with no daily limit; building the judge here instead of handing off to `/opik-online-eval`; recommending a recipe that is already on; answering "is it worth it" from the enabled flag instead of the stats; reporting success from the write's status instead of reading the automation back; merging a `setup` CI step instead of opening it for review; asking for, pasting or echoing the GitHub token; reading a `pending` run as a failure; **editing application code**; suggesting the weekly check-in more than once.

## References

Automations have no SDK yet; everything here is the MCP's `read`, `list` and `write`, and `schema('automation.create')` returns the definition's full shape: the events (`feedback_score:created`, `prompt:committed`, `experiment:finished`, `ci:run`), the steps (`test_suite.add`, `dataset.add`, `annotation_queue.add`; `test_suite.run`, `github.check`, `notify`) and `only_if` on a run's outputs (`run.pass_rate`, `run.baseline_pass_rate`, `run.regressions`, `run.experiment_id`). Production traces and test suites are the `opik` skill's: `../opik/references/production.md` (`search_traces`, scores on traces), `../opik/references/evaluation-test-suites.md` (the suite a recipe fills and Gate runs). The neighbours this skill hands off to: `../opik-online-eval/SKILL.md` (the judge), `../opik-test/SKILL.md` (one case by hand), `../opik-compare/SKILL.md` and `../opik-explain/SKILL.md` (a gate run's two experiments, a failed run's traces).

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
