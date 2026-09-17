---
name: opik-test
description: Turn a failing Opik trace (or a described failure) into a repeatable regression check — a test-suite item with the trace's input and one or two binary assertions — so a fix can be verified by the compare skill. Works over the SDK; uses the MCP write tool when connected. Returns the suite, the item, and the assertion. Use for "turn this into a test", "add a regression case for this trace", "make sure this doesn't happen again", "capture this failure", "add this to the test suite". Not for running the suite (use compare), building an evaluation from scratch (use evaluate), or explaining why the trace failed (use explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project with Opik configured and at least one trace, or a described input/expected pair. Install the `opik` skill alongside this one — it holds the shared test-suite and dataset references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-09-15"
  source_commit: "2.0.0"
  argument-hint: "[trace id, or a description of the failure to capture]"
---

# Test — Capture a Failing Case as a Repeatable Check

**Definition of done:** one test-suite item exists in Opik that reproduces the failure — its `data` carries the failing input (and the expected output when one is known), and it has **one or two binary assertions** that would have failed on the bad trace and pass on a correct one. The item is in a named suite scoped to the project, is confirmed by reading it back, and is ready for `/opik-compare` to run. If the case can't be captured, stop at the **first** genuine blocker and return **exactly one** next step. Writing a pytest file, or describing a test in prose, is not success.

Operate: **extract the case from real trace data, write assertions a judge can decide with a yes or no, store them where `/opik-compare` will find them — and change no application code.** This skill writes to Opik, never to the repo.

## Inputs

The entry point is `/opik-test <trace-id>` (capture that trace), `/opik-test <describe the failure>` (find the trace, or capture from the description alone), or `/opik-test` right after `/opik-explain` (capture the trace it just explained). Infer the rest; treat these as **optional overrides**:

- suite name (default: `<project>-regressions`) · project (default: the trace's project, else the configured one) · expected output (default: none — assertions carry the expectation) · execution policy (default: the suite's; `runs_per_item: 3, pass_threshold: 2` only for an intermittent failure).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve the case
- A **trace id** (uuid-shaped): that trace is the case.
- A **description** ("the refund answer is wrong", "it hallucinated the shipping time"): `search_traces` on the project for the matching trace (error, low score, or free-text match) and take the best one. If none matches, capture from the description alone — the user's input and expectation become the item.
- Confirm Opik is reachable: if `~/.opik.config` exists or `OPIK_API_KEY` is set, use it. Otherwise → **Blocker** ("run `opik configure`, then rerun").

### 2. Read the trace (SDK-first)
```python
import opik
client = opik.Opik()

tid = "<trace_id>"
trace = client.get_trace_content(tid)       # TracePublic: .input, .output, .project_id — NOT .project_name (accessing it raises)
project = client.rest_client.projects.get_project_by_id(trace.project_id).name
spans = client.search_spans(project_name=project, trace_id=tid)   # root span (no parent_span_id) names the entrypoint the compare skill will call
# Pass project_name: without it search_spans looks in the configured default project and returns nothing.
```
Take the **input** exactly as the trace recorded it (the root span / trace `input`), the **actual output** (what went wrong), and the **root span name** (the entrypoint). When the MCP is connected, `read('trace', id)` is an equivalent path — a convenience, not a requirement.

### 3. Write the assertions
Assertions are plain-English statements an LLM judge answers **pass/fail** from the item's `data` and the candidate output. Rules:
1. **One failure mode per assertion.** State what a correct output does, not a list of qualities.
2. **Would have failed on the bad trace.** Check it against the actual output you just read; if it would pass, it's the wrong assertion.
3. **Decidable from the output alone** (plus `expected_output` when present). No "is helpful", no Likert scales.
4. **At most two**: the positive expectation, and — only if the bad output did something specific and wrong — one negative ("does not claim …"). Prefer the positive form: small judge models misread negatives (observed: "does not promise a refund within 24 hours" judged *true* on an output that promised exactly that). If the negative matters, fold it into the positive ("states 5-7 business days, not 24 hours").

Prefer a deterministic check over a judge when the correct answer is exact: put it in `data.expected_output` as well, so `/opik-compare` can score it with a heuristic metric.

### 4. Store the case (create or append)
Suite naming: `<project>-regressions` unless the user names one. Reuse an existing suite; never create a second suite for the same project.

```python
suite = client.get_or_create_test_suite(
    name="<project>-regressions",
    project_name=project,
    tags=["regression"],
)

# Dedupe on the source trace before inserting.
existing = suite.get_items(filter_string=f'data.source_trace_id = "{tid}"')
if not existing:
    suite.insert([{
        "data": {
            "input": trace.input,                 # verbatim from the trace
            "source_trace_id": tid,
            # "expected_output": "...",           # only when the correct answer is exact
        },
        "assertions": ["<positive assertion>", "<optional negative assertion>"],
        "description": "Regression from trace <tid>: <one-line failure>. Entrypoint: <root span name>",
    }])
```

The `description` carries the **entrypoint** (root span name) because `/opik-compare` needs to know which function to call to run the item; the `source_trace_id` key is what keeps a second `/opik-test` on the same trace from duplicating it.

When the hosted MCP is connected, the `write` tool's `test_suite.create` and `test_suite_item.upsert` operations do the same thing — call `schema("test_suite_item.upsert")` for the exact envelope. Either transport ends in the same suite; the SDK path needs no MCP.

### 5. Verify
Read the item back — `suite.get_items(filter_string=…)` returns it with an `id` — and note the suite's current version (`suite.get_current_version_name()`). Don't report success from the insert call alone.

### 6. Report
Return the suite, the item, the assertions, and one next step (see **Output**). The natural next step is "`/opik-compare <suite>` once the fix is in". This skill captures; it does not run the suite (that is `/opik-compare`) and it changes no code.

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-test <trace-id>`."
- "No trace found for `<id>` in project `<name>` — confirm the id and project, or describe the failure and I'll capture it from that."
- "The trace has no recorded input — pass the input and expected behavior and I'll capture it from those."
- "Which behavior is wrong here? Tell me what the output should have done and I'll write the assertion."

## Output

**User-facing:** a short human message — the suite name and version, the item (input in one line, assertions verbatim), whether it was **new or already present**, and the single next step. Not JSON, not a pytest file.

**Underneath** (for composition / evals), one shape:
- `status`: `captured` | `exists` | `blocked`
- `suite`: `name`, `project`, `version`
- `item`: `id`, `source_trace_id`, `input` (as stored), `assertions` (list), `expected_output` (when set), `entrypoint`
- `source`: `sdk` | `mcp`
- `next_step`: exactly one (typically "run `/opik-compare <suite>` after the fix")

Invariants: `captured` and `exists` carry an `item` with a non-empty `assertions` list and a `suite`; each assertion names one failure mode; `exists` means the dedupe found the trace already captured and nothing was inserted; `blocked` carries exactly one `next_step`; every path leaves the codebase unchanged.

## Examples

**From an explained trace.** `/opik-explain` found the `retrieve` tool returned nothing and the answer claimed a 24-hour refund. `/opik-test 019fd8a7-…`: input = "what is your refund window?", root span = `answer`. Assertions: "Response states refunds take 5-7 business days" and "Response does not promise a refund within 24 hours". Suite `support-bot-regressions` created, item inserted, read back as v1. → **`captured`**; next step = "run `/opik-compare support-bot-regressions` once `retrieve()` is fixed".

**Already captured.** Same trace again. The `source_trace_id` filter finds the item. → **`exists`**: "Already in `support-bot-regressions` (item `…`) — run `/opik-compare` when ready."

**From a description, no trace.** `/opik-test the bot should refuse to give legal advice`. No matching trace, so the user's input and expectation become the item: input = the prompt they describe, assertion = "Response declines to give legal advice and points to a professional". → **`captured`** (with `source_trace_id` absent).

**Blocked — no config.** No `~/.opik.config`, no `OPIK_API_KEY`. → **`blocked`**: "run `opik configure`, then rerun." (No code touched.)

## Anti-patterns
Writing a pytest/vitest file instead of a suite item (the suite is what `/opik-compare` runs and what the UI shows); holistic assertions ("response is good", "response is accurate and helpful"); an assertion that would have **passed** on the failing trace; five assertions for one failure; a fresh suite per trace; paraphrasing the input instead of storing it verbatim; running the suite here (that is `/opik-compare`); **editing application code**; fixing the bug as a side effect.

## References

Test-suite and dataset detail live in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/references/evaluation-test-suites.md` (suites, items, assertions, execution policies, versions), `../opik/references/evaluation-datasets.md` (OQL filter syntax, datasets from traces), `../opik/references/production.md` (`search_traces`). For how to phrase a judge-decidable assertion, `../opik-evaluate/references/write-judge-prompt.md`. If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
