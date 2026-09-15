---
name: opik-optimize
description: Improve a prompt with the Opik Agent Optimizer — resolve the prompt, a dataset, and a metric, pick the algorithm, run a bounded optimization, check the gain on held-out data, and save the winner as a new prompt version with the optimization run link. Runs via the opik-optimizer package; reads prompts and datasets via the MCP when connected. Use for "optimize this prompt", "improve my system prompt", "make the agent answer better", "tune the prompt against my dataset", "run the prompt optimizer". Not for measuring quality once (use evaluate), before/after on a suite (use compare), or hand-editing a prompt without data.
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires a Python project with Opik configured, a provider API key, and a dataset (or traces to build one). Install the `opik` skill alongside this one — it holds the shared dataset and prompt-library references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
metadata:
  last_updated: "2026-09-15"
  source_commit: "2.0.0"
  argument-hint: "[prompt name or file, optional dataset and metric]"
---

# Optimize — Improve a Prompt Against Data

**Definition of done:** an **optimized prompt** whose score on the metric **beats the baseline on data it was not tuned on**, the **optimization run link** in Opik, the cost of getting there, and the winner **saved as a new prompt version** (when the prompt lives in the library) — with the swap into code left as the next step. If the optimization can't run within a stated budget, stop at the **first** genuine blocker and return **exactly one** next step. A prompt that scores higher only on its own training items is not an improvement.

Operate: **measure the baseline first, state the budget before spending it, hold data out, pick the algorithm for the failure you see, save the winner where it can be versioned — and change no application code.** The only file this skill writes is a runner outside the repo; the prompt is saved to Opik, not into the codebase.

## Inputs

The entry point is `/opik-optimize <prompt-name>` (a prompt-library prompt), `/opik-optimize <path or function>` (a prompt in code), or `/opik-optimize` (find the system prompt in this repo). Infer the rest; treat these as **optional overrides**:

- dataset (default: existing dataset for the project → export the regression suite → build from traces) · metric (default: heuristic on `expected_output` if present, else one binary judge) · algorithm (default: `MetaPromptOptimizer`) · budget (default: `n_samples=50`, `max_trials=10`) · model · validation split (default: hold out 20%).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve the prompt
- **Library:** `client.get_chat_prompt(name)` (or `get_prompt` for a text prompt). Note the current `version` — that is the baseline.
- **Code:** grep for the system prompt / `messages=[...]`; read it verbatim. Note where it lives; you will not edit it.
- **Trace:** the `llm` span's `input.messages` on a representative trace.

The optimizer's `opik_optimizer.ChatPrompt` is a **different class** from the library's `opik.ChatPrompt` — build it from the raw messages yourself:
```python
from opik_optimizer import ChatPrompt
prompt = ChatPrompt(name="<name>", system="<system text>", user="{question}")   # or messages=[...]; {var} names must match dataset keys
```

### 2. Resolve the dataset (and hold some out)
The optimizer needs an `opik.Dataset` whose item keys match the prompt's `{variables}`.
```python
import opik
client = opik.Opik()
dataset = client.get_dataset(name="<dataset>", project_name="<project>")
```
- Only a **test suite** exists → export its items into a dataset once: `suite.get_items()` → `client.get_or_create_dataset("<suite>-optimize", project_name=…)` → `insert([{**it["data"]} …])`.
- Nothing exists → build from traces (`search_traces` → `{"question": t.input[...], "expected_output": …}`) or run `/opik-evaluate` first.
- **Hold out:** split into train and validation datasets and pass `validation_dataset=`. Note what it does: the optimizer **scores every trial on `validation_dataset`** and uses the train set to show the reasoning model examples — so the validation set is the selection set, and it needs ≥10 items or every candidate ties (a 4-item split logs `n_samples … larger than evaluation dataset size` and cannot separate prompts). If you need a gain measured on items the optimizer never saw, keep a third split and re-score the winner on it with `evaluate()`. Fewer than ~20 items in total → say the result will be noisy; below 10 → **Blocker**.

### 3. Define the metric
A function `(dataset_item, llm_output) -> float`, higher is better. **Give it a real name** (`def refund_answer_similarity(...)`) — its `__name__` becomes the Optimization run's objective name in the UI and `result.metric_name`; a function called `metric` shows up as "metric".
- `expected_output` present → heuristic (`LevenshteinRatio`, `Equals`, or a task-specific check) — deterministic and free.
- Otherwise → **one** binary judge for the failure mode being optimized (`../opik-evaluate/references/write-judge-prompt.md`), wrapped to return its score `.value`. Multi-objective → `MultiMetricObjective`.
Never optimize against a judge nobody validated: an unvalidated judge is the easiest thing to overfit.

### 4. Pick the algorithm
| Failure you see | Optimizer |
|---|---|
| Instructions unclear / underspecified (general default) | `MetaPromptOptimizer` |
| The model needs examples of the right answer; few-shot is acceptable | `FewShotBayesianOptimizer` |
| Failures cluster into a few root causes | `HierarchicalReflectiveOptimizer` (`HRPO`) |
| Larger budget, want broad search | `EvolutionaryOptimizer` or `GepaOptimizer` |
| The prompt is fine, temperature/top_p are not | `ParameterOptimizer.optimize_parameter(...)` |
| Tool descriptions are the problem | `optimize_prompt(..., optimize_tools=True)` (`optimize_mcp` is deprecated) |

### 5. State the budget, then run
`uv add opik-optimizer` (or `pip install opik-optimizer`) in a scratch environment, not the repo's lockfile unless the user wants it. Each trial evaluates `n_samples` items with the task model plus the reasoning model — tell the user the rough call count before running. Provider key absent → **Blocker**.
```python
from opik_optimizer import MetaPromptOptimizer

optimizer = MetaPromptOptimizer(model="<task model>", verbose=1, seed=42)
result = optimizer.optimize_prompt(
    prompt=prompt, dataset=train, metric=metric,
    validation_dataset=validation,
    n_samples=50, max_trials=10,
    project_name="<project>",
)
```
Write the runner as a temp file outside the repo. The run appears in Opik as an Optimization (`result.get_run_link()`).

### 6. Read the result honestly
`result.initial_score` → `result.score` on the metric; `result.details["stop_reason"]` and `["trials_completed"]`; `result.llm_calls`, `result.llm_cost_total` (may be `None` when the provider returns no cost — say "cost unavailable", don't invent one). **Report the validation score**, not the training score. A gain within run-to-run noise (rerun the baseline once if in doubt) is "no measurable improvement" — say so rather than shipping a lateral move, and do **not** save a new version for it. The common cause of a flat result: the answers depend on context the prompt can't contain (retrieval, tools, account data) — then the prompt isn't the bottleneck and the next step is `/opik-explain` on the worst items, not more trials.

### 7. Save the winner (library prompts) and hand off
```python
messages = result.prompt.get_messages()          # optimizer ChatPrompt -> raw messages
new_version = client.create_chat_prompt(
    name="<name>", messages=messages, project_name="<project>",
    change_description=f"opik-optimize: {result.optimizer}, {result.metric_name} {result.initial_score:.2f} -> {result.score:.2f} (validation)",
)
```
For a prompt that lives in code, do **not** edit the file — return the optimized text and the diff as the next step. Then one next step (see **Output**): typically "`/opik-compare` the new version against the regression suite" or "point the app at version `vN`".

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-optimize`."
- "Which prompt? Name the library prompt or point me at the file/function holding the system prompt."
- "No dataset with matching keys — run `/opik-evaluate` to build one, or name an existing dataset."
- "The optimizer needs a provider credential — set `OPENAI_API_KEY` (or the relevant key) and rerun."
- "Only 6 items — too few to optimize without overfitting. Add cases (or say `synthetic`) and rerun."

## Output

**User-facing:** a short human message — baseline vs optimized score **on validation**, the run link, the cost, the algorithm, what changed in the prompt (one or two lines), the new version (or the diff for a code prompt), and the single next step. Not the full trial history.

**Underneath** (for composition / evals), one shape:
- `status`: `improved` | `no_improvement` | `blocked`
- `prompt`: `name`, `source` (`library` | `code` | `trace`), `baseline_version`, `new_version` (when saved)
- `dataset`: `train` `{name, count}`, `validation` `{name, count}`
- `metric`: `name`, `kind` (`heuristic` | `judge`), `validated: true|false`
- `optimizer`: `algorithm`, `n_samples`, `max_trials`, `stop_reason`
- `scores`: `initial`, `optimized`, `validation`, `delta`
- `cost`: `llm_calls`, `llm_cost_total`
- `run_url`
- `next_step`: exactly one

Invariants: `improved` requires `scores.validation > scores.initial` beyond noise and carries a `run_url`; `no_improvement` still carries the `run_url` and the cost; a `judge` metric with `validated: false` is flagged in the report; the codebase is never modified; a library prompt is saved as a **new version**, never overwritten in place.

## Examples

**Library prompt, heuristic metric.** `/opik-optimize support-system-prompt`. Dataset `support-qa` (120 items with `expected_output`), 96/24 split, `LevenshteinRatio`. `MetaPromptOptimizer`, 50 samples × 10 trials, budget stated. Validation 0.62 → 0.79; cost $1.40. Saved as `v7`. → **`improved`**; next step = "`/opik-compare support-bot-regressions` with the app pointed at `v7`".

**No real gain.** Validation 0.71 → 0.73, baseline rerun varies ±0.03. → **`no_improvement`**: "within noise — the prompt isn't the bottleneck; `/opik-explain` the worst items."

**Prompt in code.** System prompt found in `agent.py`. Optimized text returned with a diff; file untouched. → **`improved`**; next step = "apply the diff (or move the prompt to the library so it can be versioned)".

**Blocked.** No dataset and no traces. → **`blocked`**: "run `/opik-evaluate` to build a dataset first."

## Anti-patterns
Reporting the training-set score as the gain; optimizing against an unvalidated judge; spending an unbounded budget (no `n_samples`/`max_trials`) or not stating it; overwriting the prompt in place instead of a new version; **editing the prompt in the codebase**; treating `opik.ChatPrompt` and `opik_optimizer.ChatPrompt` as interchangeable; optimizing on fewer than ~20 items and calling it a result; choosing the algorithm by novelty rather than by the failure observed; using deprecated `optimize_mcp`.

## References

Dataset and prompt-library detail live in the `opik` skill, installed beside this one — paths relative to this file: `../opik/references/evaluation-datasets.md` (datasets, `insert`, versions, metrics), `../opik/references/best-practices.md` (prompt library, versioning), `../opik/references/tracing-python.md` (SDK client). Judge design and validation: `../opik-evaluate/references/write-judge-prompt.md`, `../opik-evaluate/references/validate-evaluator.md`. If your host lays skills out differently, locate the `opik` skill's `references/` directory.

Optimizer API (`opik-optimizer`): <https://www.comet.com/docs/opik/agent_optimization/overview>. If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
