---
name: instrument
description: Add Opik tracing to an existing app and verify a real trace lands. Installs the Opik package, detects the language and LLM framework, adds the minimum tracing, runs a safe representative path, confirms a trace in Opik, and returns the trace link. Use for "instrument my code", "add opik tracing", "add observability", "trace my agent".
last_updated: "2026-08-05"
source_commit: "TODO — pin to the verified Opik release (OPIK-7471)"
argument-hint: "[optional: file or directory path]"
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python or TypeScript project.
allowed-tools:
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - Bash
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
- Only add project-local `.env` vars if the project **already** uses that pattern. Never introduce a second config mechanism; never copy secret values between mechanisms.

### 2. Detect language & framework
Python (`*.py`, `pyproject.toml`) or TypeScript (`*.ts`, `package.json`). Identify the LLM framework from imports and pick its integration:

| Import | Integration |
|---|---|
| `openai` / `anthropic` | `track_openai` / `track_anthropic` |
| `langchain` / `langgraph` | `OpikTracer` callback |
| `crewai` / `dspy` / google-genai / bedrock / `llama_index` / `litellm` | `track_crewai` / `OpikCallback` / `track_genai` / `track_bedrock` / `LlamaIndexCallbackHandler` / `OpikLogger` |
| TS: `opik-openai` / `opik-vercel` / `opik-langchain` | `trackOpenAI` / `OpikExporter` / `OpikCallbackHandler` |

Full list: load the `opik` skill's `references/integrations.md`. If the project is **already instrumented**, audit and add only what's missing — do not re-instrument.

### 3. Add the minimum tracing
Decision policy, in order:
1. Prefer the **framework-native integration** for provider LLM spans.
2. Add manual `@opik.track` spans only for orchestration/tools the integration doesn't cover (`type="tool"` / `"llm"` / `"guardrail"`, else default).
3. Never instrument the same operation twice (no `@opik.track(type="llm")` on top of `track_openai`).
4. Mark **one entrypoint per independently-runnable agent/service** — not necessarily one per repo.
5. Decorator order relative to framework decorators (e.g. `@app.route`) is **framework-dependent** — verify per framework; do not assume a universal order.
6. Scripts: flush at the end (`opik.flush_tracker()` / `await client.flush()`). LiteLLM inside `@opik.track`: pass `metadata={"opik": {"current_span_data": get_current_span_data()}}` or traces orphan.

Make the **smallest change** that lets one representative path emit a trace.

### 4. Install the Opik package (by default)
Add **only** the required Opik package(s) via the repo's detected package manager (pip / uv / poetry / npm / pnpm / yarn), through normal project conventions. **Preserve the lockfile**; do not run generic upgrades; do not install globally; treat unusual lifecycle scripts cautiously. Surface it as a change (e.g. "added `opik` to `pyproject.toml`"). If the environment blocks installation → **Blocker** with the one exact command.

### 5. Run a safe representative path
Infer a safe command — prefer an existing **test, example, or dev script**, then a bounded single-request entrypoint. **Never** run anything that looks like production or does irreversible/expensive work (writes, emails, purchases, mass API calls). If no safe path is inferable → **Blocker** ("which dev command safely exercises this agent?"). Print the command, then run it.

### 6. Verify ingestion
Confirm a trace actually arrived — don't assume: over the MCP, `list` recent traces then `read` the newest and check the span tree; or query recent traces via the SDK. Traces are async — allow a few seconds and make sure the flush ran.

### 7. Report
Return a short human result + the trace link (see **Output**), then make the single expansion offer.

## Blockers

When you genuinely can't proceed, stop at the **earliest** blocker and return **exactly one** next step — never a checklist — and still report the changes already made. Examples:
- "Run `opik configure`, then rerun `/instrument`."
- "Install dependencies with `uv sync`, then rerun `/instrument`."
- "Which dev command safely exercises this agent?"
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

## Anti-patterns
Double-wrapping (integration + manual span on the same call); orphaned LiteLLM traces (missing `current_span_data`); missing flush in scripts; overwriting or duplicating config; **running an unsafe/production path just to force a trace**; broad dependency upgrades when only `opik` is needed; migrating prompts during activation.

## References
For SDK detail, load the `opik` skill: `references/tracing-python.md`, `references/tracing-typescript.md`, `references/integrations.md`, `references/observability.md`.
