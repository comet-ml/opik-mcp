---
last_updated: "2026-11-13"
source_commit: "3.0.6"
---

# Output shape

- `status`: `on` | `created` | `applied` | `explained` | `blocked`
- `project`: `name`, `id`
- `recipe`: `id`, `state` (when the step was a recipe)
- `automation`: `id`, `name`, `event`, `writes_to` (list of `{type, name}`), `enabled` (when one was created, changed or read)
- `estimate`: `window_days`, `fired`, `items`, `cost_usd` (from the dry run the user agreed on)
- `limits`: as sent, when any step costs money
- `run`: `id`, `kind` (`rollup` | `single`), `status` (`pending` | `done` | `partial` | `failed`), `item_id`, `failed_step`, `last_error` (a timed-out optimization reads "timed out after … waiting"), `outputs` (`run.regressions`, `run.pass_rate`, `run.baseline_pass_rate`, `run.experiment_id`, `run.baseline_experiment_id`; `optimize.gain`, `optimize.best_score`, `optimize.baseline_score`, `optimize.optimization_id`; `save.prompt_version_id`, `save.commit`; `agreement.agreement`, `agreement.compared` — whichever the run has), `waiting_on` (while `pending`: `experiment:finished:<id>` or `optimization:finished:<id>`), `parent_run_id`, `child_run_ids` (for `explained`)
- `loop`: `nodes` (`id`, `kind`, `name`, `url` when the kind has a page) and `edges` (`automation_id`, `recipe`, `from`, `to`, `produced`), as read before the step was proposed
- `recommendation`: `id`, `kind` (`setup` | `tune` | `remove` | `review`), `automation_id`, `apply` (the call as sent; for `applied`)
- `setup`: `github_action` (the CI step handed back) and `path` (where it was written for review; gate only)
- `check_in_suggested`: `true` once, after the first recipe went on
- `source`: `mcp` (the only path today)
- `next_step`: exactly one

Invariants: `on`, `created` and `applied` were each preceded by a dry run the user saw and carry an `estimate` where the backend gives one (an `improve` enable and a counted-trigger automation carry none, and no figures are invented); a step was proposed only after `loop` was read; an `applied` upkeep call was shown with what it changes before it was sent; `on` and `created` carry an `automation` with a non-empty `writes_to` (for Gate: the suite it runs, the repository it checks, the URL it notifies); a step that costs money carries `limits` with `max_cost_usd_per_day`; `explained` carries a `run` and names no fix the runs do not support, and a `pending` run is never reported as failed; `blocked` carries exactly one `next_step`; every path leaves the application code unchanged, a `setup` step is written as a change for review and never merged, and no field ever carries the GitHub token.
