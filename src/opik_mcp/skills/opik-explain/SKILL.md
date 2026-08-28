---
name: opik-explain
description: Root-cause a specific Opik trace, or a pattern across traces, and return a grounded explanation. Uses the hosted Opik MCP when it is connected, and falls back to SDK scripting otherwise. Returns the root cause, the evidence spans as clickable Opik UI links, and one suggested next step. Use for "why did this trace fail", "explain this trace", "debug this trace", "why is my agent slow or wrong". Not for adding tracing to an app (use the instrument skill) or for changing code.
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured and at least one trace. Install the `opik` skill alongside this one — it holds the shared SDK and observability references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-08-24"
  source_commit: "2.0.0"
  argument-hint: "[trace id, or a description of the behavior to explain]"
---

# Explain — Root-Cause an Opik Trace and Ground It in the Code

**Definition of done:** a grounded root cause for the requested trace (or pattern), tied to **specific evidence spans** and paired with **exactly one** suggested next step. "Grounded" means the explanation names the failing/anomalous span and connects it to the code or data that produced it — not a restatement of the trace. If the target can't be fetched or read, stop at the **first** genuine blocker and return one concrete next step. A trace dump is not an explanation.

Operate: **investigate over the real trace data, reason against the repo, commit to a single most-likely root cause with its evidence — and change no code.** This skill is read-only by design.

## Inputs

The entry point is `/opik-explain <trace-id>` (one trace) or `/opik-explain <describe the behavior>` (a pattern to find and explain). Infer the rest; treat these as **optional overrides**:

- project name (default: inferred from config/repo) · time window for a pattern (default: recent) · a known-good trace to compare against.

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve the target
- A **trace id** (uuid-shaped): explain that one trace.
- A **behavior/pattern** ("hallucinations since the prompt change", "slow responses"): search for the matching set (below), then explain the shared cause.
- Confirm Opik is reachable: if `~/.opik.config` exists or `OPIK_API_KEY` is set, use it. Otherwise → **Blocker** ("run `opik configure`, then rerun").

### 2. Fetch the trace and spans — MCP first, SDK fallback
Check whether the hosted Opik MCP is connected and prefer it; fall back to SDK scripting when it isn't.
- **MCP connected:** use the MCP to `read` the trace and `list`/`read` its spans.
- **No MCP:** fall back to the SDK.

Either way, read every span's input/output/error/duration.

```python
import opik
client = opik.Opik()

tid = "<trace_id>"
trace = client.get_trace_content(tid)       # TracePublic: exposes project_id, input, output, error info — NOT project_name (accessing .project_name raises)
spans = client.search_spans(trace_id=tid)   # spans come from a SEPARATE call, not from the trace object
# Reconstruct the tree via each span's parent_span_id (the root span has none).
# Your anchor is the first span that errored, returned wrong output, or dominates the duration.
```

For a **pattern**, pull the matching set scoped to the project, then look for the shared failing span across them:

```python
traces = client.search_traces(project_name="<project>", filters={...})  # e.g. error traces, low-score traces, high-duration traces
```

Traces are asynchronous; if you just produced the trace, allow a few seconds and confirm the flush ran.

### 3. Root-cause it
The coding agent root-causes over the fetched data, against the repo — it has the one thing a generic reasoner lacks: **the code**. Whether the trace came from the MCP or the SDK, the analysis is the same: find the anchor span (error / wrong output / latency dominator), read its input and output, connect it to the code (grep the repo for the span name / function), and state the **single most-likely root cause** with its evidence spans. Prefer one well-evidenced cause over a list of maybes.

### 4. Report
Return the root cause, the evidence spans **as clickable Opik UI links** (the trace redirect URL Opik emits, e.g. `.../session/redirect/...?trace_id=THE_ID` — never a bare id), and one next step (see **Output**). If a fix is obvious, name it as the next step; do not apply it (this skill changes no code — handing off to `opik-instrument`/`opik-test` or the developer is the next step).

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-explain <trace-id>`."
- "No trace found for `<id>` in project `<name>` — confirm the id and project, then rerun."
- "This environment can't reach Opik — open the trace in the UI and paste its error/output, or run where Opik is configured."
- "Which behavior should I explain? Give a trace id or describe what went wrong."

## Output

**User-facing:** a short human message — the root cause in one or two sentences, the evidence spans as clickable Opik UI links (name + why each matters), and the single next step. Not a raw trace dump, not JSON.

**Underneath** (for composition / evals), one shape whether the MCP or the SDK path produced it:
- `status`: `explained` | `blocked` | `not_found`
- `target`: `trace_id` + `trace_url` (the Opik UI link; for a pattern, the `trace_ids` + `trace_urls` sampled)
- `root_cause`: one grounded statement
- `evidence`: `spans` (each: `name`, `type`, a `trace_url` deep-link where available, why it's evidence)
- `next_step`: exactly one
- `reasoner`: `agent` (the coding agent root-causes over the trace data and the repo)

Invariants: `explained` must carry a `root_cause` **and** at least one evidence span; `blocked`/`not_found` carry exactly one `next_step`; every path leaves the codebase unchanged.

## Examples

**Single trace — tool failure.** `/opik-explain 019fd8a7-...`. Fetch trace + spans; the `retrieve` (`tool`) span returned empty and the `llm` span then hallucinated. Open `retrieve()` in the repo: the query filter is wrong. Root cause = the retrieval filter, evidence = the empty `tool` span feeding the `llm` span; next step = "fix the filter in `retrieve()` (or `/opik-test` it)". → **`explained`**.

**Pattern — slowness.** `/opik-explain why responses got slow this week`. `search_traces` for high-duration traces; the same external `tool` span dominates each. Root cause = that call's latency; evidence = the shared slow span across N traces; next step = "add a timeout/cache around it". → **`explained`**.

**Blocked — bad id.** `/opik-explain 123`. `get_trace_content` finds nothing. → **`not_found`**: "No trace `123` in project `X` — confirm the id/project and rerun." (No code touched.)

## Anti-patterns
Dumping the span tree without naming a cause; guessing a cause without reading the anchor span's input/output; listing five maybes instead of the one best-evidenced cause; returning bare span/trace ids instead of clickable Opik UI links; **editing code** (this skill explains; `opik-instrument`/`opik-test` or the developer make changes); calling `.project_name` on a `TracePublic` (it raises — use `project_id`).

## References

SDK and observability detail live in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/references/production.md` (`search_traces`, error/latency/cost analysis), `../opik/references/tracing-python.md` (SDK read APIs), `../opik/references/observability.md` (span-type model). If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
