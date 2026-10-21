---
last_updated: "2026-09-10"
source_commit: "2.0.0"
---

# Examples

**Normal — no LLM framework.** A Python script with a `retrieve()` tool and a local `generate()`. No provider to wrap → add `@opik.track(type="tool")` on `retrieve`, bare `@opik.track` (→ `general`) on the entrypoint, flush in `__main__`; `uv add opik`; run it; confirm the `general → tool → llm` trace via the SDK; return the link. → **`verified`**.

**Framework — OpenAI.** `from openai import OpenAI`. Use the native integration: `client = track_openai(OpenAI())`; **leave the LLM-calling function undecorated** (the integration traces it); mark the entrypoint; install `opik`; run. If `OPENAI_API_KEY` is missing, `OpenAI()` raises at construction → **`blocked`**: "set `OPENAI_API_KEY`, then rerun" (report the edits already made).

**Already instrumented.** `@opik.track` / `track_openai` already present. Audit only — add a missing entrypoint or flush, do **not** re-instrument; run + verify. → **`already_verified`**.
