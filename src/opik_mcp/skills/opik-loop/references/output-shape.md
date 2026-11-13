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
- `run`: `id`, `status`, `failed_step`, `last_error`, `parent_run_id` (for `explained`)
- `setup`: the CI step handed back, and the path it was written to for review (gate only)
- `check_in_suggested`: `true` once, after the first recipe went on
- `source`: `mcp` (the only path today)
- `next_step`: exactly one

Invariants: `on`, `created` and `applied` carry an `estimate` and were each preceded by a dry run the user saw; `on` and `created` carry an `automation` with a non-empty `writes_to`; a step that costs money carries `limits` with `max_cost_usd_per_day`; `explained` carries a `run` and names no fix the runs do not support; `blocked` carries exactly one `next_step`; every path leaves the application code unchanged, and a `setup` step is written as a change for review and never merged.
