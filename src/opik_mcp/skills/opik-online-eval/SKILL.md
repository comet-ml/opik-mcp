---
name: opik-online-eval
description: Take a judge live on production traffic — create an Opik online evaluation rule (LLM-as-judge or Python metric) on a project with sampling, filters, variable mapping, and a cost cap, then confirm new traces are being scored. Works over the SDK's REST client; reads rules and score names via the MCP when connected. Returns the rule, the score name it emits, and how to watch it. Use for "score production traces", "monitor hallucinations in prod", "take this judge live", "set up an online evaluation rule", "alert me when quality drops". Not for offline experiments (use evaluate or compare) or for finding what is already broken (use diagnose).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires Opik configured and a project that receives traces. Install the `opik` skill alongside this one — it holds the shared production and observability references; without it, this skill falls back to the public docs.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-09-15"
  source_commit: "2.0.0"
  argument-hint: "[what to score, or a judge from the evaluate skill; optional project]"
---

# Online Eval — Take a Judge Live

**Definition of done:** an **online evaluation rule exists on the project, is enabled, and has scored at least one new trace** — confirmed by reading a fresh trace's feedback score, not by the create call returning. The rule scores one failure mode, samples at a rate the project's volume can afford, carries a cost cap, and maps its variables to the fields the traces actually have. If traffic hasn't arrived yet, the done state is "rule live, unverified — watch this filter". If the rule can't be created, stop at the **first** genuine blocker and return **exactly one** next step.

Operate: **one failure mode per rule, sample before you scale, cap the spend, verify on a real trace — and change no application code.** This skill writes to Opik only.

## Inputs

The entry point is `/opik-online-eval <what to score>` ("hallucinations", "the refund-window answer", "refusals"), `/opik-online-eval` right after `/opik-evaluate` validated a judge (take that judge live), or `/opik-online-eval <project>`. Infer the rest; treat these as **optional overrides**:

- project (default: configured) · scope (default: **trace**; `span` for one model call, `thread` for whole conversations) · sampling rate (default: 1.0 under ~1k traces/day, else 0.1–0.2) · filters (default: none; typical: an `environment` or tag filter) · judge model · cost cap (default: set one) · variable mapping (default: `input → input`, `output → output`).

Ask only at a genuine, non-inferable blocker (see **Blockers**).

## Activation — the only in-scope work

### 1. Resolve project, judge, and scope
Confirm Opik is reachable (`~/.opik.config` or `OPIK_API_KEY`; otherwise → **Blocker**). Resolve the project id from a trace or by name. Decide the judge:
- A judge `/opik-evaluate` already validated → reuse its prompt and output schema verbatim.
- A named metric (hallucination, answer relevance, moderation) → a minimal binary judge for that one failure mode (`../opik-evaluate/references/write-judge-prompt.md`).
- A mechanical check (JSON valid, contains a disclaimer, latency budget) → a **Python metric rule**, not a judge.

Scope: `trace` by default; `span` when the check is about one LLM/tool call; `thread` when the check needs the whole conversation (thread rules wait for the thread to go inactive — 15 min by default).

### 2. Look at the traces before mapping variables
Read three recent **production** traces and note the real shape of `input` and `output`. Experiment runs land in the same project with a different input shape (their metadata carries `test_suite_experiment_id`), so skip those — filtering on the app's entrypoint is the reliable way: `client.search_traces(project_name=…, max_results=3, filter_string='name = "<entrypoint>"')`. (OQL has no `is_empty` for `metadata.*` keys.) Variables are **plain field paths**, dot-notation for nested keys (`output.answer`, `input.messages`) — never `{{ }}` templates. A wrong path is the most common reason a rule silently scores nothing.

### 3. Check what already runs
```python
import opik
client = opik.Opik()
existing = client.rest_client.automation_rule_evaluators.find_evaluators(project_id="<project_id>")
```
Same name or same failure mode already there → don't create a second one; report `exists` (offer to adjust sampling/enable). When the hosted MCP is connected, `list('online_rule', project_id=…)` and `list('score_name', project_id=…)` show the same, with each rule's type, enabled flag, and sampling rate.

### 4. Create the rule
There is **no high-level SDK wrapper**; use the REST client. LLM-as-judge, trace scope:
```python
from opik.rest_api.types import (
    AutomationRuleEvaluatorWrite_LlmAsJudge, LlmAsJudgeCodeWrite,
    LlmAsJudgeModelParametersWrite, LlmAsJudgeMessageWrite, LlmAsJudgeOutputSchemaWrite,
)

rule = AutomationRuleEvaluatorWrite_LlmAsJudge(
    action="evaluator",                     # required literal; the model rejects the payload without it
    name="refund_window_correct",           # becomes the feedback-score name on every scored trace — use underscores, not hyphens: OQL parses `feedback_scores.a-b` as an operator
    project_ids=["<project_id>"],
    sampling_rate=0.2,                      # fraction of SDK-logged traces scored
    enabled=True,
    filters=[],                             # e.g. [{"field": "tags", "operator": "contains", "value": "production"}]
    code=LlmAsJudgeCodeWrite(
        model=LlmAsJudgeModelParametersWrite(name="<judge model>", temperature=0.0),
        messages=[LlmAsJudgeMessageWrite(role="USER", content="<the validated judge prompt using {{input}} and {{output}}>")],
        variables={"input": "input", "output": "output"},          # field paths from step 2
        schema_=[LlmAsJudgeOutputSchemaWrite(name="refund_window_correct", type="BOOLEAN",
                                             description="True if the response states 5-7 business days")],
        max_cost_usd=5.0,                   # per-rule spend cap — set it
    ),
)
created = client.rest_client.automation_rule_evaluators.create_automation_rule_evaluator(request=rule)
```
Notes: on Opik Cloud without your own provider key, `model.name="opik-free-model"` uses the workspace's built-in free provider; the Python attribute is `schema_` (wire name `schema`); `sampling_rate` applies to production traces only (experiment traces are always scored in full); `trigger_scope` defaults to `production`. Span and thread variants: `AutomationRuleEvaluatorWrite_SpanLlmAsJudge`, `AutomationRuleEvaluatorWrite_TraceThreadLlmAsJudge`. Python metric: `AutomationRuleEvaluatorWrite_UserDefinedMetricPython` with `code={"metric": "<python source defining a BaseMetric>", "arguments": {"output": "output"}}`.

Endpoint, if scripting outside Python: `POST /v1/private/automations/evaluators/` with the same body.

### 5. Verify on a real trace
```python
import time
for _ in range(12):                                          # ~2 min
    scored = client.search_traces(project_name="<project>", max_results=1,
                                  filter_string='feedback_scores.refund_window_correct is_not_empty')
    if scored: break
    time.sleep(10)
```
If the score name already contains a hyphen (an existing rule), double-quote the key or the OQL parser reads the hyphen as an operator: `filter_string='feedback_scores."refund-window-correct" is_not_empty'`.
A scored trace → **`live`**. None, and the project had no new traces in the window → **`live_unverified`** with the filter to watch. None, but traces did arrive → read the rule's logs (`get_evaluator_logs_by_id(id)`) — a variable-path error or model failure shows there; fix and re-verify.

### 6. Report
Rule name/id, scope, sampling, cost cap, the score name, the verification trace link, and one next step (see **Output**). Natural next steps: `/opik-diagnose` will now surface low scores on this name; an alert on the score threshold; or, if the judge wasn't validated first, "validate it against 20 human labels (`/opik-evaluate`) before anyone acts on it".

## Blockers

Stop at the **earliest** blocker and return **exactly one** next step:
- "Run `opik configure`, then rerun `/opik-online-eval`."
- "Which project should this score? Pass `/opik-online-eval <what> <project>`."
- "The traces' `output` is `{answer, sources}` — should the judge read `output.answer`? (I'll map it that way unless you say otherwise.)" — ask only when the mapping is genuinely ambiguous.
- "Which failure mode should the rule catch? Name one (e.g. hallucination, wrong refund window, unsafe content)."

## Output

**User-facing:** a short human message — the rule (name, scope, sampling, cap), the score name, the verification trace as a clickable Opik UI link (or the watch filter if unverified), and the single next step. Not JSON.

**Underneath** (for composition / evals), one shape:
- `status`: `live` | `live_unverified` | `exists` | `blocked`
- `rule`: `id`, `name`, `type` (`llm_as_judge` | `user_defined_metric_python` | span/thread variants), `scope`, `sampling_rate`, `filters`, `max_cost_usd`, `enabled`
- `score_name`
- `variables`: the field-path mapping used
- `verification`: `trace_id`, `trace_url`, `value` (when `live`); `watch_filter` (when unverified)
- `source`: `sdk` | `mcp`
- `next_step`: exactly one

Invariants: `live` carries a `verification.trace_id`; every created rule has a `max_cost_usd` and a `sampling_rate`; one failure mode per rule; `exists` created nothing; `blocked` carries exactly one `next_step`; every path leaves the codebase unchanged.

## Examples

**Validated judge goes live.** `/opik-evaluate` calibrated "states the refund window as 5–7 business days" (TPR 0.95). `/opik-online-eval`: project `support-bot`, ~5k traces/day → sampling 0.1, filter `tags contains "production"`, cap $5, mapping `output → output.answer` (traces nest it). Created; 40 s later a trace carries `refund-window-correct = 1`. → **`live`**; next step = "alert when the 1h mean drops below 0.8".

**Mechanical check.** "Make sure every prod answer is valid JSON." → Python metric rule (`IsJson` on `output`), sampling 1.0 (cheap), no judge. → **`live`**.

**No traffic yet.** Rule created on a project that receives traces only in business hours. → **`live_unverified`**: "watch `feedback_scores.<name> is_not_empty` — or send one traced request".

**Already there.** A rule named `hallucination` exists, enabled, at 0.5. → **`exists`**; next step = "lower sampling to 0.1 if cost is the concern".

## Anti-patterns
A rule at `sampling_rate: 1.0` on a high-volume project without saying what it costs; no `max_cost_usd`; `{{input}}`-style template syntax in **variables** (they are field paths); a holistic "quality" judge as a rule; a judge for a mechanical check; a second rule for the same failure mode; declaring success from the create call without a scored trace; taking an unvalidated judge live and calling its scores truth; **editing application code**.

## References

Production and observability detail live in the `opik` skill, installed beside this one — paths relative to this file: `../opik/references/production.md` (online evaluation, variable mapping, feedback scores, alerts), `../opik/references/observability.md` (trace/span/thread model), `../opik/references/evaluation-datasets.md` (OQL filters, `feedback_scores.<name>` operators). Judge design: `../opik-evaluate/references/write-judge-prompt.md`, `../opik-evaluate/references/validate-evaluator.md`. If your host lays skills out differently, locate the `opik` skill's `references/` directory.

If the `opik` skill isn't installed, say so in the report and use <https://www.comet.com/docs/opik/> rather than working from memory.
