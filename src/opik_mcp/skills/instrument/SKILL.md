---
name: instrument
description: Add Opik tracing to an existing app and verify a real trace lands. Installs the Opik package, detects the language and LLM framework, adds the minimum tracing, runs a safe representative path, confirms a trace in Opik, and returns the trace link. Use for "instrument my code", "add opik tracing", "add observability", "trace my agent". Not for building a new app from scratch, or a review-only pass with no code changes.
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project. Install the `opik` skill alongside this one — it holds the shared SDK and integration references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-08-05"
  source_commit: "2.0.0"
  argument-hint: "[optional: file or directory path]"
---

# Instrument — Add Opik Tracing and Verify a Real Trace

**Definition of done:** a representative, safely-executed path produces a trace that is confirmed in Opik and a direct trace link is returned. If verification can't be completed safely or autonomously, stop at the **first** genuine blocker and return **exactly one** concrete next step. Code edits alone are not success.

Operate: **opinionated in execution, conservative in code changes, automatic in routine decisions, uncompromising about verifying value — but never by running something unsafe.**

## Inputs

The entry point is just `/instrument` (optionally `/instrument <path>`). Infer everything else; treat these only as **optional overrides** the user may pass, never as required setup:

- target path (default: project root) · project name (default: inferred from the repo) · run command (default: an inferred safe path) · `migrate_prompts` (default: **false**).

Never turn inference into a questionnaire. Ask only when you hit a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Configure Opik (one source of truth)
- If `~/.opik.config` exists or `OPIK_API_KEY` is set, use it as-is.
- Otherwise run the official flow: `opik configure` (Python) / `npx opik-ts configure` (TypeScript).
- **Verify the config before instrumenting:** run `opik healthcheck` — it validates the config, the install, and backend/workspace reachability. If it fails, stop at that Blocker and fix config or connectivity before adding any tracing.
- Only add project-local `.env` vars if the project **already** uses that pattern. Never introduce a second config mechanism; never copy secret values between mechanisms.

### 2. Detect language & framework
Python (`*.py`, `pyproject.toml`) or TypeScript (`*.ts`, `package.json`). Identify the LLM framework from imports and pick its integration:

| Import | Integration |
|---|---|
| `openai` / `anthropic` | `track_openai` / `track_anthropic` |
| `langchain` / `langgraph` | `OpikTracer` callback |
| `crewai` / `dspy` / google-genai / bedrock / `llama_index` / `litellm` | `track_crewai` / `OpikCallback` / `track_genai` / `track_bedrock` / `LlamaIndexCallbackHandler` / `OpikLogger` |
| TS: `opik-openai` / `opik-vercel` / `opik-langchain` | `trackOpenAI` / `OpikExporter` / `OpikCallbackHandler` |

Full list: read `../opik/references/integrations.md` (the `opik` skill, installed beside this one). If that file isn't there, read <https://www.comet.com/docs/opik/integrations/overview> — never settle for manual spans on a framework that has a native integration just because the reference was unreachable. If the project is **already instrumented**, audit and add only what's missing — do not re-instrument.

### 3. Add the minimum tracing
Decision policy, in order:
1. Prefer the **framework-native integration** for provider LLM spans.
2. Add manual `@opik.track` spans only for orchestration/tools the integration doesn't cover (`type="tool"` / `"llm"` / `"guardrail"`). A bare `@opik.track` produces the default span type, **`general`** — the right choice for an entrypoint/orchestrator.
3. Never instrument the same operation twice (no `@opik.track(type="llm")` on top of `track_openai`).
4. Mark **one entrypoint per independently-runnable agent/service** — not necessarily one per repo.
5. Decorator order relative to framework decorators (e.g. `@app.route`) is **framework-dependent** — verify per framework; do not assume a universal order.
6. Scripts: flush at the end (`opik.flush_tracker()` / `await client.flush()`). LiteLLM inside `@opik.track`: pass `metadata={"opik": {"current_span_data": get_current_span_data()}}` or traces orphan.

Make the **smallest change** that lets one representative path emit a trace.

### 4. Install the Opik package (by default)
Add **only** the required Opik package(s) via the repo's detected package manager (pip / uv / poetry / npm / pnpm / yarn), through normal project conventions. **Preserve the lockfile**; do not run generic upgrades; do not install globally; treat unusual lifecycle scripts cautiously. Surface it as a change (e.g. "added `opik` to `pyproject.toml`"). If the environment blocks installation → **Blocker** with the one exact command.

### 5. Run a safe representative path
Infer a safe command — prefer an existing **test, example, or dev script**, then a bounded single-request entrypoint. **Never** run anything that looks like production or does irreversible/expensive work (writes, emails, purchases, mass API calls). If no safe path is inferable → **Blocker** ("which dev command safely exercises this agent?"). Print the command, then run it.

If the run needs an **LLM provider credential** (e.g. `OPENAI_API_KEY`) and it's absent, that's a Blocker — the app can't produce a trace. Note some SDK clients raise at **construction** (module load), before any span runs, so there is **no partial trace** to wait on: return blocked with the one next step, don't wait on a flush that never happened.

### 6. Verify ingestion
Confirm a trace actually arrived — don't assume. **Prefer the SDK for verification**: it's already installed as part of instrumenting (zero extra moving parts), whereas the Opik MCP is optional and may not be connected.

```python
import opik
client = opik.Opik()

tid = "<trace_id>"                          # the tracer logs a trace URL/id when the run flushes — use that
detail = client.get_trace_content(tid)      # TracePublic: exposes project_id, NOT project_name (accessing .project_name raises)
spans = client.search_spans(trace_id=tid)   # spans come from a SEPARATE call, not from the trace object
# Reconstruct the tree via each span's parent_span_id (the root span has none); check the expected types (general -> tool/llm).
```

To find the newest trace instead of using a known id, use the client's trace search (e.g. `search_traces`) scoped to the project. Optionally, if the Opik MCP is connected, `list` recent traces then `read` the newest.

Traces are asynchronous — allow a few seconds after the run and make sure the flush ran.

### 7. Report
Return a short human result + the trace link (see **Output**), then make the single expansion offer.

## Blockers

When you genuinely can't proceed, stop at the **earliest** blocker and return **exactly one** next step — never a checklist — and still report the changes already made. Examples:
- "Run `opik configure`, then rerun `/instrument`."
- "Install dependencies with `uv sync`, then rerun `/instrument`."
- "Which dev command safely exercises this agent?"
- "Instrumented and installed, but the run needs a provider credential — set `OPENAI_API_KEY` (or the relevant provider key) and rerun."
- "Instrumented and ran, but this environment can't query Opik — open the project and confirm trace `<id>` arrived."

## Expansion — after the trace lands (one offer, not a funnel)

Do **not** migrate prompts, add threading, or broaden spans during activation. After verification, make a **single consolidated offer** of what you found, e.g.:

> Tracing is verified. I also found ways to deepen it: 3 Prompt Library candidates, missing conversation threading, and 2 untraced tools. Expand?

## Output

**User-facing:** a short human message — what was instrumented, the trace link, and the one expansion offer (or, if blocked, the single next step plus what changed). Not raw JSON.

**Underneath** (for composition / evals), a small state model:
- `status`: `verified` | `blocked` | `already_verified` | `unsupported`
- `changes`: `files_changed`, `dependency_added`, `config_source`, `entrypoints_instrumented`, `integrations_added`
- `verification`: `command_run`, `trace_id`, `trace_url`
- `blocker`: `reason`, `next_step`
- `expansion_opportunities`: `prompts`, `threads`, `spans`

Invariants: `verified` must carry a `trace_id`/`trace_url`; `blocked` must carry exactly one `next_step` **and** still report `changes`; `already_verified` = existing instrumentation exercised and confirmed; `unsupported` explains the unsupported language/shape and **modifies nothing**.

## Examples

**Normal — no LLM framework.** A Python script with a `retrieve()` tool and a local `generate()`. No provider to wrap → add `@opik.track(type="tool")` on `retrieve`, bare `@opik.track` (→ `general`) on the entrypoint, flush in `__main__`; `uv add opik`; run it; confirm the `general → tool → llm` trace via the SDK; return the link. → **`verified`**.

**Framework — OpenAI.** `from openai import OpenAI`. Use the native integration: `client = track_openai(OpenAI())`; **leave the LLM-calling function undecorated** (the integration traces it); mark the entrypoint; install `opik`; run. If `OPENAI_API_KEY` is missing, `OpenAI()` raises at construction → **`blocked`**: "set `OPENAI_API_KEY`, then rerun" (report the edits already made).

**Already instrumented.** `@opik.track` / `track_openai` already present. Audit only — add a missing entrypoint or flush, do **not** re-instrument; run + verify. → **`already_verified`**.

## Anti-patterns
Double-wrapping (integration + manual span on the same call); orphaned LiteLLM traces (missing `current_span_data`); missing flush in scripts; overwriting or duplicating config; **running an unsafe/production path just to force a trace**; broad dependency upgrades when only `opik` is needed; migrating prompts during activation.

## References

SDK detail lives in the `opik` skill, installed beside this one. Read the files directly — paths are relative to this file: `../opik/references/tracing-python.md`, `../opik/references/tracing-typescript.md`, `../opik/references/integrations.md`, `../opik/references/observability.md`. If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
