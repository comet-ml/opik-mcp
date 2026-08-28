---
name: opik-diagnose
description: Surface the Opik traces worth a developer's attention, ranked by signal — errors, failed tool calls, latency, regressions, and low online-eval scores — plus Diagnostics issues. Reads live/production traces via the SDK (search_traces and agent_insights) and works with no MCP; uses the MCP issue entity when connected. Returns a ranked shortlist, each item ready to hand to the explain skill. Use for "what is broken in production", "which traces need attention", "find failing or slow traces", "which tool calls are failing", "triage my agent". Not for offline experiment results (use evaluate or compare) and not for root-causing one trace (use explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured and a project that has traces. Install the `opik` skill alongside this one — it holds the shared SDK and observability references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-08-24"
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

### 2. Pull candidate traces (SDK-first)
The SDK is the primary path and needs no MCP.

```python
import opik
client = opik.Opik()

traces = client.search_traces(project_name="<project>", max_results=200)  # recent window
# Narrow server-side first when the volume is large; otherwise rank client-side (step 3).
# Each trace carries the fields you rank on: error info, duration, feedback_scores.
```

### 3. Rank by signal
Score each candidate and keep the top few. Priority order:
1. **Errored** — the trace or a span captured an exception.
2. **Tool-call failures** — a `tool` span errored, returned an error-shaped result, or repeated the same call (a retry loop). Agents fail here often, so surface it as its own signal: use `has_tool_spans` to find candidates, then scan their `tool` spans for a non-empty error, an output that reads like an error/refusal, or duplicate consecutive calls.
3. **Latency outliers** — duration well above the project's typical (use the p90/p99 as the bar).
4. **Low online-eval score** — a feedback score below its threshold (Answer Relevance, Hallucination, etc.).
5. **Regressions** — a signal that worsened versus the prior window.

Give each shortlisted item the one signal that flagged it and a short why. Prefer a short, ranked list over a long one.

### 4. Surface Diagnostics issues
Opik's Diagnostics groups recurring problems into issues. Read them via the SDK REST client (no MCP required):

```python
# Needs the project_id (a uuid), not the name — read it off any trace from
# search_traces (trace.project_id), or resolve it from the project name first.
issues = client.rest_client.agent_insights.find_agent_insights_issues(project_id=project_id)
```

When the hosted MCP is connected, the `issue` entity is an equivalent path — a convenience, not a requirement. Merge issues into the shortlist, deduped against the traces you already ranked.

### 5. Stay in scope
Online/production **trace** signal only. Do **not** surface offline experiment results — those are the output of `/opik-evaluate` and `/opik-compare`, not rediscovered here.

### 6. Report
Return the ranked shortlist and one next step. Give each item as a **clickable Opik UI link** (the trace redirect URL Opik emits, e.g. `.../session/redirect/...?trace_id=THE_ID`), never a bare id, so the user can open it and deep-dive. Each item is ready for `/opik-explain`; the natural next step is "explain the top trace" (see **Output**). This skill surfaces and hands off; it does not root-cause (that is `/opik-explain`) and it changes no code.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-diagnose`."
- "Which project should I scan? Pass `/opik-diagnose <project>` or set it in the Opik config."
- "This environment can't reach Opik — open the project's traces view, sort by errors/duration, or run where Opik is configured."

## Output

**User-facing:** a short human message — the ranked shortlist (a clickable Opik UI link per trace + its signal + one-line why, worst first), then the single next step. Not a raw dump of every trace, not JSON.

**Underneath** (for composition / evals), one shape:
- `status`: `found` | `empty` | `blocked`
- `scope`: `project`, `window`
- `shortlist`: list of `{trace_id, trace_url (the Opik UI link), signal (error|tool_call|latency|low_score|regression|diagnostics), why, rank}`
- `source`: `sdk` | `mcp`
- `next_step`: exactly one (typically "explain the top trace")

Invariants: `found` carries a non-empty `shortlist`, each item with a `signal`, a `trace_id`, and a clickable `trace_url`; `empty` = the read succeeded but nothing crossed a threshold; `blocked` carries exactly one `next_step`; the shortlist never contains offline experiment results; every path leaves the codebase unchanged.

## Examples

**Triage a project.** `/opik-diagnose`. `search_traces` on the project; two traces errored, one is 5x the p90 duration, one scored 0.2 on Hallucination. Shortlist = the two errors (rank 1-2), the latency outlier (3), the low-score trace (4), each with its signal; next step = "explain the top trace". → **`found`**.

**Nothing wrong.** `/opik-diagnose`. Reads fine, but no trace errored, ran slow, or scored low. → **`empty`**: "No traces crossed a threshold in the recent window."

**Blocked — no config.** `/opik-diagnose`. No `~/.opik.config`, no `OPIK_API_KEY`. → **`blocked`**: "run `opik configure`, then rerun `/opik-diagnose`." (No code touched.)

## Anti-patterns
Dumping every trace instead of a ranked shortlist; surfacing offline experiment/`evaluate` results (out of scope); requiring the MCP (the SDK `agent_insights` path needs none); root-causing a trace here (hand it to `/opik-explain`); **editing code** (this skill only surfaces); ranking by recency instead of signal.

## References

SDK and observability detail live in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/references/production.md` (`search_traces`, Diagnostics, online-eval scores, error/latency analysis), `../opik/references/tracing-python.md` (SDK read APIs), `../opik/references/observability.md` (span/score model). If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
