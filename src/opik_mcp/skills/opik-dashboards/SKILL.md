---
name: opik-dashboards
description: Answer a question about an LLM app with Opik's own metrics, and save the answer as a chart or a dashboard. Runs metric queries over traces, spans and threads (volume, latency, cost, tokens, errors, feedback scores) with breakdowns by name, model, provider, tags or metadata; reads dashboards a user already has; and builds new ones. Use for "chart our p99 latency", "which model costs the most", "build me a dashboard for this project", "add a cost chart to my dashboard", "what does this dashboard show", "is quality dropping". Requires the Opik MCP (dashboards have no SDK API). Not for reading individual traces (use opik-explain) or offline experiment results (use opik-evaluate).
compatibility: Requires the Opik MCP server connected to a workspace, with the chart_data, read, list and write tools. Tested with Claude Code; works with any Agent Skills-compatible host. Dashboards are an MCP-only surface — the Opik Python/TS SDKs have no dashboard API — so without the MCP this skill can only point the user at the Opik UI.
allowed-tools:
  - Read
  - Grep
  - Glob
metadata:
  last_updated: "2026-09-02"
  source_commit: "2.0.0"
  argument-hint: "[optional: what to chart, and for which project]"
---

# Dashboards — Chart Opik Metrics, and Save the Chart

**Definition of done:** the user's question is answered **with numbers pulled from Opik** — and, when they asked for something durable, a dashboard exists in their workspace with those charts on it and a link to open it. A chart nobody checked before saving is not done: every chart you save is one you have already run.

Operate: **query first, save second.** `chart_data` is free and reversible; a dashboard is something a person opens tomorrow.

## Inputs

Entry point: `/opik-dashboards`, `/opik-dashboards <what to chart>`, or any request that reads as "show me / chart / dashboard". Infer the rest; treat as optional overrides:

- project (default: the user's default project, else ask) · window (default: `7d`) · breakdown (default: none) · save or not (default: **don't** — answer first, offer to save).

## The four tools

| You want | Call |
| --- | --- |
| Numbers for a question | `chart_data(metric=…, project_name=…, window="7d")` |
| What a dashboard holds | `read("dashboard", <id or name>)` → `{dashboard, charts[]}`, each chart with its `widget_id` |
| The workspace's dashboards | `list("dashboard")` (add `project_id` for a project's own) |
| Save / edit | `write("dashboard.create" \| "dashboard.add_charts" \| "dashboard.remove_charts" \| "dashboard.update", …)` |

Charts are described the same way everywhere — one **ChartSpec**: `{kind, metric, breakdown, sub_metric, filters, project_name, chart_type, title}`. `kind` is `metric` (time series), `stat` (single-number card) or `text` (markdown note). Call `schema("dashboard.create")` for the exact field list rather than guessing.

## Activation

### 1. Resolve scope
Project name (from the request, the user's default project, or `list("project")`) and a window. Don't ask when you can infer; do ask when the workspace has several plausible projects.

### 2. Pick the metric
Say what the question is *about*, then take the metric from that entity:

- **traces** — `trace_count`, `duration` (p50/p90/p99), `trace_average_duration`, `trace_error_rate`, `cost`, `token_usage`, `feedback_scores`, `guardrails_failed_count`
- **spans** — `span_count`, `span_duration`, `span_error_rate`, `span_cost`, `span_token_usage`, `span_feedback_scores`
- **threads** — `thread_count`, `thread_duration`, `thread_cost`, `thread_feedback_scores`

"Which model / provider costs the most" is a **span** question (model and provider live on spans, so only span metrics break down by them). "Is quality dropping" is `feedback_scores`. "Are we erroring more" is `trace_error_rate`, and if you also want *which* error, `trace_count` broken down by `error_type`.

### 3. Run it before you save it
```
chart_data(metric="span_token_usage", project_name="chatbot-prod",
           window="14d", breakdown="model", sub_metric="completion_tokens")
```
Each series comes back with its points **and** a summary (`first/last/min/max/avg/total/change_pct`) over the whole window — read the summary, not the point list. An empty `series` means no data matched: check the project and widen the window before concluding anything.

Breakdown rules the tool enforces (it fails with the fix in the message, so don't pre-empt them nervously): `model`/`provider`/`type` are span-only; `metadata` needs `breakdown_key`; a breakdown on `duration`, `token_usage` or any feedback-score metric needs `sub_metric` (a percentile, a usage key, or the score name) because the breakdown collapses the multi-series response to one.

### 4. Answer
Lead with the number and the movement ("p99 is 4.2s, up 38% over 14 days"), then the split if you broke it down. This step is the deliverable for most requests — stop here unless the user wants something saved.

### 5. Save, if asked
```
write("dashboard.create", {
  "name": "Chatbot health", "project_name": "chatbot-prod",
  "charts": [
    {"kind": "stat",   "metric": "trace_count", "title": "Traces"},
    {"kind": "stat",   "metric": "error_count", "title": "Errors"},
    {"kind": "metric", "metric": "duration", "breakdown": "name", "sub_metric": "p99"},
    {"kind": "metric", "metric": "cost", "chart_type": "bar"}
  ]})
```
- A dashboard's `project_name` scopes it to that project **and** becomes each chart's default project. Charts on a workspace dashboard with no project at all render as "not configured" — give every chart a project, or give the dashboard one.
- Adding to an existing dashboard is `dashboard.add_charts` (pass every chart in **one** call — each call re-reads and rewrites the whole config), optionally with `section` to group them.
- `dashboard.update` with `charts` **replaces every chart**; use it to rebuild, not to append.
- Removing takes `widget_ids` from `read("dashboard", …)`.
- `dry_run: true` shows the exact request without writing.
- Report the `dashboard_url` from the result — a link the user can open beats an id.

Stat cards (`kind: "stat"`) use their own names: `trace_count`, `error_count`, `duration.p50|p90|p99`, `total_estimated_cost_sum`, `usage.total_tokens`, `feedback_scores.<name>`, … A dashboard usually opens with a row of those, then the time series.

### 6. Reading someone else's dashboard
`read("dashboard", <name>)` lists every chart with a `widget_id`; `chart_data(dashboard=…, widget=…)` re-runs one and hands you its numbers, over a window you choose. That is how you answer "what is this dashboard telling me" instead of describing its titles.

## Blockers

Stop at the **earliest** blocker, return **exactly one** next step:
- No Opik MCP connected → "Dashboards need the Opik MCP; connect it, or build this in the Opik UI." (Do not try the SDK — it has no dashboard API.)
- Project ambiguous → name the candidates the tool listed and ask which.
- Metric has no data in the window → say so, and offer the widened window rather than silently widening it.

## Output

**User-facing:** the answer in a sentence or two with the actual numbers, the breakdown if there is one, and — when something was saved — the dashboard link and what is on it.

**Underneath** (for composition / evals):
- `status`: `answered` | `saved` | `empty` | `blocked`
- `scope`: `project`, `window`, `interval`
- `charts`: `[{metric, breakdown?, summary}]`
- `dashboard`: `{id, name, url, charts:[{widget_id, title}]}` when something was saved
- `next_step`: exactly one

Invariants: every saved chart was run first; `empty` means the query succeeded and returned no series; `blocked` carries one next step; nothing here edits the user's code.

## Anti-patterns

Saving a dashboard nobody asked for; saving charts you never ran; describing a dashboard by its widget titles instead of running them; charting `model`/`provider` off trace metrics (span-only); calling `dashboard.add_charts` once per chart; using `dashboard.update` to append (it replaces); inventing filter shapes — copy the `{field, operator, value}` objects Opik itself uses; reaching for `ask_ollie` when a metric query answers the question directly.

## References

Metric semantics, filters and the span/trace/thread model live in the `opik` skill, installed beside this one: `../opik/references/observability.md` (span and score model), `../opik/references/production.md` (what to watch in production). For triaging *which traces* are bad rather than *how much*, that is `/opik-diagnose`; for one trace's root cause, `/opik-explain`.
