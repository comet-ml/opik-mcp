---
name: opik-diagnose
description: Surface the Opik traces worth a developer's attention, ranked by signal — Diagnostics issues first, then errors, failed tool calls, latency, regressions, and low online-eval scores. With the Opik MCP connected it lists the project's agent_insights_issue entities, offers to turn Diagnostics on when the project has it switched off, then fills the gaps with list (filters, sort, a time window); without the MCP it reads the same via the SDK (agent_insights and search_traces), so it works with no MCP. Returns a ranked shortlist, each item ready to hand to the explain skill. Use for "what is broken in production", "which traces need attention", "find failing or slow traces", "which tool calls are failing", "triage my agent". Not for offline experiment results (use evaluate or compare) and not for root-causing one trace (use explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured and a project that has traces. Install the `opik` skill alongside this one — it holds the shared SDK and observability references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-09-09"
  source_commit: "2.0.0"
  argument-hint: "[optional: project name, or what to look for]"
---

# Diagnose — Surface the Traces Worth Attention

**Definition of done:** a **ranked shortlist** of the online/production traces (and Diagnostics issues) worth attention, each carrying the **signal that flagged it** and its **trace id**, scoped to a project and a recent window, and ready to hand to `/opik-explain`. "Worth attention" means errored, slow, regressed, or low online-eval score — not a dump of every trace, and never offline experiment results. If the project can't be read, stop at the first genuine blocker and return one next step.

Operate: **rank by real signal over live data, surface the few things worth a look, hand the top one to `/opik-explain` — and change no code.** This skill is read-only by design.

## Inputs

The entry point is `/opik-diagnose` (the current project), `/opik-diagnose <project>`, or `/opik-diagnose <what to look for>` (e.g. "slow traces", "errors today"). Infer the rest; treat these as **optional overrides**:

- project (default: inferred from config/repo) · window (default: recent) · signal focus (default: all — errors, latency, regressions, low scores) · shortlist size (default: a handful).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve scope
Project (from config/repo) + a recent window. Confirm Opik is reachable: if `~/.opik.config` exists or `OPIK_API_KEY` is set, use it. Otherwise → **Blocker** ("run `opik configure`, then rerun").

### 2. Read the project once, for the shape of the week

**With the Opik MCP connected**, `read('project', <name or id>)` costs one call
and answers three things the rest of this skill would otherwise guess: whether
anything is wrong at all (error rate and latency against the previous 7 days —
a flat week is worth saying so and stopping), which score names the project
actually records (the ones worth filtering on later; guessing a name returns an
empty result that reads like good news), and what the project contains.

A rate reported as `null` means no traces in the window, not a healthy zero.
If the window is quiet, widen it with `since="30d"` before concluding anything.

### 3. Start from Diagnostics issues
Opik's Diagnostics already groups a project's recurring failures into ranked issues, each with a severity, occurrence counts, a cause, a suggested fix and example traces. Read that list first — it is the answer to "what is broken" the UI already computed, so do not rebuild it from raw traces.

**With the Opik MCP connected**, the `agent_insights_issue` entity is the primary path:

```text
list('agent_insights_issue', project_name='<project>')        # open issues, ranked as the Diagnostics page ranks them
read('agent_insights_issue', '<issue id>', project_name='<project>')
#  → {issue: {name, cause, suggested_fix, severity, status, …}, example_trace_ids: [...], details: [...],
#     url: '<the issue's Diagnostics page>', trace_url_template: '<…/logs?trace={trace_id}>'}
```

Turn each open issue into one shortlist item as `references/diagnostics-list.md` (**Issue to shortlist item**) describes.

**An empty list is not an all-clear, and a non-empty list is not the whole answer either.** Act on the sentence the list gives you: `references/diagnostics-list.md` (**Empty list**, **Coverage line**) says what each one means and what to do; ask the user once before enabling Diagnostics.

Never wait or poll for a scan. Hand back the page link, finish the triage from traces, and say the grouped report will be there in a few minutes. A shortlist built while a scan you started is still running reports `source=diagnostics_pending`.

Say the as-of date in the report when it matters: a user who asked for a week
and got issues through yesterday should learn that from you, not discover it.

Triage never changes an issue's status. `agent_insights_issue.resolve`,
`.close` and `.reopen` exist, and they are for when the user asks for them:
whether a failure is dealt with is their call, and an issue marked resolved
leaves the list everyone else reads. Surfacing an issue is this skill's job;
retiring one is not.

**Without the MCP**, the SDK REST client reads the same issues: `references/diagnostics-list.md` (**Without the MCP**).

### 4. Pull candidate traces to fill the gaps — MCP first, SDK fallback
Diagnostics reports what its last run grouped. Anything newer, or below its grouping threshold — a single latency outlier, one low online-eval score, a regression versus the prior window — still needs a scan. Skip traces already covered by an issue's `example_trace_ids`; they are on the shortlist under that issue.

- **MCP connected:** one `list` call per signal. The backend does the filtering and ordering, so each call returns a short, already-ranked page — no SDK, no client-side sorting. `since` takes `"1h"`, `"24h"`, `"7d"`; `filters` is an OQL string; `sort` is `"<field> [asc|desc]"` (desc by default). Trace lists hide evaluator/playground/experiment traces (`source = "sdk"`) unless you name `source`.

  The calls, one per signal, and the fields they return: `references/trace-queries.md` (**MCP**).

- **No MCP:** fall back to the SDK.

  The call: `references/trace-queries.md` (**SDK**).

Skip traces already covered by an issue's `example_trace_ids`; they are on the shortlist under that issue.

### 5. Rank the remaining traces by signal
Score each remaining candidate and keep the top few. Priority order:
1. **Errored** — the trace or a span captured an exception.
2. **Tool-call failures** — a `tool` span errored, returned an error-shaped result, or repeated the same call (a retry loop). Agents fail here often, so surface it as its own signal: use `has_tool_spans` to find candidates, then scan their `tool` spans for a non-empty error, an output that reads like an error/refusal, or duplicate consecutive calls.
3. **Latency outliers** — duration well above the project's typical (use the p90/p99 as the bar).
4. **Low online-eval score** — a feedback score below its threshold (Answer Relevance, Hallucination, etc.).
5. **Regressions** — a signal that worsened versus the prior window.

Append these after the Diagnostics items, in the signal order above. Give each shortlisted item the one signal that flagged it and a short why — one entry per trace: when a trace matches several signals (an errored `tool` span also errors the trace), keep the highest-priority signal and mention the rest in the why. Prefer a short, ranked list over a long one.

### 6. Stay in scope
Online/production **trace** signal only. Do **not** surface offline experiment results — those are the output of `/opik-evaluate` and `/opik-compare`, not rediscovered here.

### 7. Report
Return the ranked shortlist and one next step. Give each item as a **clickable Opik UI link**, never a bare id, so the user can open it and deep-dive. Do not invent the URL shape — a guessed link looks right and 404s, which is worse than the id. Take it from what the server gave you: an issue's `trace_url_template`, or the trace link template the MCP names in its session instructions (`<opik>/api/v1/session/redirect/projects/?trace_id={trace_id}&path=…`), which resolves the project and workspace from the id, so filling in the id is all it needs. On the SDK path, `opik.url_helpers.get_project_url_by_trace_id(trace_id, url_override)` builds the same link. Each item is ready for `/opik-explain`; the natural next step is "explain the top trace" (see **Output**). This skill surfaces and hands off; it does not root-cause (that is `/opik-explain`) and it changes no code.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-diagnose`."
- "Which project should I scan? Pass `/opik-diagnose <project>` or set it in the Opik config."
- "This environment can't reach Opik — open the project's traces view, sort by errors/duration, or run where Opik is configured."

## Output

**User-facing:** a short human message — the ranked shortlist (a clickable Opik UI link per trace + its signal + one-line why, worst first), then the single next step. Not a raw dump of every trace, not JSON.

**Underneath** (for composition / evals), one shape, with its invariants: `references/output-shape.md`.

## Examples

Worked runs (MCP connected, SDK only, nothing wrong, blocked): `references/examples.md`.

## Anti-patterns
Dumping every trace instead of a ranked shortlist; rebuilding the Diagnostics ranking from raw traces when `agent_insights_issue` (or the SDK `agent_insights` client) already lists the issues; surfacing offline experiment/`evaluate` results (out of scope); requiring the MCP (the SDK `agent_insights` path needs none); root-causing a trace here (hand it to `/opik-explain`); **editing code** (this skill only surfaces); ranking by recency instead of signal.

## References

SDK and observability detail live in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/SKILL.md` (**Searching traces** — the OQL filter grammar shared by the MCP `list` tool and `search_traces`), `../opik/references/production.md` (`search_traces`, Diagnostics, online-eval scores, error/latency analysis), `../opik/references/tracing-python.md` (SDK read APIs), `../opik/references/observability.md` (span/score model). If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
