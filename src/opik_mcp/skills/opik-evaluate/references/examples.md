---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Examples

**Agent, from traces.** `/opik-evaluate`. 100 traces read: 30% give wrong refund windows, 10% invent policies. Suite `support-bot-eval`, 40 items from traces, assertions "States the refund window as 5–7 business days" and "Does not invent a policy not present in the docs". Runner built, `run_tests` → 27/40. Read back: worst items all hit the refund assertion. → **`evaluated`**; next step = "`/opik-test` isn't needed — the suite exists; fix `retrieve()` and run `/opik-compare support-bot-eval`".

**RAG, dataset path.** `/opik-evaluate the RAG answers`. Dataset with `input`, `expected_output`, `context`; `evaluate()` with `ContextPrecision`, `ContextRecall`, `Hallucination`. Recall 0.61 is the weak stage. → **`evaluated`**; next step = "raise top-k / fix the retriever, then `/opik-compare`".

**Prompt only.** The target is a prompt version in the library. `execute_experiment` with the variant; poll until items fill; read back. → **`evaluated`** with `shape: server_side`.

**Blocked.** No entrypoint is inferable from the repo. → **`blocked`**: "Which function should I evaluate?"
