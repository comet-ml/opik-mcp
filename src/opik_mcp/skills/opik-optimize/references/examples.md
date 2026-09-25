---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# Examples

**Library prompt, heuristic metric.** `/opik-optimize support-system-prompt`. Dataset `support-qa` (120 items with `expected_output`), 96/24 split, `LevenshteinRatio`. `MetaPromptOptimizer`, 50 samples × 10 trials, budget stated. Validation 0.62 → 0.79; cost $1.40. Saved as `v7`. → **`improved`**; next step = "`/opik-compare support-bot-regressions` with the app pointed at `v7`".

**No real gain.** Validation 0.71 → 0.73, baseline rerun varies ±0.03. → **`no_improvement`**: "within noise — the prompt isn't the bottleneck; `/opik-explain` the worst items."

**Prompt in code.** System prompt found in `agent.py`. Optimized text returned with a diff; file untouched. → **`improved`**; next step = "apply the diff (or move the prompt to the library so it can be versioned)".

**Blocked.** No dataset and no traces. → **`blocked`**: "run `/opik-evaluate` to build a dataset first."
